"""Abschluss-Paket einer Spec: alles zum Durchschauen an David (vor und nach dem Live-Deploy).

Aufruf: ``python abschluss_paket.py <S> --stage <url> --rundschau <link> --tests <link>
[--belege <link>] [--direkt "<Titel>=<url>"]… [--basic-auth-nutzer <name>]
[--app-rolle "<Name (rolle)>"]… [--bitwarden <Eintragsname>] [--neu "<Satz>"]…
[--tun "<Satz>"]… [--stand abnahme|live] [--repo <pfad>] [--dry-run]``

Schreibt ``docs/agents/abschluss_<S>.md`` (bei ``--stand live``: ``abschluss_<S>_live.md``) und
``~/.claude/data/abschluss/offen/<projekt>_<S>.json`` (liest der Hook
``abschluss-melder.mjs`` und zeigt die Links in der nächsten Session einmal an).
Mail über ``mail.befehl`` aus ``.to-spawn/config.json`` (Art ``spec_fertig``, geht auch bei
``mail.nur_kritisch`` raus) — nur wenn dort eingerichtet. Schlüssel gegen doppelte Mails:
``abschluss_<S>`` bzw. ``abschluss_<S>_live``. ``--dry-run``: nichts schreiben, nichts mailen.
Ohne ``--belege``/``--direkt``/Zugang/``--tun``/``live`` bleibt die alte Kurzform („3 Links“).
Die Mail nennt nur Namen, nie ein Passwort; steht in einem Argument etwas wie ``passwort=`` oder
``https://nutzer:pass@``, bricht das Skript mit Exit 2 ab, ohne etwas zu schreiben oder zu mailen.
Ordner-Override für Tests: ``TO_SPAWN_ABSCHLUSS_ORDNER``.

Eine Mail je Spec (#586), zusätzlich zum Aufruf oben:

* ``abschluss_paket.py ablegen <S> <gleiche Argumente> [--spec-fertig <ISO>]`` baut das Paket
  (gleicher Geheimnis-Check), mailt nicht und legt es als ``.to-spawn/abschluss_<S>[_live]_paket.json`` ab.
  Eine schon abgelegte SPEC-FERTIG-Zeit bleibt (erste gewinnt). Exit 0, 2 = Passwort/falsches Argument.
* ``abschluss_paket.py nachsehen <S> [--stand abnahme|live] [--jetzt <ISO>] [--repo <pfad>] [--dry-run]`` verschickt die eine
  Mail (Spec-Teil mit „Je Ticket“ + „Wirkungskreis“ → System-Teil = Rückblick-Marker
  ``docs/agents/rueckblick_<S>.md`` → Code-Befunde = Thermo-Marker ``docs/agents/thermo_<S>.md``),
  sobald beide Marker da sind; fehlt einer ``WARTE_MINUTEN`` (= Thermo-Sperre, 120) nach SPEC FERTIG noch, geht sie mit
  Vermerk „Rückblick fehlgeschlagen: …“ raus. Gleicher Mail-Schlüssel wie oben.
  Exit 0 = verschickt/schon verschickt/Probe, 1 = Mail-Befehl gescheitert, 2 = keine Ablage,
  3 = wartet noch.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
_SKRIPTE = str(Path(__file__).resolve().parent)
if _SKRIPTE not in sys.path:
    sys.path.insert(0, _SKRIPTE)
from test_uebersicht import lade_tickets  # noqa: E402
from thermo_lauf import SPERRE_MIN  # noqa: E402
from thermo_lauf import marker_pfad as thermo_marker_pfad  # noqa: E402

from to_spawn import bau_log, capo, config, melder  # noqa: E402

log = logging.getLogger("abschluss_paket")

#: Geheimnis-Muster in Argumenten: ``passwort=``, ``password:``, ``pw=`` — auch als Wortende
#: (``Zugangspasswort:``, ``meinPW=``) — oder ``nutzer:pass@`` mit oder ohne ``https://``.
_GEHEIM = re.compile(
    r"(?i)(?:(?:passwort|password|passwd|kennwort|pw)\s*[:=])"
    r"|(?:\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:[^/\s@]+@)"
    r"|(?:(?:^|[\s=])[^\s/:@=]+:[^\s/@]+@)"
)
_URL = re.compile(r"^https?://\S+$", re.I)


@dataclass
class Paket:
    """Alles, was in Datei und Mail steht."""

    spec: int
    stage: str
    rundschau: str
    tests: str
    saetze: list[str]
    belege: str = ""
    direkt: list[tuple[str, str]] = field(default_factory=list)
    basic_auth_nutzer: str = ""
    app_rollen: list[str] = field(default_factory=list)
    bitwarden: str = ""
    tun: list[str] = field(default_factory=list)
    stand: str = "abnahme"


def abschluss_ordner() -> Path:
    """Wurzel ``…/abschluss`` (Unterordner ``offen/`` und ``gezeigt/``)."""
    return Path(os.environ.get("TO_SPAWN_ABSCHLUSS_ORDNER") or Path.home() / ".claude" / "data" / "abschluss")


def geheimnis_in(argv: list[str]) -> str | None:
    """Schalter, in dessen Wert ein Passwort-Muster steckt — sonst ``None``. Nennt nie den Wert."""
    schalter = "ein Argument"
    for teil in argv:
        if teil.startswith("--") and "=" not in teil:
            schalter = teil
            continue
        if _GEHEIM.search(teil):
            return teil.split("=", 1)[0] if teil.startswith("--") else schalter
    return None


def _tickets(repo: Path, spec: int) -> list[tuple[str, str]]:
    try:
        return lade_tickets(repo, spec)
    except (OSError, ValueError, AttributeError) as fehler:
        log.warning("Manifest spec-%s nicht lesbar: %s", spec, fehler)
        return []


def neu_saetze(repo: Path, spec: int, neu: list[str]) -> list[str]:
    """Bis zu 3 Sätze „was ist neu“: ``--neu`` vor Ticket-Titeln aus dem Manifest."""
    saetze = [s.strip() for s in neu if s.strip()]
    if len(saetze) < 3:
        saetze += [f"Neu: {titel.strip()}." for _nr, titel in _tickets(repo, spec) if titel.strip()]
    return saetze[:3]


def tun_saetze(repo: Path, spec: int, tun: list[str], stand: str) -> list[str]:
    """Bis zu 3 Sätze „was du tun musst“: ``--tun`` oder Standard (Zettel, Abnahme-Ticket)."""
    eigene = [s.strip() for s in tun if s.strip()]
    if eigene:
        return eigene[:3]
    abnahme = next((nr for nr, titel in _tickets(repo, spec) if titel.strip().lower().startswith("abnahme")), "")
    if stand == "live":
        saetze = ["Die echte App kurz über die Direkt-Links durchklicken."]
        if abnahme:
            saetze.append(f"Passt alles: Abnahme-Ticket #{abnahme} schließen.")
        return saetze
    saetze = [
        "Die Test-App über die Direkt-Links durchklicken.",
        "Passt alles: den Zettel für den Live-Deploy ausstellen.",
    ]
    if abnahme:
        saetze.append(f"Abnahme-Ticket #{abnahme} prüfen und abhaken.")
    return saetze


def markdown(spec: int, stage: str, rundschau: str, tests: str, saetze: list[str]) -> str:
    """Alte Kurzform: 3 Links + was neu ist."""
    zeilen = [
        f"# Spec {spec} fertig — 3 Links",
        "",
        f"1. **Stage-App** (auch am Handy): {stage}",
        f"2. **Rundschau**: {rundschau}",
        f"3. **Test-Übersicht**: {tests}",
        "",
        "## Was ist neu",
        "",
        *[f"- {s}" for s in saetze],
        "",
    ]
    return "\n".join(zeilen)


def betreff(p: Paket, neue_form: bool) -> str:
    """Betreff: alte Kurzform, „bereit zur Abnahme“ oder „live“."""
    if not neue_form:
        return f"Spec {p.spec} fertig — 3 Links"
    if p.stand == "live":
        return f"Spec {p.spec} live — alles zum Durchschauen"
    return f"Spec {p.spec} bereit zur Abnahme — alles zum Durchschauen"


def markdown_voll(p: Paket) -> str:
    """Neue Form: 5 nummerierte Punkte, dann „Was ist neu“ und „Was du tun musst“."""
    direkt = [f"   - {titel}: {url}" for titel, url in p.direkt] or ["   - (keine angegeben)"]
    zugang = [f"   - Stage-App: {p.stage}"]
    if p.basic_auth_nutzer:
        zugang.append(f"   - Anmelde-Name (Basic-Auth): {p.basic_auth_nutzer}")
    zugang += [f"   - App-Rolle: {rolle}" for rolle in p.app_rollen]
    if p.bitwarden:
        zugang.append(f"   - Passwort: Bitwarden-Eintrag {p.bitwarden}")
    zeilen = [
        f"# {betreff(p, True)}",
        "",
        f"1. **Rundschau** (was gebaut wurde, in einfachen Worten): {p.rundschau}",
        f"2. **Belegseiten** (je Ticket der Nachweis): {p.belege or '(noch keine)'}",
        f"3. **Test-Übersicht** (welche Prüfungen grün sind): {p.tests}",
        "4. **Direkt-Links** (springen an die richtige Stelle, auch am Handy):",
        *direkt,
        "5. **Zugang**:",
        *zugang,
        "",
        "## Was ist neu",
        "",
        *[f"- {s}" for s in p.saetze],
        "",
        "## Was du tun musst",
        "",
        *[f"- {s}" for s in p.tun],
        "",
    ]
    return "\n".join(zeilen)


def _direkt(roh: list[str], ap: argparse.ArgumentParser) -> list[tuple[str, str]]:
    """``"<Titel>=<url>"`` zerlegen; falsches Format → Exit 2 mit klarer Meldung."""
    paare: list[tuple[str, str]] = []
    for eintrag in roh:
        titel, gleich, url = eintrag.partition("=")
        titel, url = titel.strip(), url.strip()
        if not gleich or not titel or not _URL.match(url):
            ap.error(f"--direkt „{eintrag}“: erwartet „<Titel>=<url>“ mit Titel und Link ab http:// oder https://")
        paare.append((titel, url))
    return paare


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Abschluss-Paket einer Spec (alles zum Durchschauen an David).")
    ap.add_argument("spec", type=int)
    ap.add_argument("--stage", required=True, help="Stage-App-Link (staging.url)")
    ap.add_argument("--rundschau", required=True, help="Link der veröffentlichten Rundschau")
    ap.add_argument("--tests", required=True, help="Link der veröffentlichten Test-Übersicht")
    ap.add_argument("--belege", default="", help="Link der veröffentlichten Belegseiten-Übersicht")
    ap.add_argument("--direkt", action="append", default=[], help="Direkt-Link „<Titel>=<url>“ (mehrfach)")
    ap.add_argument("--basic-auth-nutzer", default="", help="Anmelde-Name der Stage (nie das Passwort)")
    ap.add_argument("--app-rolle", action="append", default=[], help="App-Rolle „<Name (rolle)>“ (mehrfach)")
    ap.add_argument("--bitwarden", default="", help="Name des Bitwarden-Eintrags mit dem Passwort")
    ap.add_argument("--neu", action="append", default=[], help="Satz „was ist neu“ (bis 3×)")
    ap.add_argument("--tun", action="append", default=[], help="Satz „was du tun musst“ (bis 3×)")
    ap.add_argument("--stand", choices=("abnahme", "live"), default="abnahme")
    ap.add_argument("--repo", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true", help="nichts schreiben, nichts mailen")
    return ap


def _geheimnis_abbruch(argv: list[str]) -> bool:
    """Passwort in einem Argument → Meldung auf stderr und ``True`` (Aufrufer endet mit Exit 2)."""
    stelle = geheimnis_in(argv)
    if stelle:
        sys.stderr.write(
            f"FEHLER: {stelle} enthält ein Passwort — die Mail nennt nur Namen. Passwort weglassen, "
            "nur den Bitwarden-Eintrag nennen (--bitwarden). Nichts geschrieben, nichts gemailt.\n"
        )
    return bool(stelle)


def _repo(roh: Path | None) -> Path:
    return (
        roh or (Path(os.environ["TO_SPAWN_REPO"]) if os.environ.get("TO_SPAWN_REPO") else config.repo_wurzel())
    ).resolve()


def _paket(a: argparse.Namespace, ap: argparse.ArgumentParser, repo: Path) -> tuple[Paket, bool]:
    """Paket aus den Argumenten bauen; zweiter Wert: neue Form (sonst alte Kurzform „3 Links“)."""
    direkt = _direkt(a.direkt, ap)
    rollen = [r.strip() for r in a.app_rolle if r.strip()]
    neue_form = bool(a.belege or direkt or a.basic_auth_nutzer or rollen or a.bitwarden or a.tun or a.stand == "live")
    p = Paket(
        spec=a.spec,
        stage=a.stage,
        rundschau=a.rundschau,
        tests=a.tests,
        saetze=neu_saetze(repo, a.spec, a.neu),
        belege=a.belege,
        direkt=direkt,
        basic_auth_nutzer=a.basic_auth_nutzer.strip(),
        app_rollen=rollen,
        bitwarden=a.bitwarden.strip(),
        tun=tun_saetze(repo, a.spec, a.tun, a.stand),
        stand=a.stand,
    )
    return p, neue_form


def _endung(p: Paket) -> str:
    return "_live" if p.stand == "live" else ""


def _md_datei(repo: Path, p: Paket) -> Path:
    return repo / "docs" / "agents" / f"abschluss_{p.spec}{_endung(p)}.md"


def _offen_datei(repo: Path, p: Paket) -> Path:
    return abschluss_ordner() / "offen" / f"{repo.name}_{p.spec}.json"


def _offen_daten(repo: Path, p: Paket) -> dict[str, Any]:
    return {
        "projekt": repo.name,
        "repo": str(repo),
        "spec": p.spec,
        "stage": p.stage,
        "rundschau": p.rundschau,
        "tests": p.tests,
        "belege": p.belege,
        "direkt": [{"titel": t, "url": u} for t, u in p.direkt],
        "stand": p.stand,
        "zugang": {"basic_auth_nutzer": p.basic_auth_nutzer, "app_rollen": p.app_rollen, "bitwarden": p.bitwarden},
        "erstellt": datetime.now().isoformat(timespec="seconds"),
    }


def _probe(md_datei: Path, text: str, offen: Path, titel: str, konfig: dict[str, Any]) -> None:
    mail = "ja" if melder.mail_eingerichtet(konfig) else "nein (mail.befehl leer)"
    sys.stdout.write(f"[Probe] {md_datei}\n{text}\n[Probe] {offen}\n[Probe] Mail „{titel}“: {mail}\n")


def _schreiben(md_datei: Path, text: str, offen: Path, daten: dict[str, Any]) -> None:
    md_datei.parent.mkdir(parents=True, exist_ok=True)
    md_datei.write_text(text, encoding="utf-8")
    offen.parent.mkdir(parents=True, exist_ok=True)
    offen.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Abschluss geschrieben: %s · %s", md_datei, offen)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if argv[:1] == ["ablegen"]:
        return ablegen(argv[1:])
    if argv[:1] == ["nachsehen"]:
        return nachsehen(argv[1:])
    if _geheimnis_abbruch(argv):
        return 2
    ap = _parser()
    a = ap.parse_args(argv)
    repo = _repo(a.repo)
    p, neue_form = _paket(a, ap, repo)
    text = markdown_voll(p) if neue_form else markdown(p.spec, p.stage, p.rundschau, p.tests, p.saetze)
    md_datei, offen = _md_datei(repo, p), _offen_datei(repo, p)
    konfig = config.lade(repo)
    titel = betreff(p, neue_form)
    if a.dry_run:
        _probe(md_datei, text, offen, titel, konfig)
        return 0
    _schreiben(md_datei, text, offen, _offen_daten(repo, p))
    if not melder.mail_eingerichtet(konfig):
        log.info("Mail nicht eingerichtet (mail.befehl leer) — nur Datei.")
        return 0
    if not melder.melden(repo, "spec_fertig", titel, text, f"abschluss_{p.spec}{_endung(p)}", konfig=konfig):
        log.warning("Mail „%s“ nicht verschickt (schon gesendet oder Befehl gescheitert).", titel)
        return 1
    return 0


# --- #586: eine Mail je Spec (ablegen bei SPEC FERTIG, nachsehen bis Rückblick + Thermo da) ---------

#: So lange wartet ``nachsehen`` nach SPEC FERTIG auf Rückblick- und Thermo-Marker, dann geht die
#: Mail mit Vermerk raus — ausdrücklich dieselbe Grenze wie die Thermo-Sperre (Grill E16).
WARTE_MINUTEN = SPERRE_MIN
# Echter Lauf über Spec 578 brauchte 137 s (graphify update + affected je Datei).
WIRKUNGSKREIS_ZEITLIMIT_S = 600


def ablage_pfad(repo: Path, spec: int, stand: str = "abnahme") -> Path:
    """Abgelegtes Paket einer Spec je Stand (schreibt ``ablegen``, liest ``nachsehen``)."""
    endung = "_live" if stand == "live" else ""
    return repo / ".to-spawn" / f"abschluss_{spec}{endung}_paket.json"


def rueckblick_pfad(repo: Path, spec: int) -> Path:
    """Marker des Rückblicks (#581/#585)."""
    return repo / "docs" / "agents" / f"rueckblick_{spec}.md"


def _zeit(text: str | None, ap: argparse.ArgumentParser, schalter: str) -> datetime:
    """ISO-Zeitpunkt (ohne Zone = Ortszeit) oder jetzt; falsches Format → Exit 2."""
    if not text:
        return datetime.now().astimezone()
    try:
        return datetime.fromisoformat(text).astimezone()
    except ValueError:
        ap.error(f"{schalter} „{text}“: erwartet ISO-Zeitpunkt wie 2026-10-06T10:00:00+02:00")


def ablegen(argv: list[str]) -> int:
    """Paket bauen und ablegen, nicht mailen. Eine schon abgelegte SPEC-FERTIG-Zeit bleibt."""
    if _geheimnis_abbruch(argv):
        return 2
    ap = _parser()
    ap.add_argument("--spec-fertig", default=None, help="Zeitpunkt „SPEC FERTIG“ (ISO), sonst jetzt")
    a = ap.parse_args(argv)
    repo = _repo(a.repo)
    p, neue_form = _paket(a, ap, repo)
    datei = ablage_pfad(repo, p.spec, p.stand)
    fertig = _zeit(a.spec_fertig, ap, "--spec-fertig").isoformat(timespec="seconds")
    alt = melder.lade_json(datei)
    if alt.get("spec_fertig"):
        fertig = str(alt["spec_fertig"])
    daten = {"paket": asdict(p), "neue_form": neue_form, "spec_fertig": fertig}
    if a.dry_run:
        sys.stdout.write(f"[Probe] {datei}\n{json.dumps(daten, ensure_ascii=False, indent=2)}\n")
        return 0
    melder.speichere_json(datei, daten)
    log.info("Abschluss-Paket abgelegt: %s (SPEC FERTIG %s)", datei, fertig)
    return 0


def _ticket_zeilen(repo: Path, spec: int, konfig: dict[str, Any]) -> list[str]:
    """Je Ticket: Titel, Umfang aus der jüngsten Bau-Log-Zusammenfassung, Belegseiten (wie capo)."""
    ordner = capo.belege_ordner(konfig)
    zeilen = []
    for nr, titel in _tickets(repo, spec):
        try:
            umfang = str(bau_log.zusammenfassung(repo, nr).get("umfang_ist") or "").replace("\n", " ").strip()
            umfang = umfang or "kein Bau-Log"
        except (OSError, ValueError) as fehler:
            log.warning("Bau-Log #%s unlesbar: %s", nr, fehler)
            umfang = f"Bau-Log unlesbar: {fehler}"
        try:
            belege = ", ".join(capo.belegseiten(repo, int(nr), ordner=ordner)) or "Belegseite fehlt"
        except (OSError, ValueError) as fehler:
            log.warning("Belegseite #%s nicht ermittelt: %s", nr, fehler)
            belege = f"Belegseite nicht ermittelt: {fehler}"
        zeilen.append(f"- #{nr} {titel.strip()} — {umfang} · Vorher/Nachher: {belege}")
    return zeilen or ["- (keine Tickets im Manifest)"]


def wirkungskreis(repo: Path, spec: int) -> str:
    """Ausgabe von ``scripts/wirkungskreis.py`` (#583): Exit 0 = Text, Exit 2 = „nicht ermittelbar: …“."""
    skript = repo / "scripts" / "wirkungskreis.py"
    if not skript.is_file():
        return f"Wirkungskreis nicht ermittelt: scripts/wirkungskreis.py fehlt in {repo.name}"
    try:
        lauf = subprocess.run(
            [sys.executable, "-X", "utf8", str(skript), str(spec), "--repo", str(repo)],
            cwd=str(repo),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=WIRKUNGSKREIS_ZEITLIMIT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        return f"Wirkungskreis nicht ermittelt: {fehler}"
    if lauf.returncode in (0, 2):
        return lauf.stdout.strip() or "Wirkungskreis nicht ermittelt: keine Ausgabe"
    return f"Wirkungskreis nicht ermittelt: Exit {lauf.returncode} — {(lauf.stderr or lauf.stdout).strip()[-300:]}"


def mail_text(repo: Path, p: Paket, neue_form: bool, konfig: dict[str, Any], system: str, befunde: str) -> str:
    """Die eine Mail: Spec-Teil → System-Teil (Rückblick) → Code-Befunde (Thermo)."""
    heute = markdown_voll(p) if neue_form else markdown(p.spec, p.stage, p.rundschau, p.tests, p.saetze)
    return "\n".join(
        [
            "## Spec-Teil",
            "",
            heute,
            "### Je Ticket",
            "",
            *_ticket_zeilen(repo, p.spec, konfig),
            "",
            "### Wirkungskreis",
            "",
            wirkungskreis(repo, p.spec),
            "",
            "## System-Teil",
            "",
            system.rstrip("\n"),
            "",
            "## Code-Befunde",
            "",
            befunde.rstrip("\n"),
            "",
        ]
    )


def nachsehen(argv: list[str]) -> int:
    """Mail verschicken, sobald Rückblick + Thermo da sind (oder WARTE_MINUTEN um sind).

    Exit 0 = verschickt/schon verschickt/Probe, 1 = Mail-Befehl gescheitert, 2 = keine Ablage,
    3 = wartet noch auf einen Marker.
    """
    ap = argparse.ArgumentParser(description="Abgelegtes Abschluss-Paket als eine Mail verschicken.")
    ap.add_argument("spec", type=int)
    ap.add_argument("--stand", choices=("abnahme", "live"), default="abnahme", help="welche Ablage")
    ap.add_argument("--jetzt", default=None, help="Zeitpunkt jetzt (ISO), sonst Uhr")
    ap.add_argument("--repo", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true", help="Mailtext zeigen, nichts schreiben, nichts mailen")
    a = ap.parse_args(argv)
    repo = _repo(a.repo)
    datei = ablage_pfad(repo, a.spec, a.stand)
    ablage = melder.lade_json(datei)
    try:
        roh = dict(ablage["paket"])
        roh["direkt"] = [(str(t), str(u)) for t, u in roh.get("direkt") or []]
        p = Paket(**roh)
        fertig = datetime.fromisoformat(str(ablage["spec_fertig"])).astimezone()
    except (KeyError, TypeError, ValueError) as fehler:
        sys.stderr.write(
            f"FEHLER: keine gültige Ablage {datei} ({fehler!r}) — erst „abschluss_paket.py ablegen {a.spec} …“.\n"
        )
        return 2
    schluessel = f"abschluss_{p.spec}{_endung(p)}"
    if not a.dry_run and melder.schon_gesendet(repo, schluessel):
        log.info("Mail zu %s schon verschickt — nichts zu tun.", schluessel)
        return 0
    jetzt = _zeit(a.jetzt, ap, "--jetzt")
    minuten = (jetzt - fertig).total_seconds() / 60
    if minuten < 0:
        log.warning("SPEC FERTIG %s liegt nach jetzt %s — zähle als 0 Minuten.", fertig, jetzt)
        minuten = 0.0
    marker = {
        "System": ("Rückblick-Marker", rueckblick_pfad(repo, p.spec)),
        "Code": ("Thermo-Marker", thermo_marker_pfad(repo, p.spec)),
    }
    fehlen = [name for name, (_art, pfad) in marker.items() if not pfad.is_file()]
    if fehlen and minuten < WARTE_MINUTEN:
        namen = ", ".join(str(marker[n][1].relative_to(repo)) for n in fehlen)
        sys.stdout.write(f"wartet: {namen} fehlt noch ({int(minuten)} von {WARTE_MINUTEN} Minuten nach SPEC FERTIG)\n")
        return 3
    unlesbar: set[str] = set()

    def teil(name: str) -> str:
        """Marker-Inhalt unverändert oder Vermerk — nie ein Absturz, sonst gäbe es nie eine Mail (E14)."""
        art, pfad = marker[name]
        pfad_rel = pfad.relative_to(repo).as_posix()
        if name in fehlen:
            return f"Rückblick fehlgeschlagen: {art} {pfad_rel} fehlt {WARTE_MINUTEN} Minuten nach SPEC FERTIG"
        try:
            return pfad.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as fehler:
            log.warning("%s %s unlesbar: %s", art, pfad_rel, fehler)
            unlesbar.add(name)
            return f"Rückblick fehlgeschlagen: {art} {pfad_rel} unlesbar: {fehler}"

    system = teil("System")
    if "System" not in fehlen and "System" not in unlesbar:
        system = f"{system.rstrip()}\n\nAbhaken per Chat: „Rückblick {p.spec}: 1 ja, 2 nein“"
    konfig = config.lade(repo)
    neue_form = bool(ablage.get("neue_form"))
    text = mail_text(repo, p, neue_form, konfig, system, teil("Code"))
    titel = betreff(p, neue_form)
    md_datei, offen = _md_datei(repo, p), _offen_datei(repo, p)
    if a.dry_run:
        _probe(md_datei, text, offen, titel, konfig)
        return 0
    _schreiben(md_datei, text, offen, _offen_daten(repo, p))
    if not melder.mail_eingerichtet(konfig):
        log.warning("Mail nicht eingerichtet (mail.befehl leer) — nur Datei %s, keine Mail.", md_datei)
        return 0
    if not melder.melden(repo, "spec_fertig", titel, text, schluessel, konfig=konfig):
        log.warning("Mail „%s“ nicht verschickt (Befehl gescheitert).", titel)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
