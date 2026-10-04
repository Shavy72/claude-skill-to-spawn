"""Aufsicht über die Aufseher-Session: Nutzungs-Limit erkennen, Ausweich-Modell starten (#213).

``claude --fallback-model`` greift nur bei „overloaded/not available“, nicht beim
Nutzungs-Limit („You've hit your session limit“, „You've reached your Fable limit“).
Deshalb startet :func:`fahre` den Aufseher selbst (``Popen``) und liest in einem
Hintergrund-Faden die neuen Zeilen seines Transkripts
``~/.claude/projects/<cwd>/<session-id>.jsonl``. Taucht die Limit-Zeile auf:

* Aufseher läuft noch auf dem Haupt-Modell → Prozess beenden, Bau-Log-Zeile
  ``waechter_modell``, Mail ``waechter_ausweich``, Neustart per
  ``claude --resume <session-id> --model <Ausweich>`` mit kurzem Weiter-Prompt.
* Aufseher läuft schon auf dem Ausweich-Modell → Reset-Uhrzeit aus der Limit-Zeile
  lesen (#254): nennt sie eine, pausiert der Aufseher bis dahin (plus Puffer) und
  fährt danach selbst per ``--resume`` weiter; nennt sie keine (oder liegt der
  Reset mehr als :data:`MAX_WARTE_S` weg), bleibt es bei Mail ``session_tot``.

Rückkehr auf das Haupt-Modell (#436): beim Wechsel aufs Ausweich-Modell merkt sich
:func:`fahre` den Reset-Zeitpunkt der Limit-Zeile plus Puffer (unbekannt → jetzt +
:data:`RUECKKEHR_VORGABE_S`). Ist er erreicht, endet die Ausweich-Session, Bau-Log
``waechter_modell`` (grund „Limit vorbei“) und es geht per ``--resume`` mit dem
Haupt-Modell weiter. Nach einer Limit-Pause fährt der Aufseher ebenfalls auf dem
Haupt-Modell weiter — das Limit ist dann offen.

Erzwungene Ablösung (#436, Spec #399 E17): die Aufsicht misst den Kontext jeder neuen
Modellantwort (``hooks.kontext``: input + cache_read + cache_creation) gegen die
Handoff-Grenze aus ``~/.claude/smart-zone.json`` (:func:`handoff_grenze`). Erreicht er
sie, startet genau einmal je :func:`fahre` ein Faden die respawn-Tür
``respawn_aufseher.aufseher_abloesen`` (Handoff + Start-Prompt ins Pane ``$TMUX_PANE``
anfordern). Exit 0 → :func:`abloesung_schreiben`; die Abbruch-Bedingung in ``wache.py``
beendet die Session und startet den Nachfolger. Ohne tmux nur Warnung + Bau-Log-Zeile.

Der Aufpasser (#236) setzt ein stilles Aufseher-Fenster über ``fahre(session_id=…)``
mit ``--resume`` fort; die Gesprächs-ID steht in ``.to-spawn/sessions/wache-<S>.json``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import bau_log, hooks, melder, respawn_aufseher, sessions_datei

log = logging.getLogger("to_spawn.waechter_lauf")

_LIMIT_TEXT = re.compile(r"(reached|hit) your .*limit", re.IGNORECASE)
#: Uhrzeit-Angabe derselben Zeile: „· resets 5:40am (Europe/Berlin)“ (#254).
_RESET_TEXT = re.compile(
    r"resets?\s+(?:at\s+)?(?P<stunde>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<halb>am|pm)?"
    r"(?:\s*\((?P<zone>[^)]+)\))?",
    re.IGNORECASE,
)
TAKT_S = 5.0
#: Sekunden, nach denen ein fehlendes Transkript eine Warnung wert ist.
WARTE_TRANSKRIPT_S = 60.0
#: Puffer nach dem Reset, bevor der Aufseher weiterfährt (#254).
RESET_PUFFER_S = 120.0
#: Obergrenze der Pause. Darüber (z. B. Wochen-Limit) meldet der Aufseher wie bisher
#: und bleibt stehen — lieber ein Mensch als eine Pause über Tage.
MAX_WARTE_S = 6 * 3600.0
#: So weit darf ein Reset zurückliegen und noch gelten. Weiter zurück heißt: die Zeile
#: meint ein anderes Fenster — sonst startet der Aufseher im Takt des Puffers neu (F1).
RESET_TOLERANZ_S = 120.0
#: So oft pausiert der Aufseher in Folge. Danach steht er wie vor #254 — ein Limit,
#: das nach jedem Neustart sofort wieder greift, ist ein Fall für einen Menschen (F1).
MAX_PAUSEN = 3
#: Rückkehr aufs Haupt-Modell, wenn die Limit-Zeile keinen Reset nennt (#436): 5 h.
RUECKKEHR_VORGABE_S = 5 * 3600.0
#: Handoff-Grenzen in k-Token, wenn ``~/.claude/smart-zone.json`` fehlt oder schweigt.
HANDOFF_K_OPUS = 250
HANDOFF_K_NICHT_OPUS = 200
#: Umgebungs-Variable mit dem Pfad der Ablöse-Datei (setzt ``wache.py`` je Lauf).
ABLOESE_ENV = "TO_SPAWN_WACHE_ABLOESUNG"


def zahl_aus_umgebung(name: str, vorgabe: float) -> float:
    """Zahl aus einer Umgebungs-Variablen, nie negativ; Müll → ``vorgabe`` (F8)."""
    roh = (os.environ.get(name) or "").strip()
    if not roh:
        return vorgabe
    try:
        return max(0.0, float(roh))
    except ValueError:
        log.warning("%s=%r ist keine Zahl — es gilt %s.", name, roh, vorgabe)
        return vorgabe


def handoff_grenze(modell: str, heim: Path | None = None) -> int:
    """Handoff-Grenze in Token aus ``~/.claude/smart-zone.json`` (SSOT Doktrin Nr. 1).

    Opus-Modelle → ``haupt.handoff_k``, sonst ``haupt_nicht_opus.handoff_k``; fehlt die
    Datei oder der Wert, gelten :data:`HANDOFF_K_OPUS` / :data:`HANDOFF_K_NICHT_OPUS`.
    """
    opus = "opus" in modell.lower()
    schluessel, vorgabe = (
        ("haupt", HANDOFF_K_OPUS) if opus else ("haupt_nicht_opus", HANDOFF_K_NICHT_OPUS)
    )
    datei = (heim or Path.home()) / ".claude" / "smart-zone.json"
    try:
        daten: Any = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        log.info("Smart-Zone-Datei %s nicht lesbar (%s) — Grenze %s k.", datei, fehler, vorgabe)
        daten = {}
    abschnitt = daten.get(schluessel) if isinstance(daten, dict) else None
    wert = abschnitt.get("handoff_k") if isinstance(abschnitt, dict) else None
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or wert <= 0:
        wert = vorgabe
    return int(wert * 1000)


def abloesung_schreiben(ziel: Path, handoff: Path, start: str = "") -> None:
    """Ablöse-Datei ``{"handoff", "start"}`` schreiben — die einzige Schreibstelle (#436).

    ``start`` = Text des Start-Prompts (leer bei ``wache.py --abloesen``). Erst eine
    Nebendatei, dann umbenennen: die Abbruch-Bedingung in ``wache.py`` prüft nur, ob die
    Datei da ist, und darf nie eine halbe lesen.
    """
    neu = ziel.with_name(ziel.name + ".neu")
    neu.write_text(
        json.dumps({"handoff": str(handoff), "start": start}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(neu, ziel)


def transkript_ordner(cwd: Path, heim: Path | None = None) -> Path:
    """Ordner, in den Claude Code die Transkripte einer Arbeitsmappe schreibt."""
    return (
        (heim or Path.home())
        / ".claude"
        / "projects"
        / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    )


def _texte(eintrag: dict[str, Any]) -> str:
    inhalt = (eintrag.get("message") or {}).get("content")
    if isinstance(inhalt, str):
        return inhalt
    if isinstance(inhalt, list):
        return " ".join(
            str(b.get("text") or "")
            for b in inhalt
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def ist_limit_zeile(eintrag: dict[str, Any]) -> bool:
    """Limit-Meldung von Claude Code (nicht: ein Zitat im Nutzer- oder Werkzeug-Text)."""
    if eintrag.get("type") != "assistant":
        return False
    fehler = (
        bool(eintrag.get("isApiErrorMessage"))
        or (eintrag.get("message") or {}).get("model") == "<synthetic>"
    )
    if not fehler:
        return False
    # ``error == "rate_limit"`` allein reicht nicht: auch kurze API-Drosselungen tragen es.
    return bool(_LIMIT_TEXT.search(_texte(eintrag)))


def _zone(name: str | None, rueckfall: tzinfo | None) -> tzinfo | None:
    """Zeitzone aus der Klammer der Limit-Zeile; unbekannter Name → Ortszeit.

    Unter Windows kennt Python die Zeitzonen nur mit dem Paket ``tzdata``. Fehlt es,
    landet jede Angabe hier im Rückfall — deshalb eine Warnung statt einer Debug-Zeile.
    """
    if name:
        try:
            return ZoneInfo(name.strip())
        except (ZoneInfoNotFoundError, ValueError) as fehler:
            log.warning(
                "Zeitzone %r unbekannt (%s) — es gilt die Ortszeit dieses Rechners. "
                "Unter Windows hilft „pip install tzdata“.",
                name,
                fehler,
            )
    return rueckfall


def _aus_quota(eintrag: dict[str, Any]) -> datetime | None:
    """``quotaLimits.resetsAt`` ist die genaue Quelle (Unix-Zeit) — sie schlägt den Text."""
    quota = eintrag.get("quotaLimits")
    wert = quota.get("resetsAt") if isinstance(quota, dict) else None
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(wert), tz=timezone.utc).astimezone()
    except (OSError, OverflowError, ValueError) as fehler:
        log.debug("resetsAt %r unbrauchbar (%s) — Text lesen.", wert, fehler)
        return None


def _aus_text(text: str, jetzt: datetime) -> datetime | None:
    """Uhrzeit aus „resets 5:40am (Europe/Berlin)“ — schon vorbei heißt: morgen."""
    treffer = _RESET_TEXT.search(text)
    if not treffer:
        return None
    stunde = int(treffer["stunde"])
    minute = int(treffer["minute"] or 0)
    halb = (treffer["halb"] or "").lower()
    if halb == "pm" and stunde < 12:
        stunde += 12
    elif halb == "am" and stunde == 12:
        stunde = 0
    elif not halb and 1 <= stunde <= 12:
        # „resets 7“ kann 7 Uhr oder 19 Uhr heißen — raten wäre bis zu 12 h daneben (F7).
        log.info("Uhrzeit %r ohne am/pm ist mehrdeutig — keine Pause.", treffer.group(0))
        return None
    if stunde > 23 or minute > 59:
        log.debug("Uhrzeit %s:%s aus der Limit-Zeile ist keine Zeit.", stunde, minute)
        return None
    ortszeit = jetzt.astimezone(_zone(treffer["zone"], jetzt.tzinfo))
    # ``fold=1``: an der Zeitumstellung gibt es die Stunde zweimal — erst nach der
    # zweiten ist das Limit wirklich offen (F7).
    ziel = ortszeit.replace(
        hour=stunde, minute=minute, second=0, microsecond=0, fold=1
    )
    if ziel <= ortszeit:
        ziel += timedelta(days=1)  # über Mitternacht hinweg
    return ziel


def reset_zeitpunkt(
    eintrag: dict[str, Any], jetzt: datetime | None = None
) -> datetime | None:
    """Wann das Nutzungs-Limit wieder aufgeht; ``None``, wenn die Zeile es nicht sagt."""
    jetzt = jetzt or datetime.now().astimezone()
    return _aus_quota(eintrag) or _aus_text(_texte(eintrag), jetzt)


def warte_sekunden(
    ziel: datetime,
    *,
    jetzt: datetime | None = None,
    puffer: float = RESET_PUFFER_S,
    hoechstens: float = MAX_WARTE_S,
) -> float | None:
    """Sekunden bis ``ziel`` plus Puffer; ``None``, wenn das länger als ``hoechstens`` dauert."""
    jetzt = jetzt or datetime.now().astimezone()
    rest = (ziel - jetzt).total_seconds()
    if rest < -RESET_TOLERANZ_S:
        log.info(
            "Reset %s liegt %.0f s zurück — die Zeile meint ein anderes Fenster.",
            ziel,
            -rest,
        )
        return None
    sekunden = max(0.0, rest) + puffer
    if sekunden > hoechstens:
        log.info(
            "Reset erst %s (%.1f h) — das ist zu lang zum Warten.",
            ziel,
            sekunden / 3600.0,
        )
        return None
    return sekunden


def _warten(
    sekunden: float, takt: float, abbruch: Callable[[], bool] | None
) -> bool:
    """Die Pause absitzen; ``False``, wenn ``abbruch`` vorher greift (z. B. Umzug, #212)."""
    ende = time.monotonic() + sekunden
    while True:
        if abbruch is not None and abbruch():
            return False
        rest = ende - time.monotonic()
        if rest <= 0:
            return True
        time.sleep(min(takt, rest))


class Aufsicht(threading.Thread):
    """Liest neue Zeilen einer Transkript-Datei ab Byte ``ab`` und meldet die erste Limit-Zeile.

    Mit ``grenze`` misst sie zusätzlich den Kontext jeder Modellantwort
    (``hooks.kontext``) und ruft ``bei_grenze(kontext)`` genau einmal, sobald er die
    Grenze erreicht (#436).
    """

    def __init__(
        self,
        datei: Path,
        ab: int,
        takt: float,
        bei_limit: Callable[[str], None],
        warte_s: float = WARTE_TRANSKRIPT_S,
        grenze: int | None = None,
        bei_grenze: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(name="waechter-aufsicht", daemon=True)
        self.datei = datei
        self.pos = ab
        self.takt = takt
        self.bei_limit = bei_limit
        self.warte_s = warte_s
        self.halt = threading.Event()
        #: Die gefundene Limit-Zeile — der Rückruf liest daraus die Reset-Uhrzeit (#254).
        self.limit_eintrag: dict[str, Any] | None = None
        self.grenze = grenze
        self.bei_grenze = bei_grenze
        #: Höchster gemessener Kontext dieser Session in Token (#436).
        self.kontext = 0
        self._grenze_gemeldet = False

    def _kontext_messen(self, eintrag: dict[str, Any]) -> None:
        messung = hooks.kontext([eintrag])
        if messung is None:
            return
        self.kontext = max(self.kontext, messung["spitze"])
        if (
            self.grenze
            and self.bei_grenze is not None
            and not self._grenze_gemeldet
            and self.kontext >= self.grenze
        ):
            self._grenze_gemeldet = True
            self.bei_grenze(self.kontext)

    def _neue_zeilen(self, rest: bytes) -> tuple[list[bytes], bytes]:
        try:
            with self.datei.open("rb") as strom:
                strom.seek(self.pos)
                stueck = strom.read()
        except OSError as fehler:
            log.debug("Transkript %s (noch) nicht lesbar: %s", self.datei, fehler)
            return [], rest
        self.pos += len(stueck)
        *zeilen, rest = (rest + stueck).split(b"\n")
        return zeilen, rest

    def _limit_in(self, zeilen: list[bytes]) -> bool:
        for roh in zeilen:
            try:
                eintrag = json.loads(roh.decode("utf-8", errors="replace"))
            except ValueError as fehler:
                log.debug("Transkript-Zeile unlesbar (%s): %.80r", fehler, roh)
                continue
            if not isinstance(eintrag, dict):
                continue
            self._kontext_messen(eintrag)
            if ist_limit_zeile(eintrag):
                self.limit_eintrag = eintrag
                self.bei_limit(_texte(eintrag)[:200] or str(eintrag.get("error")))
                return True
        return False

    def run(self) -> None:
        rest = b""
        beginn = time.monotonic()
        gewarnt = False
        while True:
            zuletzt = self.halt.is_set()  # nach dem Halt genau einmal nachlesen
            zeilen, rest = self._neue_zeilen(rest)
            if self._limit_in(zeilen) or zuletzt:
                return
            if (
                not gewarnt
                and not self.datei.is_file()
                and time.monotonic() - beginn > self.warte_s
            ):
                gewarnt = True
                log.warning(
                    "Transkript %s nach %.0f s nicht da — Limit-Erkennung blind.",
                    self.datei,
                    self.warte_s,
                )
            self.halt.wait(self.takt)


#: Denkstufe der Aufseher-Session, wenn die Konfig ``effort.waechter`` nichts sagt (David 28.09.2026).
EFFORT = "medium"


def effort(konfig: dict[str, Any]) -> str:
    """Denkstufe des Aufsehers aus ``effort.waechter`` — eine Quelle für Aufseher und Takt-Lauf (#402)."""
    stufen = konfig.get("effort") if isinstance(konfig.get("effort"), dict) else {}
    return str(stufen.get("waechter") or EFFORT)


def befehl(
    claude: str,
    modell: str,
    ausweich: str,
    remote_control: bool,
    spec: int,
    prompt: str,
    session_id: str | None = None,
    effort: str = "",
) -> list[str]:
    """Start-Befehl der Aufseher-Session (``effort`` → ``--effort``, Konfig ``effort.waechter``)."""
    cmd = [claude, "--model", modell]
    if effort:
        cmd += ["--effort", effort]
    if ausweich and ausweich != modell:
        cmd += ["--fallback-model", ausweich]
    if remote_control:
        cmd += ["--remote-control", f"Aufseher #{spec}"]
    if session_id:
        cmd += ["--session-id", session_id]
    return [*cmd, prompt]


def resume_befehl(
    claude: str, sid: str, modell: str, effort: str, remote_control: bool, spec: int, text: str
) -> list[str]:
    """Fortsetzung eines Aufseher-Gesprächs mit denselben Flags wie beim Start."""
    cmd = [claude, "--resume", sid, "--model", modell]
    if effort:
        cmd += ["--effort", effort]
    if remote_control:
        cmd += ["--remote-control", f"Aufseher #{spec}"]
    return [*cmd, text]


def _starte(cmd: list[str], cwd: Path) -> subprocess.Popen[bytes]:
    try:
        return subprocess.Popen(cmd, cwd=str(cwd))
    except OSError:
        # Windows: ``claude`` ist ein .cmd-Shim, der nur über die Shell startet.
        return subprocess.Popen(
            " ".join(f'"{c}"' for c in cmd), cwd=str(cwd), shell=True
        )


def _beende(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _log_repo(repo: Path) -> Path | None:
    """Bau-Log-Ziel: Regel des Skills (``TO_SPAWN_LOG_REPO``, fehlt der Ordner → ``None``)."""
    regel = getattr(bau_log, "log_repo", None)
    if callable(regel):
        ziel = regel(repo, versioniert=True)  # schreibt docs/agents/bau_log/ (#257)
        return ziel if isinstance(ziel, Path) else None
    return repo


def _melde_wechsel(repo: Path, spec: int, sid: str, modell: str, ausweich: str, grund: str) -> None:
    """Mail zuerst, dann Zeile ins versionierte Bau-Log — nie ein Grund, den Neustart zu lassen."""
    try:
        melder.melden(
            repo,
            "waechter_ausweich",
            f"Aufseher #{spec} läuft auf Ausweich-Modell",
            f"Aufseher #{spec}: {modell} hat das Limit erreicht ({grund}) — weiter mit {ausweich}.",
            f"waechter_ausweich|{spec}|{sid}",
        )
    except (OSError, ValueError, subprocess.SubprocessError) as fehler:
        log.warning("Aufseher #%s: Mail waechter_ausweich gescheitert: %s", spec, fehler)
    _log_zeile(repo, spec, "waechter_modell", von=modell, nach=ausweich, grund=grund)


def _log_zeile(repo: Path, spec: int, typ: str, **felder: Any) -> None:
    """Eine Zeile ins versionierte Bau-Log — nie ein Grund, den Aufseher zu stoppen."""
    ziel = _log_repo(repo)
    try:
        if ziel is not None:
            bau_log.eintrag_schreiben(ziel, spec, typ, **felder)
            return
        # Versionierte Datei nicht erreichbar (#257: Worktree weg) — die Zeile darf
        # trotzdem nicht verschwinden, also in die unversionierte Laufdatei (F4).
        rueckfall = bau_log.log_rueckfall() or repo
        bau_log.schreibe(rueckfall, spec, typ, **felder)
        log.warning(
            "Aufseher #%s: Zeile %s nur in der Laufdatei unter %s (kein versioniertes Ziel).",
            spec,
            typ,
            rueckfall,
        )
    except (OSError, ValueError) as fehler:
        log.warning(
            "Aufseher #%s: Bau-Log-Zeile %s nicht geschrieben (%s) — Aufseher läuft weiter.",
            spec,
            typ,
            fehler,
        )


def _melde_pause(
    repo: Path,
    spec: int,
    sid: str,
    modell: str,
    ziel: datetime,
    sekunden: float,
    grund: str,
    runde: int,
) -> None:
    """Mail und Bau-Log-Zeile vor der Limit-Pause (#254).

    Die Runde steht im Melde-Schlüssel: sonst entprellt der Melder die zweite Pause
    derselben Session weg und eine Schleife liefe unsichtbar (F6).
    """
    bis = f"{ziel.astimezone():%d.%m. %H:%M}"
    try:
        melder.melden(
            repo,
            "waechter_pause",
            f"Aufseher #{spec} pausiert bis {bis}",
            f"Aufseher #{spec}: {modell} hat das Limit erreicht ({grund}). "
            f"Pause {runde} von {MAX_PAUSEN} bis {bis} ({sekunden / 60:.0f} min), "
            "danach fährt er selbst weiter.",
            f"waechter_pause|{spec}|{sid}|{runde}|{int(ziel.timestamp())}",
        )
    except (OSError, ValueError, subprocess.SubprocessError) as fehler:
        log.warning("Aufseher #%s: Mail waechter_pause gescheitert: %s", spec, fehler)
    _log_zeile(
        repo,
        spec,
        "waechter_pause",
        modell=modell,
        bis=bis,
        sekunden=round(sekunden, 1),
        runde=runde,
        grund=grund,
    )


def _melde_stillstand(
    repo: Path, spec: int, sid: str, modell: str, text: str, grund: str
) -> None:
    """Aufseher bleibt stehen: Mail wie vor #254 — und eine Spur im Bau-Log (F5)."""
    try:
        raus = melder.melden(
            repo,
            "session_tot",
            f"Aufseher #{spec} steht",
            f"Aufseher #{spec} hat auch auf dem Ausweich-Modell {modell} das Limit erreicht: {text}",
            f"session_tot|waechter|{spec}|{sid}|{datetime.now().astimezone():%Y-%m-%d}",
        )
    except (OSError, ValueError, subprocess.SubprocessError) as fehler:
        raus = False
        log.warning("Aufseher #%s: Mail session_tot gescheitert: %s", spec, fehler)
    if not raus:
        log.warning(
            "Aufseher #%s steht (%s) und es ging KEINE Mail raus — nur diese Zeile.",
            spec,
            grund,
        )
    _log_zeile(repo, spec, "blockiert", modell=modell, grund=grund)


def _abloesen(
    repo: Path, spec: int, cwd: Path, kontext: int, grenze: int, laeuft: Callable[[], bool]
) -> None:
    """Aufseher an der Handoff-Grenze über die respawn-Tür ablösen (eigener Faden, #436).

    Exit 0 und die Session läuft noch → Ablöse-Datei (Pfad aus :data:`ABLOESE_ENV`);
    die Abbruch-Bedingung in ``wache.py`` beendet dann die Session und startet den
    Nachfolger. Die Bau-Log-Zeile steht vor der Ablöse-Datei, damit sie das Ende der
    Session sicher überlebt.
    """
    pane = (os.environ.get("TMUX_PANE") or "").strip()
    if not pane:
        zeile = "keine Ablösung: TMUX_PANE fehlt (kein tmux-Fenster, respawn nur auf dem Bau-Server)"
        log.warning("Aufseher #%s: Kontext %s ≥ Grenze %s — %s.", spec, kontext, grenze, zeile)
        _log_zeile(
            repo, spec, "aufseher_abloesung", kontext=kontext, grenze=grenze, exit=None, zeile=zeile
        )
        return
    log.warning(
        "Aufseher #%s: Kontext %s ≥ Grenze %s — Ablösung über respawn (Pane %s).",
        spec,
        kontext,
        grenze,
        pane,
    )
    erg = respawn_aufseher.aufseher_abloesen(cwd, spec, pane)
    ziel = (os.environ.get(ABLOESE_ENV) or "").strip()
    zeile = erg.zeile
    schreiben = erg.exit == 0 and erg.handoff is not None
    if schreiben and not ziel:
        zeile += f" — aber {ABLOESE_ENV} fehlt, kein Nachfolger"
        schreiben = False
    elif schreiben and not laeuft():
        zeile += " — Session schon beendet, kein Nachfolger"
        schreiben = False
    _log_zeile(
        repo, spec, "aufseher_abloesung", kontext=kontext, grenze=grenze, exit=erg.exit, zeile=zeile
    )
    if not schreiben or erg.handoff is None:
        log.warning("Aufseher #%s: %s", spec, zeile)
        return
    try:
        abloesung_schreiben(Path(ziel), erg.handoff, erg.start_prompt)
    except OSError as fehler:
        log.error("Aufseher #%s: Ablöse-Datei %s nicht geschrieben: %s", spec, ziel, fehler)


def _rueckkehr_ab(eintrag: dict[str, Any] | None, puffer: float) -> float:
    """Unix-Zeit, ab der das Haupt-Modell wieder darf: Reset + Puffer, sonst jetzt + 5 h."""
    ziel = reset_zeitpunkt(eintrag or {})
    sekunden = (
        warte_sekunden(ziel, puffer=puffer, hoechstens=float("inf")) if ziel is not None else None
    )
    return time.time() + (RUECKKEHR_VORGABE_S if sekunden is None else sekunden)


def fahre(
    *,
    claude: str,
    spec: int,
    prompt: str,
    modell: str,
    ausweich: str,
    remote_control: bool,
    repo: Path,
    cwd: Path,
    takt: float = TAKT_S,
    abbruch: Callable[[], bool] | None = None,
    session_id: str | None = None,
    puffer: float = RESET_PUFFER_S,
    hoechstens: float = MAX_WARTE_S,
    effort: str = "",
    grenze: int | None = None,
) -> int:
    """Aufseher starten und beaufsichtigen; Rückgabe = Exit-Code der letzten Session.

    ``abbruch`` (optional) wird je Takt gefragt; ``True`` beendet die Session mit Exit 0.
    ``session_id`` (Aufpasser, #236 R1): vorhandenes Gespräch per ``--resume`` fortsetzen
    statt frisch zu starten — nur wenn das Transkript noch da ist, sonst Exit 2.
    Die Gesprächs-ID landet in ``<repo>/.to-spawn/sessions/wache-<S>.json`` (R2).
    Auf dem Ausweich-Modell wartet der Aufseher das Limit aus, wenn die Limit-Zeile
    eine Reset-Uhrzeit nennt (#254): ``puffer`` Sekunden obendrauf, länger als
    ``hoechstens`` wird nie gewartet. Nach dem Limit kehrt der Aufseher aufs Haupt-Modell
    ``modell`` zurück (#436). ``grenze`` (Token, Test-Naht): Handoff-Grenze für die
    erzwungene Ablösung; ``None`` → :func:`handoff_grenze` des laufenden Modells.
    """
    haupt = modell
    rueckkehr_ab: float | None = None  # Unix-Zeit, ab der das Haupt-Modell wieder darf
    abloesung_lief = threading.Event()  # genau eine Ablösung je fahre()
    if session_id:
        sid = session_id
        transkript = transkript_ordner(cwd) / f"{sid}.jsonl"
        if not transkript.is_file():
            log.error(
                "Aufseher #%s --resume %s: Transkript %s fehlt — kein Start.",
                spec,
                sid,
                transkript,
            )
            return 2
        cmd = resume_befehl(
            claude,
            sid,
            modell,
            effort,
            remote_control,
            spec,
            f"Aufpasser: Weiter als Bau-Aufseher Spec #{spec} genau dort, wo du warst — "
            "nächster Tick wie gehabt.",
        )
    else:
        sid = str(uuid.uuid4())
        cmd = befehl(claude, modell, ausweich, remote_control, spec, prompt, session_id=sid, effort=effort)
    sessions_datei.schreiben(repo, f"wache-{spec}", sid, cwd, 1)
    pausen = 0  # Limit-Pausen dieser Session (Obergrenze MAX_PAUSEN, F1)
    while True:
        datei = transkript_ordner(cwd) / f"{sid}.jsonl"
        ab = datei.stat().st_size if datei.is_file() else 0
        proc = _starte(cmd, cwd)
        limit = threading.Event()
        gruende: list[str] = []
        pause: list[tuple[datetime, float]] = []
        auf_ausweich = not ausweich or modell == ausweich
        # Der Rückruf braucht die ganze Limit-Zeile (Reset-Uhrzeit, #254); die Aufsicht
        # legt sie vorher in ``limit_eintrag`` ab, deshalb wird sie zuerst gebaut.
        aufsicht = Aufsicht(
            datei, ab, takt, lambda _text: None, grenze=grenze or handoff_grenze(modell)
        )

        def bei_grenze(kontext: int, _proc: subprocess.Popen[bytes] = proc) -> None:
            if abloesung_lief.is_set():
                return
            abloesung_lief.set()
            threading.Thread(
                target=_abloesen,
                args=(repo, spec, cwd, kontext, aufsicht.grenze or 0, lambda: _proc.poll() is None),
                name="waechter-abloesung",
                daemon=True,
            ).start()

        aufsicht.bei_grenze = bei_grenze

        def bei_limit(
            text: str,
            _proc: subprocess.Popen[bytes] = proc,
            _auf: bool = auf_ausweich,
            _gruende: list[str] = gruende,
            _limit: threading.Event = limit,
            _modell: str = modell,
            _pause: list[tuple[datetime, float]] = pause,
            _aufsicht: Aufsicht = aufsicht,
            _runde: int = pausen + 1,
        ) -> None:
            _gruende.append(text)
            _limit.set()
            log.warning(
                "Aufseher #%s: Nutzungs-Limit erkannt (%s) — %s", spec, _modell, text
            )
            if not _auf:
                _beende(_proc)
                return
            ziel = reset_zeitpunkt(_aufsicht.limit_eintrag or {})
            wartezeit = (
                warte_sekunden(ziel, puffer=puffer, hoechstens=hoechstens)
                if ziel is not None
                else None
            )
            if _runde > MAX_PAUSEN:
                # Limit greift nach jedem Neustart sofort wieder — hier hilft nur ein Mensch (F1).
                _melde_stillstand(
                    repo,
                    spec,
                    sid,
                    _modell,
                    text,
                    f"{MAX_PAUSEN} Pausen in Folge halfen nicht",
                )
                return
            if ziel is None or wartezeit is None:
                # Keine brauchbare Uhrzeit → wie vor #254: melden, Fenster stehen lassen.
                _melde_stillstand(
                    repo, spec, sid, _modell, text, "keine brauchbare Reset-Uhrzeit"
                )
                return
            _pause.append((ziel, wartezeit))
            _beende(_proc)

        aufsicht.bei_limit = bei_limit
        aufsicht.start()
        zurueck = False
        while True:
            try:
                rc = proc.wait(timeout=takt)
                break
            except subprocess.TimeoutExpired:
                if abbruch is not None and abbruch():
                    log.info(
                        "Aufseher #%s: Abbruch-Bedingung erfüllt — Session wird beendet.",
                        spec,
                    )
                    aufsicht.halt.set()
                    _beende(proc)
                    return 0
                if (
                    rueckkehr_ab is not None
                    and modell != haupt
                    and time.time() >= rueckkehr_ab
                ):
                    log.info(
                        "Aufseher #%s: Limit vorbei — zurück von %s auf %s.", spec, modell, haupt
                    )
                    zurueck = True
                    aufsicht.halt.set()
                    _beende(proc)
                    rc = 0
                    break
        aufsicht.halt.set()
        aufsicht.join(timeout=takt + 1)
        if zurueck:
            _log_zeile(repo, spec, "waechter_modell", von=modell, nach=haupt, grund="Limit vorbei")
            cmd = resume_befehl(
                claude,
                sid,
                haupt,
                effort,
                remote_control,
                spec,
                f"Weiter als Bau-Aufseher Spec #{spec}: Das Nutzungs-Limit von {haupt} ist vorbei, "
                f"Modell-Wechsel {modell} → {haupt}. Nächster Tick wie gehabt "
                f"(python scripts/capo.py {spec}).",
            )
            modell = haupt
            rueckkehr_ab = None
            continue
        if not limit.is_set() or (auf_ausweich and not pause):
            return rc

        grund = gruende[0] if gruende else "Nutzungs-Limit"
        if auf_ausweich:
            ziel, wartezeit = pause[0]
            pausen += 1
            _melde_pause(repo, spec, sid, modell, ziel, wartezeit, grund, pausen)
            log.warning(
                "Aufseher #%s: Limit-Pause bis %s (%.0f s) — danach fährt er selbst weiter.",
                spec,
                ziel,
                wartezeit,
            )
            if not _warten(wartezeit, takt, abbruch):
                log.info("Aufseher #%s: Abbruch während der Limit-Pause.", spec)
                return 0
            _log_zeile(
                repo,
                spec,
                "waechter_weiter",
                modell=modell,
                pause_s=round(wartezeit, 1),
                runde=pausen,
            )
            if modell != haupt:
                # Limit ist offen → zurück aufs Haupt-Modell (#436).
                _log_zeile(
                    repo, spec, "waechter_modell", von=modell, nach=haupt, grund="Limit-Pause vorbei"
                )
                modell = haupt
                rueckkehr_ab = None
            cmd = resume_befehl(
                claude,
                sid,
                modell,
                effort,
                remote_control,
                spec,
                f"Weiter als Bau-Aufseher Spec #{spec}: Das Nutzungs-Limit ist seit "
                f"{ziel.astimezone():%H:%M} wieder offen, die Pause ist vorbei. "
                f"Nächster Tick wie gehabt (python scripts/capo.py {spec}); "
                f"docs/agents/bau_log/{spec}.jsonl beim nächsten Handoff-Commit mitnehmen.",
            )
            continue

        _melde_wechsel(repo, spec, sid, modell, ausweich, grund)
        weiter = (
            f"Weiter als Bau-Aufseher Spec #{spec}: Modell-Wechsel {modell} → {ausweich} wegen Nutzungs-Limit. "
            f"Nächster Tick wie gehabt (python scripts/capo.py {spec}); "
            f"docs/agents/bau_log/{spec}.jsonl beim nächsten Handoff-Commit mitnehmen."
        )
        rueckkehr_ab = _rueckkehr_ab(aufsicht.limit_eintrag, puffer)
        modell = ausweich
        cmd = resume_befehl(claude, sid, ausweich, effort, remote_control, spec, weiter)
