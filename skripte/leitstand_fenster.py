"""Bau-Leitstand, Fenster-Teil: Ablage-Pfade und Zustands-/URL-Datei, Seite, Takt-Anweisung, Start-Mail, Schalter ``aktiv`` und Sitzungsstart.

Das Rechnen (Doc-Bildung, seed, vorbereiten, bestaetigen, uebernehmen) und die CLI liegen in ``leitstand.py``;
der importiert von hier und re-exportiert die Namen — dieses Modul importiert nie ``leitstand``.
"""

from __future__ import annotations

import argparse
import contextlib
import inspect
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_SKRIPTE = Path(__file__).resolve().parent
_SKILL = str(_SKRIPTE.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import (
    config,
    melder,
    sitzung_env,
    sitzung_filter,
    sitzungsjob,
    speicher,
    sperre,
    starter,
    terminal_maus,
    waechter_lauf,
)

log = logging.getLogger("leitstand_seite")

EXIT_KEINE_URL = 4
EXIT_AUS = 5
#: Vorgaben für ``leitstand.*`` in ``.to-spawn/config.json`` (``aktiv``, ``modell``, ``takt``).
MODELL = "claude-sonnet-5"
TAKT = "3m"
MAIL_ART = "leitstand_start"
VORLAGE = _SKRIPTE.parent / "leitstand" / "seite.html"
#: Obergrenze automatischer Staffelwechsel an der Handoff-Grenze je Fenster (#950).
MAX_STAFFELN = 50
#: Höchst-Wartezeit (s) auf einen ruhigen Takt-Abschluss nach Erreichen der Handoff-Grenze (#950).
RUHE_WARTE_MAX_S = 60.0


def jetzt_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- Ablage + Zustand


@dataclass
class Ablage:
    repo: Path
    spec: str

    @property
    def ordner(self) -> Path:
        return self.repo / ".to-spawn" / "leitstand"

    @property
    def zustand_datei(self) -> Path:
        return self.ordner / f"zustand-{self.spec}.json"

    @property
    def writes_datei(self) -> Path:
        return self.ordner / f"writes-{self.spec}.json"

    @property
    def offen_datei(self) -> Path:
        return self.ordner / f"offen-{self.spec}.json"

    @property
    def seite_datei(self) -> Path:
        return self.ordner / f"seite-{self.spec}.json"

    @property
    def docs(self) -> Path:
        return self.ordner / "docs" / self.spec

    @property
    def log_datei(self) -> Path:
        return self.ordner / f"leitstand-{self.spec}.log"

    @property
    def sperre_datei(self) -> Path:
        return self.ordner / f"leitstand-{self.spec}.sperre"

    @property
    def sitzung_sperre(self) -> Path:
        return self.ordner / f"sitzung-{self.spec}.sperre"

    @property
    def html_datei(self) -> Path:
        return self.ordner / f"seite-{self.spec}.html"

    @property
    def anweisung_datei(self) -> Path:
        return self.ordner / f"takt-{self.spec}.md"

    @property
    def mail_datei(self) -> Path:
        return self.ordner / f"mail-{self.spec}.json"


def leerer_zustand() -> dict[str, Any]:
    return {
        "gesendet": [],
        "seed_gesendet": [],
        "gestartet": None,
        "start": {},
        "ende": {},
        "live_inhalt": None,
        "live_zeit": None,
    }


def lade_zustand(pfad: Path) -> dict[str, Any]:
    leer = leerer_zustand()
    if not pfad.exists():
        return leer
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as fehler:
        log.error("Zustandsdatei unlesbar (%s) — Abbruch, damit nichts doppelt gesendet wird.", fehler)
        raise
    return {**leer, **daten}


def speichere_json(pfad: Path, daten: Any) -> None:
    """Atomar schreiben (``.neu`` + ``os.replace``)."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    neu = pfad.with_suffix(".neu")
    neu.write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(neu, pfad)


def nimm_sperre(pfad: Path) -> contextlib.ExitStack | None:
    """Betriebssystem-Sperre gegen Doppelstart (:func:`to_spawn.sperre.halte`, #95).

    ``None`` = belegt oder nicht sperrbar; sonst ``.close()`` gibt frei. Fällt beim Prozess-Tod von selbst.
    """
    stapel = contextlib.ExitStack()
    try:
        gehalten = stapel.enter_context(sperre.halte(pfad))
    except OSError:
        gehalten = False
    if not gehalten:
        stapel.close()
        return None
    return stapel


# ---------------------------------------------------------------- url-Datei


def lies_url(ablage: Ablage) -> str | None:
    if not ablage.seite_datei.exists():
        return None
    try:
        daten = json.loads(ablage.seite_datei.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as fehler:
        log.error("seite-%s.json unlesbar (%s) — Abbruch, sonst entsteht eine zweite Seite.", ablage.spec, fehler)
        raise
    url = str(daten.get("url") or "").strip() if isinstance(daten, dict) else ""
    return url or None


def setze_url(ablage: Ablage, url: str) -> None:
    url = url.strip()
    if not re.match(r"^https://claude\.ai/(?:code/)?artifact/[\w-]+$", url):
        raise ValueError(f"Keine claude.ai-Artifact-URL: {url!r}")
    speichere_json(ablage.seite_datei, {"url": url, "spec": ablage.spec, "gesetzt": jetzt_iso()})


# ---------------------------------------------------------------- Fenster-Session (seite, anweisung, mail, sitzung)


def leitstand_konfig(repo: Path) -> dict[str, Any]:
    """``leitstand.*`` aus der Repo-Konfig (fehlt = leer, es gelten die Vorgaben hier)."""
    wert = config.lade(repo).get("leitstand")
    return wert if isinstance(wert, dict) else {}


def ist_aktiv(lk: dict[str, Any]) -> bool:
    return lk.get("aktiv", True) is not False


def python_befehl(plattform: str = sys.platform) -> str:
    """So ruft die Session Python auf — muss exakt zur ``--allowedTools``-Freigabe passen."""
    if plattform == "win32":
        return "python"
    return Path(sys.executable).as_posix() if sys.executable else "python3"


def skript_pfad() -> str:
    """Pfad von ``leitstand.py`` (der CLI), nicht dieses Moduls — so steht er in Anweisung und Freigabe."""
    return (_SKRIPTE / "leitstand.py").resolve().as_posix()


def erlaubte_werkzeuge(python: str, skript: str, ordner: str) -> list[str]:
    return [f"Bash({python} {skript} *)", "Artifact", "ArtifactData", "ToolSearch", f"Read({ordner}/**)"]


def sitzung_befehl(
    modell: str,
    werkzeuge: list[str],
    anweisung: str,
    takt: str = TAKT,
    *,
    mcp_pfad: Path | str | None = None,
    session_id: str | None = None,
) -> list[str]:
    """Leitstand-Session mit schlankem MCP-Profil (nur ``context-mode`` + ``--strict-mcp-config``, #869)."""
    mcp_flags = (
        ["--mcp-config", str(mcp_pfad), sitzung_filter.STRICT_MCP_FLAG]
        if mcp_pfad is not None
        else sitzung_filter.schlanke_mcp_flags(logger=log)
    )
    sid_flags = ["--session-id", session_id] if session_id else []
    return [
        "claude",
        "--model",
        modell,
        *mcp_flags,
        *sid_flags,
        "--allowedTools",
        *werkzeuge,
        "--",
        f"/loop {takt} Lies {anweisung} und führe genau das aus",
    ]


def rendere_seite(vorlage: str, spec: str) -> str:
    """Vorlage mit Titel ``Bau-Leitstand #<S>`` (die Vorlage beginnt bei ``<title>``)."""
    titel = f"<title>Bau-Leitstand #{spec}</title>"
    neu, anzahl = re.subn(r"<title>.*?</title>", titel, vorlage, count=1, flags=re.DOTALL)
    return neu if anzahl else f"{titel}\n{vorlage}"


def schreibe_seite(ablage: Ablage) -> Path:
    ablage.html_datei.parent.mkdir(parents=True, exist_ok=True)
    ablage.html_datei.write_text(rendere_seite(VORLAGE.read_text(encoding="utf-8"), ablage.spec), encoding="utf-8")
    return ablage.html_datei


def anweisung_text(
    spec: str,
    python: str,
    skript: str,
    ordner: str,
    url: str | None,
    *,
    seed_fehlt: bool,
    mail_fehlt: bool,
) -> str:
    """Takt-Anweisung für die Session — reine Rechnung aus dem Stand."""
    ls = f"{python} {skript} {spec}"
    kopf = [
        f"# Bau-Leitstand Spec {spec} — je Durchlauf Antwort max. 1 Zeile, nichts erklären",
        "",
        "Werkzeuge nicht geladen → ToolSearch `select:ArtifactData,Artifact`.",
        "Ein Befehl endet mit einem hier nicht genannten Exit → Antwort „FEHLER <Befehl> Exit <n>“, Durchlauf beenden.",
        f"Keine anderen Dateien lesen als die hier genannten (alle unter {ordner}).",
        "Keine Übergabe-Datei (.claude-handoff*.md) schreiben — der Leitstand startet an der Handoff-Grenze selbst neu.",
        "",
    ]
    batch = (
        "writes-Datei lesen (JSON-Array). Genau EIN ArtifactData-Aufruf: action `batch`, "
        f"url `{url or '<URL>'}`, writes = das Array unverändert."
    )
    nur_erfolg = f"Nur wenn der Batch erfolgreich war: Bash `{ls} bestaetigen`. Sonst Antwort „FEHLER <Grund>“, nicht bestätigen."
    if not url:
        schritte = [
            "## Ersteinrichtung (noch keine Seite)",
            f"1. Bash `{ls} url`. Exit 0 → Seite gibt es schon: Antwort „Seite da“, Durchlauf beenden. Exit 4 → weiter.",
            f"2. Bash `{ls} seite` → stdout = Pfad der HTML-Datei.",
            (
                f"3. Werkzeug Artifact, publish: diese Datei, Titel `Bau-Leitstand #{spec}`, "
                'capabilities `{"db":{}}`, icon `chart`. Die zurückgegebene URL = <URL>.'
            ),
            f"4. Bash `{ls} url --setzen <URL>`.",
            f"5. Bash `{ls} seed`. Exit 3 → weiter mit 8. Exit 0 → stdout = Pfad der writes-Datei.",
            f"6. {batch}",
            f"7. {nur_erfolg}",
            f"8. Bash `{ls} mail`.",
            "Antwort: „Seite angelegt <URL>“.",
        ]
        return "\n".join([*kopf, *schritte, ""])
    schritte = ["## Takt"]
    if mail_fehlt:
        schritte.append(f"- Zuerst Bash `{ls} mail`.")
    if seed_fehlt:
        schritte += [
            f"1. Bash `{ls} seed`. Exit 3 → weiter mit 4 (vorbereiten). Exit 0 → stdout = Pfad der writes-Datei.",
            f"2. {batch}",
            f"3. {nur_erfolg} Danach Antwort „seed gesendet“, Durchlauf beenden.",
            f"4. Bash `{ls} vorbereiten`. Exit 3 → Antwort „nichts Neues“, fertig. Exit 0 → stdout = Pfad der writes-Datei.",
            f"5. {batch}",
            f"6. {nur_erfolg}",
        ]
    else:
        schritte += [
            f"1. Bash `{ls} vorbereiten`. Exit 3 → Antwort „nichts Neues“, fertig. Exit 0 → stdout = Pfad der writes-Datei.",
            f"2. {batch}",
            f"3. {nur_erfolg}",
        ]
    return "\n".join([*kopf, *schritte, ""])


def schreibe_anweisung(ablage: Ablage, python: str | None = None) -> Path:
    zustand = lade_zustand(ablage.zustand_datei)
    text = anweisung_text(
        ablage.spec,
        python or python_befehl(),
        skript_pfad(),
        ablage.ordner.as_posix(),
        lies_url(ablage),
        seed_fehlt=not zustand["seed_gesendet"],
        mail_fehlt=not ablage.mail_datei.exists(),
    )
    ablage.anweisung_datei.parent.mkdir(parents=True, exist_ok=True)
    ablage.anweisung_datei.write_text(text, encoding="utf-8")
    return ablage.anweisung_datei


def start_mail(ablage: Ablage) -> int:
    """Genau eine Mail je Spec mit dem Link zur Seite (Art ``leitstand_start``, auch bei ``nur_kritisch``)."""
    if ablage.mail_datei.exists():
        log.info("Start-Mail Spec %s schon erledigt (%s) — keine zweite Mail.", ablage.spec, ablage.mail_datei.name)
        return 0
    url = lies_url(ablage)
    if not url:
        log.error("Start-Mail Spec %s: noch keine Seiten-URL — erst `url --setzen`.", ablage.spec)
        return EXIT_KEINE_URL
    konfig = config.lade(ablage.repo)
    if not melder.mail_eingerichtet(konfig):
        log.info("Start-Mail Spec %s: mail.befehl fehlt — keine Mail, nur Log. Seite: %s", ablage.spec, url)
        speichere_json(ablage.mail_datei, {"status": "ohne_mail", "url": url, "zeit": jetzt_iso()})
        return 0
    schluessel = f"{MAIL_ART}_{ablage.spec}"
    betreff = f"Bau-Leitstand Spec #{ablage.spec}"
    text = f"Live-Stand der Spec #{ablage.spec} (auch am Handy): {url}"
    if not melder.darf_raus(MAIL_ART, konfig):
        melder.melden(ablage.repo, MAIL_ART, betreff, text, schluessel, konfig=konfig)
        speichere_json(ablage.mail_datei, {"status": "nur_log", "url": url, "zeit": jetzt_iso()})
        return 0
    if melder.melden(ablage.repo, MAIL_ART, betreff, text, schluessel, konfig=konfig) or melder.schon_gesendet(
        ablage.repo, schluessel
    ):
        speichere_json(ablage.mail_datei, {"status": "gesendet", "url": url, "zeit": jetzt_iso()})
        return 0
    log.error("Start-Mail Spec %s nicht verschickt (Mail-Befehl gescheitert, siehe oben).", ablage.spec)
    return 1


def _warte(prozess: Any, timeout: float) -> int:
    """Wartet bis ``timeout`` s auf ``prozess``; minimale Attrappen ohne ``timeout``-Schlüsselwort nutzen ``wait()``."""
    try:
        parameter = inspect.signature(prozess.wait).parameters
    except (TypeError, ValueError):
        return int(prozess.wait(timeout=timeout))
    timeout_param = parameter.get("timeout")
    nimmt_timeout = (
        timeout_param is not None
        and timeout_param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ) or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameter.values())
    if nimmt_timeout:
        return int(prozess.wait(timeout=timeout))
    return int(prozess.wait())


def sitzung(
    ablage: Ablage,
    *,
    probe: bool = False,
    grenze: int | None = None,
    aufsicht_takt: float | None = None,
    ruhe_warte_max: float = RUHE_WARTE_MAX_S,
    max_staffeln: int = MAX_STAFFELN,
) -> int:
    """Interaktive Leitstand-Session im aktuellen Fenster (Tab/tmux) — hält eine Sperre gegen Doppelstart.

    Überwacht das Transkript der laufenden Session gegen die Handoff-Grenze aus
    ``~/.claude/smart-zone.json`` (:func:`to_spawn.waechter_lauf.handoff_grenze`, #950).
    Erreicht der Kontext die Grenze, beendet der Rahmen die alte Session nach Abschluss des
    laufenden Takts ruhig und startet im selben Fenster eine frische Session (Staffelwechsel).
    """
    lk = leitstand_konfig(ablage.repo)
    if not ist_aktiv(lk):
        log.info("leitstand.aktiv ist aus — keine Leitstand-Session für Spec %s.", ablage.spec)
        return EXIT_AUS
    python = python_befehl()
    modell = str(lk.get("modell") or MODELL)
    takt = str(lk.get("takt") or TAKT)
    anweisung = schreibe_anweisung(ablage, python).as_posix()
    werkzeuge = erlaubte_werkzeuge(python, skript_pfad(), ablage.ordner.as_posix())
    if probe:
        befehl = sitzung_befehl(modell, werkzeuge, anweisung, takt)
        print(json.dumps(befehl, ensure_ascii=False))
        return 0
    claude = starter.finde("claude")  # unter Test nur die Attrappe (#1406)
    if not Path(claude).is_file():
        log.error("claude nicht gefunden — Leitstand-Session nicht gestartet.")
        return 127
    sperre = nimm_sperre(ablage.sitzung_sperre)
    if sperre is None:
        log.error("Leitstand-Session für Spec %s läuft schon (Sperre belegt).", ablage.spec)
        return 2
    env = {**sitzung_env.ohne_sitzung(), "TO_SPAWN_REPO": str(ablage.repo), "PYTHONIOENCODING": "utf-8"}
    # ohne_sitzung: Sitzungs-Variablen = Child Session, kein Transkript/Resume (wie bau.py)
    env["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] = "1"
    env[speicher.ANZEIGE_VARIABLE] = "1"  # Anzeige-Fenster belegt nie einen Platz (#460)
    terminal_maus.maus_ruhig(env)  # Klick-Sperre nur hier, nicht global (Windows)
    ziel_grenze = grenze if grenze is not None else waechter_lauf.handoff_grenze(modell)
    ueberwachung_takt = (
        aufsicht_takt
        if aufsicht_takt is not None
        else (waechter_lauf.zahl_aus_umgebung("TO_SPAWN_AUFSICHT_TAKT", waechter_lauf.TAKT_S) or waechter_lauf.TAKT_S)
    )
    try:
        for runde in range(1, max_staffeln + 2):
            if runde > 1:
                anweisung = schreibe_anweisung(ablage, python).as_posix()
            sid = str(uuid.uuid4())
            befehl = sitzung_befehl(modell, werkzeuge, anweisung, takt, session_id=sid)
            datei = waechter_lauf.transkript_ordner(ablage.repo) / f"{sid}.jsonl"
            ab = datei.stat().st_size if datei.is_file() else 0
            grenze_erreicht = threading.Event()

            def bei_grenze(
                kontext: int,
                _event: threading.Event = grenze_erreicht,
                _runde: int = runde,
            ) -> None:
                log.info(
                    "Leitstand #%s (Runde %s): Handoff-Grenze erreicht (%s >= %s Token) — Staffelwechsel.",
                    ablage.spec,
                    _runde,
                    kontext,
                    ziel_grenze,
                )
                _event.set()

            # Vordergrund im eigenen Fenster (braucht die Konsole) — kein Hintergrundstart.
            # Job hält den ganzen Baum, auch Git-Bash-Kinder; Blockende beendet den Rest (#122).
            prozess = starter.starte([claude, *befehl[1:]], cwd=ablage.repo, env=env)
            with sitzungsjob.gebunden(prozess):
                aufsicht = waechter_lauf.Aufsicht(
                    datei,
                    ab,
                    ueberwachung_takt,
                    lambda _text: None,
                    grenze=ziel_grenze,
                    bei_grenze=bei_grenze,
                )
                aufsicht.start()
                grenze_seit: float | None = None
                try:
                    while True:
                        try:
                            rc = _warte(prozess, ueberwachung_takt)
                            break
                        except subprocess.TimeoutExpired:
                            if not grenze_erreicht.is_set():
                                continue
                            if grenze_seit is None:
                                grenze_seit = time.monotonic()
                            if (
                                waechter_lauf.session_ruhig(datei, min(ueberwachung_takt, 1.0))
                                or time.monotonic() - grenze_seit >= ruhe_warte_max
                            ):
                                log.info(
                                    "Leitstand #%s: beende alte Session (Runde %s) für Staffelwechsel.",
                                    ablage.spec,
                                    runde,
                                )
                                aufsicht.halt.set()
                                waechter_lauf._beende(prozess)
                                rc = 0
                                break
                finally:
                    aufsicht.halt.set()
                    aufsicht.join(timeout=ueberwachung_takt + 1)
            if not grenze_erreicht.is_set():
                return rc
            if runde > max_staffeln:
                log.error(
                    "Leitstand #%s: %s Staffelwechsel erreicht — keine weitere Nachfolge.",
                    ablage.spec,
                    max_staffeln,
                )
                return rc
            log.info("Leitstand #%s: Staffelwechsel %s — starte frische Session.", ablage.spec, runde)
        return 0
    finally:
        sperre.close()


def fuehre_aus(a: argparse.Namespace, ablage: Ablage) -> int:
    """Unterbefehle ohne Takt-Sperre (die Session hält beim Takt die Sperre nicht)."""
    if a.befehl == "url":
        if a.setzen:
            try:
                setze_url(ablage, a.setzen)
            except ValueError as fehler:
                log.error("%s", fehler)
                return 2
        url = lies_url(ablage)
        if not url:
            return EXIT_KEINE_URL
        print(url)
        return 0
    if a.befehl == "seite":
        print(schreibe_seite(ablage).as_posix())
        return 0
    if a.befehl == "anweisung":
        print(schreibe_anweisung(ablage).as_posix())
        return 0
    if a.befehl == "mail":
        return start_mail(ablage)
    if a.befehl == "aktiv":
        aktiv = ist_aktiv(leitstand_konfig(ablage.repo))
        print("an" if aktiv else "aus")
        return 0 if aktiv else EXIT_AUS
    return sitzung(ablage, probe=a.probe)
