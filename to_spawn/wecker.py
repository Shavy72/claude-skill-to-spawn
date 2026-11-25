"""Externer Wecker für Leit-/Aufsichts-Sessions (#287).

Problem: Scheitert ein sitzungsinterner Weckruf (``/loop``, ``ScheduleWakeup``) am
Wochen- oder Sitzungs-Limit, plant Claude Code keinen neuen Weckruf. Nach dem Reset
des Limits bleibt die Leit-/Aufsichts-Session still stehen, bis jemand von Hand
anstößt.

Lösung: Der Weckruf kommt von **außerhalb** der Session über den System-Taktgeber
(Linux: Cron über ``crontab`` + tmux; Windows: geplante Aufgabe über ``schtasks`` mit
``pythonw.exe`` + Session-Anstoß über ``claude --resume`` — jeweils im eigenen
Werkzeugkasten, siehe Lastenheft L6). Eine schmale Schnittstelle (:class:`SystemWecker`)
trennt die fachliche Weck- und Limit-Logik vom System-Werkzeugkasten:

1. Registrierte Weckaufträge liegen atomar in ``<zustand>/auftraege.json``.
2. Jeder Durchgang (:func:`durchgang`, ausgelöst per Cron / geplanter Aufgabe alle ``M``
   Minuten oder als Sicherheitsnetz über :mod:`to_spawn.aufpasser`) prüft fällige
   Aufträge unter der Lauf-Sperre ``<zustand>/lock``.
3. Trifft ein Weckruf auf ein Limit (z. B. ``95% of your weekly limit · resets 11pm``
   oder ``You've hit your … limit``), steht der gescheiterte Weckruf mit Grund im Log
   (``wecker.jsonl`` und ``wecker.log``), und der Wecker merkt sich den Reset-Zeitpunkt
   plus Puffer (:func:`waechter_lauf._aus_text`).
4. Während des aktiven Limit-Fensters wird nicht gespammt; spätestens ``M`` Minuten
   nach dem Reset sendet der externe Wecker den Weckruf erneut — auch wenn die alte
   Limit-Meldung noch im Verlauf steht — und die Session läuft ohne Eingriff weiter.

Wochen-Limit PC ↔ Bau-Server (#287 Abnahme 4, L7):
Windows-PC und Linux-Bau-Server nutzen dasselbe Claude.ai-Konto und dieselbe
Organisation (``authMethod: claude.ai``, ``apiProvider: firstParty``, kein getrennter
``ANTHROPIC_API_KEY``). Sie teilen daher **dasselbe** 5-Stunden- und Wochen-Limit —
parallele Sessions über beide Maschinen schöpfen aus einem gemeinsamen Kontingent.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import itertools
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from . import (
    aufpasser,
    config,
    konsole,
    manifest,
    prozessbaum,
    sitzung_env,
    sperre,
    starter,
    tmux_aufruf,
    waechter_lauf,
    werkzeug,
)

log = logging.getLogger("to_spawn.wecker")

SKILL = Path(__file__).resolve().parent.parent

#: Vorgabe-Abstand eines registrierten Weckauftrags in Minuten.
STANDARD_TAKT_MIN = 15
#: Vorgabe-Takt der Linux-Cron-Zeile in Minuten (so läuft die Session ≤ M min nach Reset weiter).
CRON_TAKT_MIN = 5
#: Marke der eigenen Cron-Zeile (idempotentes Ersetzen).
CRON_MARKE = "# to-spawn-wecker"
#: Log-Datei für Cron-Ausgaben neben ``auftraege.json``.
CRON_LOG = "cron.out"
#: Kurze Pause nach dem Tippen, um sofortige Limit-Antworten im Pane zu erkennen (s).
NACHPRUEF_S = 3.0
#: Pause zwischen Einfügen (paste-buffer) und Enter (s).
TIPP_PAUSE_S = 0.15
#: Puffer nach dem Reset-Zeitpunkt (eine Quelle: ``waechter_lauf.RESET_PUFFER_S``).
RESET_PUFFER_S = waechter_lauf.RESET_PUFFER_S
#: Obergrenze für einen gemerkten Limit-Reset (Wochen-Limit ≤ 8 Tage).
MAX_LIMIT_WARTE_S = 8 * 24 * 3600.0
#: Erlaubte Namen für Weckaufträge.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
#: Prozent-Limit-Meldung aus dem echten Wochen-Limit-Beleg (#287).
_PROZENT_LIMIT = re.compile(r"\b\d+%\s+of\s+your\s+.*limit\b", re.IGNORECASE)
#: Weitere Limit-Marker im Pane-Text.
_WEITERE_LIMIT_MARKER = ("usage limit", "limit reset")

_PUFFER_NR = itertools.count(1)
_TIPP_SPERRE = threading.Lock()

EreignisArt = Literal[
    "geweckt",
    "gescheitert",
    "wartet_auf_reset",
    "uebersprungen",
    "nicht_faellig",
    "trocken",
]


@dataclass
class Weckauftrag:
    """Ein registrierter externer Weckauftrag für eine Leit-/Aufsichts-Session."""

    name: str
    ziel: str
    text: str
    takt_min: int = STANDARD_TAKT_MIN
    tmux_socket: str | None = None
    zuletzt_geweckt: float = 0.0
    zuletzt_gescheitert: float = 0.0
    letzter_grund: str = ""
    warten_bis: float = 0.0
    limit_hash: str = ""
    angelegt: float = 0.0

    @classmethod
    def aus_dict(cls, daten: dict[str, Any]) -> Weckauftrag:
        return cls(
            name=str(daten["name"]),
            ziel=str(daten["ziel"]),
            text=str(daten["text"]),
            takt_min=max(1, int(daten.get("takt_min") or STANDARD_TAKT_MIN)),
            tmux_socket=str(daten["tmux_socket"]) if daten.get("tmux_socket") else None,
            zuletzt_geweckt=float(daten.get("zuletzt_geweckt") or 0.0),
            zuletzt_gescheitert=float(daten.get("zuletzt_gescheitert") or 0.0),
            letzter_grund=str(daten.get("letzter_grund") or ""),
            warten_bis=float(daten.get("warten_bis") or 0.0),
            limit_hash=str(daten.get("limit_hash") or ""),
            angelegt=float(daten.get("angelegt") or 0.0),
        )


@dataclass(frozen=True)
class PaneStand:
    """Gelesener Zustand des Ziel-Panes (Rückgabe des System-Kastens)."""

    existiert: bool
    session_laeuft: bool = False
    farbig: str = ""
    text: str = ""
    status: str | None = None
    fehler: str = ""


@dataclass(frozen=True)
class Ergebnis:
    """Ausgang eines Weck-Versuchs für genau einen Auftrag."""

    name: str
    ziel: str
    ereignis: EreignisArt
    grund: str = ""
    warten_bis: float = 0.0


class SystemWecker(Protocol):
    """Schmale Schnittstelle der System-Werkzeugkästen (L6, #287)."""

    def pane_lesen(self, ziel: str, *, tmux_socket: str | None = None) -> PaneStand: ...

    def tippen(self, ziel: str, text: str, *, tmux_socket: str | None = None) -> None: ...

    def cron_einrichten(
        self,
        zustand: Path,
        *,
        takt_min: int = CRON_TAKT_MIN,
        trocken: bool = False,
        python: str = sys.executable,
    ) -> str: ...


# --- Pfade, Zustand, Log --------------------------------------------------------


def zustand_standard() -> Path:
    """Standard-Zustandsordner; überschreibbar über ``TO_SPAWN_WECKER_ORDNER``."""
    roh = (os.environ.get("TO_SPAWN_WECKER_ORDNER") or "").strip()
    if roh:
        return Path(roh).expanduser()
    return config.heim_ordner() / ".local" / "state" / "to-spawn" / "wecker"


def auftraege_pfad(zustand: Path) -> Path:
    return zustand / "auftraege.json"


def log_jsonl_pfad(zustand: Path) -> Path:
    return zustand / "wecker.jsonl"


def _jetzt_iso(zeit: float | None = None) -> str:
    wert = time.time() if zeit is None else zeit
    return datetime.fromtimestamp(wert, tz=UTC).astimezone().isoformat(timespec="seconds")


def _log_ereignis(zustand: Path, erg: Ergebnis, *, jetzt: float, trocken: bool) -> None:
    """Jeder gescheiterte/ausgeführte Weckruf landet strukturiert in ``wecker.jsonl``."""
    if erg.ereignis == "gescheitert":
        log.warning("Wecker %s (%s): gescheitert — %s", erg.name, erg.ziel, erg.grund)
    elif erg.ereignis == "geweckt":
        log.info("Wecker %s (%s): geweckt", erg.name, erg.ziel)
    elif erg.ereignis == "wartet_auf_reset":
        log.info("Wecker %s (%s): wartet auf Reset — %s", erg.name, erg.ziel, erg.grund)
    else:
        log.debug("Wecker %s (%s): %s (%s)", erg.name, erg.ziel, erg.ereignis, erg.grund)
    if trocken or erg.ereignis == "nicht_faellig":
        return
    zeile: dict[str, Any] = {
        "zeit": _jetzt_iso(jetzt),
        "name": erg.name,
        "ziel": erg.ziel,
        "ereignis": erg.ereignis,
        "grund": erg.grund,
    }
    if erg.warten_bis > 0:
        zeile["warten_bis"] = _jetzt_iso(erg.warten_bis)
    try:
        zustand.mkdir(parents=True, exist_ok=True)
        with log_jsonl_pfad(zustand).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    except OSError as fehler:
        log.warning("Wecker-Log %s nicht schreibbar: %s", log_jsonl_pfad(zustand), fehler)


def lade_auftraege(zustand: Path) -> dict[str, Weckauftrag]:
    """Liest alle registrierten Weckaufträge; fehlende Datei ⇒ leeres Dict."""
    datei = auftraege_pfad(zustand)
    if not datei.is_file():
        return {}
    roh = json.loads(datei.read_text(encoding="utf-8"))
    if not isinstance(roh, dict):
        raise TypeError(f"Wecker-Datei {datei} hat unerwartetes Format")
    aus: dict[str, Weckauftrag] = {}
    for name, eintrag in roh.items():
        if isinstance(eintrag, dict):
            daten = {"name": name, **eintrag}
            aus[name] = Weckauftrag.aus_dict(daten)
    return aus


def speichere_auftraege(zustand: Path, auftraege: dict[str, Weckauftrag]) -> None:
    """Schreibt ``auftraege.json`` atomar über :func:`manifest.schreibe_atomar`."""
    zustand.mkdir(parents=True, exist_ok=True)
    daten = {name: asdict(auftrag) for name, auftrag in sorted(auftraege.items())}
    manifest.schreibe_atomar(auftraege_pfad(zustand), daten)


def auftrag_setzen(
    zustand: Path,
    *,
    name: str,
    ziel: str,
    text: str,
    takt_min: int = STANDARD_TAKT_MIN,
    tmux_socket: str | None = None,
    jetzt: float | None = None,
) -> Weckauftrag:
    """Weckauftrag anlegen oder aktualisieren."""
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"Ungültiger Wecker-Name {name!r} (erlaubt: Buchstaben, Ziffern, _ . -)")
    if not ziel.strip():
        raise ValueError("Ziel darf nicht leer sein")
    if not text.strip():
        raise ValueError("Weckruf-Text darf nicht leer sein")
    if takt_min < 1:
        raise ValueError("takt_min muss mindestens 1 sein")
    jetzt_s = time.time() if jetzt is None else jetzt
    zustand.mkdir(parents=True, exist_ok=True)
    with _sperre_oder_fehler(zustand):
        alle = lade_auftraege(zustand)
        vorher = alle.get(name)
        auftrag = Weckauftrag(
            name=name,
            ziel=ziel.strip(),
            text=text.strip(),
            takt_min=int(takt_min),
            tmux_socket=tmux_socket or None,
            zuletzt_geweckt=vorher.zuletzt_geweckt if vorher else 0.0,
            zuletzt_gescheitert=vorher.zuletzt_gescheitert if vorher else 0.0,
            letzter_grund=vorher.letzter_grund if vorher else "",
            warten_bis=vorher.warten_bis if vorher else 0.0,
            limit_hash=vorher.limit_hash if vorher else "",
            angelegt=vorher.angelegt if vorher and vorher.angelegt else jetzt_s,
        )
        alle[name] = auftrag
        speichere_auftraege(zustand, alle)
    return auftrag


def auftrag_loeschen(zustand: Path, name: str) -> bool:
    """Weckauftrag entfernen; ``True`` = entfernt, ``False`` = nicht vorhanden."""
    if not auftraege_pfad(zustand).is_file():
        return False
    with _sperre_oder_fehler(zustand):
        alle = lade_auftraege(zustand)
        if name not in alle:
            return False
        del alle[name]
        speichere_auftraege(zustand, alle)
    return True


def _sperre_oder_fehler(zustand: Path) -> ExitStack:
    stapel = ExitStack()
    gehalten = stapel.enter_context(sperre.halte(zustand / "lock"))
    if not gehalten:
        stapel.close()
        raise RuntimeError(f"Wecker-Sperre {zustand / 'lock'} ist belegt")
    return stapel


# --- Limit-Erkennung (eine Quelle mit waechter_lauf) ----------------------------


def limit_zeile(text: str) -> str | None:
    """Jüngste Limit-Meldung im Pane-Text (unten zuerst); ``None`` = keine gefunden.

    Nutzt :data:`waechter_lauf._LIMIT_TEXT` (``hit/reached your … limit``) sowie die
    Wochen-Limit-Prozentanzeige (``95% of your weekly limit · resets 11pm``, #287).
    """
    for zeile in reversed(text.splitlines()):
        sauber = zeile.strip()
        if not sauber:
            continue
        klein = sauber.lower()
        if (
            waechter_lauf._LIMIT_TEXT.search(sauber)
            or _PROZENT_LIMIT.search(sauber)
            or any(m in klein for m in _WEITERE_LIMIT_MARKER)
        ):
            return sauber
    return None


def reset_zeitpunkt_aus_bild(zeile: str, jetzt: datetime | None = None) -> datetime | None:
    """Reset-Zeitpunkt aus einer Limit-Zeile — einzige Quelle: :func:`waechter_lauf._aus_text`."""
    basis = jetzt or datetime.now().astimezone()
    return waechter_lauf._aus_text(zeile, basis)


def neue_limit_zeile(vorher: str, nachher: str, wecktext: str) -> str | None:
    """Prüft, ob NACH dem eben gesendeten Weckruf eine neue Limit-Meldung erschien.

    Unterscheidet zuverlässig zwischen einer alten Limit-Meldung im Scrollback (vor dem
    Reset, auch wenn das Pane nach oben scrollt oder ``wecktext`` umbricht) und einer
    frischen Limit-Antwort auf genau diesen Weckruf.
    """
    vorher_kern = aufpasser.ohne_statuszeile(vorher)
    nachher_kern = aufpasser.ohne_statuszeile(nachher)
    erste_zeile = wecktext.splitlines()[0].strip()
    if erste_zeile and erste_zeile in nachher_kern:
        danach = nachher_kern.rsplit(erste_zeile, 1)[1]
        return limit_zeile(danach)
    if nachher_kern == vorher_kern:
        return None
    alt_zeilen = vorher_kern.splitlines()
    neu_zeilen = nachher_kern.splitlines()
    hinzugekommen: list[str] = []
    abgleich = difflib.SequenceMatcher(None, alt_zeilen, neu_zeilen, autojunk=False)
    for art, _i1, _i2, j1, j2 in abgleich.get_opcodes():
        if art in ("insert", "replace"):
            hinzugekommen.extend(neu_zeilen[j1:j2])
    return limit_zeile("\n".join(hinzugekommen))


def _warten_bis_aus_zeile(zeile: str, jetzt: float, puffer_s: float) -> float:
    """Berechnet die Unix-Zeit nach Reset + Puffer; ``0.0`` wenn kein brauchbarer Reset."""
    jetzt_dt = datetime.fromtimestamp(jetzt, tz=UTC).astimezone()
    ziel = reset_zeitpunkt_aus_bild(zeile, jetzt_dt)
    if ziel is None:
        return 0.0
    rest = (ziel - jetzt_dt).total_seconds()
    if rest < -waechter_lauf.RESET_TOLERANZ_S:
        return 0.0
    warte = max(0.0, rest) + puffer_s
    if warte > MAX_LIMIT_WARTE_S:
        return 0.0
    return jetzt + warte


# --- Cron-Helfer (Linux) --------------------------------------------------------


def cron_ausdruck(takt_min: int) -> str:
    """Cron-Zeitausdruck für ``takt_min`` Minuten (1–59 → ``*/M``, 60 → stündlich)."""
    if takt_min < 1 or takt_min > 60:
        raise ValueError(f"Cron-Takt muss zwischen 1 und 60 Minuten liegen (erhalten: {takt_min})")
    if takt_min == 60:
        return "0 * * * *"
    if takt_min == 1:
        return "* * * * *"
    return f"*/{takt_min} * * * *"


def cron_befehl(
    zustand: Path,
    *,
    takt_min: int = CRON_TAKT_MIN,
    python: str = sys.executable,
) -> str:
    """Vollständige Cron-Zeile (ohne Marke); alle Pfade sauber gequotet."""
    q = shlex.quote
    log_pfad = zustand / CRON_LOG
    return (
        f"{cron_ausdruck(takt_min)} {q(python)} {q(str(SKILL / 'to_spawn.py'))} "
        f"wecker lauf --zustand {q(str(zustand))} >> {q(str(log_pfad))} 2>&1"
    )


def cron_zeilen(zeilen: list[str], befehl: str) -> list[str]:
    """Crontab mit genau einer Wecker-Zeile (an alter Stelle oder am Ende, idempotent)."""
    zeile = f"{befehl}  {CRON_MARKE}"
    stellen = [i for i, z in enumerate(zeilen) if CRON_MARKE in z]
    neu = [z for z in zeilen if CRON_MARKE not in z]
    neu.insert(stellen[0] if stellen else len(neu), zeile)
    return neu


# --- System-Werkzeugkästen (L6) -------------------------------------------------


class LinuxTmuxWecker:
    """Linux-Werkzeugkasten: tmux-Pane lesen/tippen + ``crontab`` (#287)."""

    def _tmux(
        self,
        *args: str,
        tmux_socket: str | None = None,
        eingabe: str | None = None,
    ) -> str:
        befehl = ("-L", tmux_socket, *args) if tmux_socket else args
        return tmux_aufruf.aufrufen(*befehl, eingabe=eingabe)

    def pane_lesen(self, ziel: str, *, tmux_socket: str | None = None) -> PaneStand:
        try:
            pid_roh = self._tmux("display-message", "-p", "-t", ziel, "#{pane_pid}", tmux_socket=tmux_socket)
        except RuntimeError as fehler:
            return PaneStand(existiert=False, fehler=str(fehler))
        pid_text = pid_roh.strip().splitlines()[0] if pid_roh.strip() else ""
        pane_pid = int(pid_text) if pid_text.isdigit() else 0
        laeuft = not aufpasser.session_beendet(pane_pid)
        try:
            farbig = self._tmux("capture-pane", "-e", "-p", "-t", ziel, tmux_socket=tmux_socket)
        except RuntimeError as fehler:
            return PaneStand(existiert=False, fehler=str(fehler))
        return PaneStand(
            existiert=True,
            session_laeuft=laeuft,
            farbig=farbig,
            text=aufpasser.ohne_farben(farbig),
        )

    def tippen(self, ziel: str, text: str, *, tmux_socket: str | None = None) -> None:
        puffer = f"wecker-{os.getpid()}-{next(_PUFFER_NR)}"
        with _TIPP_SPERRE:
            self._tmux("load-buffer", "-b", puffer, "-", tmux_socket=tmux_socket, eingabe=text)
            try:
                self._tmux("paste-buffer", "-p", "-d", "-b", puffer, "-t", ziel, tmux_socket=tmux_socket)
            except tmux_aufruf.TmuxFehler:
                try:
                    self._tmux("delete-buffer", "-b", puffer, tmux_socket=tmux_socket)
                except tmux_aufruf.TmuxFehler:
                    pass
                raise
            time.sleep(TIPP_PAUSE_S)
            try:
                self._tmux("send-keys", "-t", ziel, "Enter", tmux_socket=tmux_socket)
            except tmux_aufruf.TmuxFehler:
                try:
                    self._tmux("send-keys", "-t", ziel, "C-u", tmux_socket=tmux_socket)
                except tmux_aufruf.TmuxFehler:
                    pass
                raise

    def cron_einrichten(
        self,
        zustand: Path,
        *,
        takt_min: int = CRON_TAKT_MIN,
        trocken: bool = False,
        python: str = sys.executable,
    ) -> str:
        befehl = cron_befehl(zustand, takt_min=takt_min, python=python)
        alt = aufpasser._crontab("-l")
        zeilen = alt.stdout.splitlines() if alt.returncode == 0 else []
        neu = cron_zeilen(zeilen, befehl)
        zeile = next(z for z in neu if CRON_MARKE in z)
        if neu == zeilen:
            return f"Wecker-Cron unverändert: {zeile}"
        if trocken:
            return f"[trocken] Wecker-Cron würde eingerichtet: {zeile}"
        zustand.mkdir(parents=True, exist_ok=True)
        stempel = time.strftime("%Y%m%d-%H%M%S")
        (zustand / f"crontab.vorher.{stempel}").write_text(alt.stdout, encoding="utf-8")
        ergebnis = aufpasser._crontab("-", eingabe="\n".join(neu) + "\n")
        if ergebnis.returncode != 0:
            raise RuntimeError(f"crontab schreiben: {ergebnis.stderr.strip()[:300]}")
        return f"Wecker-Cron eingerichtet: {zeile}"


#: Name der geplanten Aufgabe in der Windows-Aufgabenplanung (#287).
WINDOWS_AUFGABE_NAME = "to-spawn Wecker"
#: Zeitlimit je ``schtasks``-Aufruf in Sekunden.
SCHTASKS_ZEITLIMIT_S = 30
#: Höchste gelesene Schwanzlänge eines Session-Transkripts unter Windows (Bytes).
_TRANSKRIPT_TAIL_BYTES = 64 * 1024
_PROMPT_RAHMEN = "─" * 30
_LEER_PROMPT = f"{_PROMPT_RAHMEN}\n❯ \n{_PROMPT_RAHMEN}\n"
_ISO_INTERVALL_RE = re.compile(r"^PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?$", re.IGNORECASE)
_AKTIV_STATUS = frozenset({"busy", "shell"})


def windows_aufgabe_intervall(takt_min: int) -> str:
    """ISO-8601-Intervall für Windows-Aufgabenplanung (1–59 → ``PT<M>M``, 60 → ``PT1H``)."""
    if takt_min < 1 or takt_min > 60:
        raise ValueError(f"Aufgaben-Takt muss zwischen 1 und 60 Minuten liegen (erhalten: {takt_min})")
    if takt_min == 60:
        return "PT1H"
    return f"PT{takt_min}M"


def windows_aufgabe_argumente(zustand: Path) -> str:
    """Argumente für ``pythonw.exe`` in der Windows-Aufgabenplanung."""
    return f'"{SKILL / "to_spawn.py"}" wecker lauf --zustand "{zustand}"'


def _windows_intervall_min(intervall: str) -> int | None:
    treffer = _ISO_INTERVALL_RE.fullmatch(intervall.strip())
    if not treffer or (treffer["h"] is None and treffer["m"] is None):
        return None
    return int(treffer["h"] or 0) * 60 + int(treffer["m"] or 0)


def windows_aufgabe_gleich(xml: str | None, pythonw: str, argumente: str, takt_min: int) -> bool:
    """Prüft, ob die vorhandene ``schtasks /Query /XML``-Aufgabe Befehl, Argumente und Takt trifft."""
    # Regex statt XML-Parser: schtasks kündigt UTF-16 an, liefert über die Pipe aber Text.
    if not xml:
        return False

    def feld(name: str) -> str:
        treffer = re.search(rf"<{name}>(.*?)</{name}>", xml, re.DOTALL)
        return html.unescape(treffer.group(1)).strip() if treffer else ""

    return (
        feld("Command").strip('"').lower() == pythonw.lower()
        and feld("Arguments") == argumente
        and _windows_intervall_min(feld("Interval")) == takt_min
    )


@dataclass(frozen=True)
class _WindowsZiel:
    """Aufgelöste Windows-Session für :class:`WindowsWecker`."""

    session_id: str
    cwd: Path | None
    transkript: Path | None
    pid: int | None
    status: str | None
    schluessel: str


class WindowsWecker:
    """Windows-Werkzeugkasten: geplante Aufgabe (``schtasks``) + Session-Anstoß (#287, L6).

    Unter Windows gibt es kein tmux; statt in ein Terminal-Fenster zu tippen, liest der
    Kasten den Zustand aus ``~/.claude/sessions/<pid>.json`` und dem Session-Transkript
    (``~/.claude/projects/<slug>/<session-id>.jsonl``) und stößt die Session von außen
    fensterlos über ``claude -p --no-chrome --resume <session-id> <weckruf>`` an
    (durch die Start-Tür :func:`to_spawn.starter.starte` mit ``CREATE_NO_WINDOW |
    CREATE_NEW_PROCESS_GROUP`` und bereinigter Umgebung :func:`sitzung_env.ohne_sitzung`).
    """

    def __init__(
        self,
        *,
        heim: Path | None = None,
        zustand: Path | None = None,
        aufgabe_name: str = WINDOWS_AUFGABE_NAME,
    ) -> None:
        self._heim = heim
        self._zustand = zustand
        self.aufgabe_name = aufgabe_name
        self._prozesse: dict[str, subprocess.Popen[Any]] = {}

    def _heim_ordner(self) -> Path:
        return self._heim if self._heim is not None else config.heim_ordner()

    def _zustand_ordner(self) -> Path:
        return self._zustand if self._zustand is not None else zustand_standard()

    @staticmethod
    def _norm_status(wert: object) -> str | None:
        if not isinstance(wert, str):
            return None
        klein = wert.strip().lower()
        if klein in _AKTIV_STATUS:
            return "busy"
        if klein == "idle":
            return "idle"
        return None

    def _live_sessions(self) -> list[dict[str, Any]]:
        ordner = self._heim_ordner() / ".claude" / "sessions"
        if not ordner.is_dir():
            return []
        treffer: list[tuple[str, float, dict[str, Any]]] = []
        for datei in ordner.glob("*.json"):
            try:
                roh = json.loads(datei.read_text(encoding="utf-8"))
                mtime = datei.stat().st_mtime
            except (OSError, ValueError):
                continue
            if not isinstance(roh, dict):
                continue
            sid = roh.get("sessionId")
            if not isinstance(sid, str) or not sid.strip():
                continue
            try:
                pid = int(roh.get("pid") or datei.stem)
            except (TypeError, ValueError):
                continue
            if pid <= 0 or not prozessbaum.lebt(pid):
                continue
            eintrag = dict(roh)
            eintrag["pid"] = pid
            treffer.append((str(roh.get("updatedAt") or ""), mtime, eintrag))
        treffer.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [e for _, _, e in treffer]

    def _transkript_finden(self, session_id: str, cwd: Path | None) -> Path | None:
        heim = self._heim_ordner()
        if cwd is not None:
            kandidat = waechter_lauf.transkript_ordner(cwd, heim=heim) / f"{session_id}.jsonl"
            if kandidat.is_file():
                return kandidat
        projekte = heim / ".claude" / "projects"
        if projekte.is_dir():
            kandidaten = list(projekte.glob(f"*/{session_id}.jsonl"))
            if kandidaten:
                return max(kandidaten, key=lambda p: p.stat().st_mtime)
        if cwd is not None:
            return waechter_lauf.transkript_ordner(cwd, heim=heim) / f"{session_id}.jsonl"
        return None

    @staticmethod
    def _cwd_aus_transkript(pfad: Path) -> Path | None:
        try:
            with pfad.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                groesse = fh.tell()
                fh.seek(max(0, groesse - _TRANSKRIPT_TAIL_BYTES))
                roh = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return None
        for zeile in reversed(roh.splitlines()):
            zeile = zeile.strip()
            if not zeile:
                continue
            try:
                eintrag = json.loads(zeile)
            except ValueError:
                continue
            cwd = eintrag.get("cwd") if isinstance(eintrag, dict) else None
            if isinstance(cwd, str) and cwd.strip():
                return Path(cwd)
        return None

    def _ziel_aufloesen(self, ziel: str) -> _WindowsZiel | None:
        roh = ziel.strip()
        if not roh:
            return None
        live = self._live_sessions()

        def bauen(sid: str, cwd: Path | None, pid: int | None, status: str | None) -> _WindowsZiel:
            for eintrag in live:
                if eintrag.get("sessionId") == sid:
                    pid = int(eintrag["pid"])
                    status = self._norm_status(eintrag.get("status"))
                    if cwd is None and isinstance(eintrag.get("cwd"), str) and eintrag["cwd"].strip():
                        cwd = Path(eintrag["cwd"])
                    break
            transkript = self._transkript_finden(sid, cwd)
            if cwd is None and transkript is not None and transkript.is_file():
                cwd = self._cwd_aus_transkript(transkript)
            schluessel = hashlib.sha1(sid.encode("utf-8")).hexdigest()[:16]
            return _WindowsZiel(
                session_id=sid,
                cwd=cwd,
                transkript=transkript,
                pid=pid,
                status=status,
                schluessel=schluessel,
            )

        # 1) Pfad zu einer Sessions-JSON (z. B. .to-spawn/sessions/wache-<S>.json)
        pfad = Path(roh).expanduser()
        if pfad.suffix.lower() == ".json" and pfad.is_file():
            try:
                daten = json.loads(pfad.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            if not isinstance(daten, dict):
                return None
            sid = daten.get("session_id") or daten.get("sessionId")
            if not isinstance(sid, str) or not sid.strip():
                return None
            cwd = Path(daten["cwd"]) if isinstance(daten.get("cwd"), str) and daten["cwd"].strip() else None
            pid_roh = daten.get("pid")
            pid = int(pid_roh) if isinstance(pid_roh, int) and pid_roh > 0 and prozessbaum.lebt(pid_roh) else None
            status = self._norm_status(daten.get("status")) if pid is not None else None
            return bauen(sid.strip(), cwd, pid, status)

        # 2) PID (``pid:<N>`` oder reine Ziffernfolge)
        pid_text = roh[4:].strip() if roh.lower().startswith("pid:") else (roh if roh.isdigit() else "")
        if pid_text.isdigit():
            pid_val = int(pid_text)
            datei = self._heim_ordner() / ".claude" / "sessions" / f"{pid_val}.json"
            if not datei.is_file():
                return None
            try:
                daten = json.loads(datei.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            sid = daten.get("sessionId") if isinstance(daten, dict) else None
            if not isinstance(sid, str) or not sid.strip():
                return None
            lebt = prozessbaum.lebt(pid_val)
            cwd = Path(daten["cwd"]) if isinstance(daten.get("cwd"), str) and daten["cwd"].strip() else None
            status = self._norm_status(daten.get("status")) if lebt else None
            return bauen(sid.strip(), cwd, pid_val if lebt else None, status)

        # 3) Explizite Session-ID (``session:<id>``)
        if roh.lower().startswith("session:"):
            sid = roh.split(":", 1)[1].strip()
            if not sid:
                return None
            if any(e.get("sessionId") == sid for e in live) or self._transkript_finden(sid, None) is not None:
                return bauen(sid, None, None, None)
            return None

        # 4) Lebende Session über Name oder Session-ID
        klein = roh.lower()
        for eintrag in live:
            sid = str(eintrag["sessionId"])
            name = str(eintrag.get("name") or "").strip().lower()
            if sid == roh or (name and name == klein):
                cwd = Path(eintrag["cwd"]) if isinstance(eintrag.get("cwd"), str) and eintrag["cwd"].strip() else None
                return bauen(sid, cwd, int(eintrag["pid"]), self._norm_status(eintrag.get("status")))

        # 5) Session-ID direkt über vorhandenes Transkript unter ``~/.claude/projects/``
        if self._transkript_finden(roh, None) is not None:
            return bauen(roh, None, None, None)
        return None

    def _laeufer_pfad(self, schluessel: str) -> Path:
        return self._zustand_ordner() / f"win-laeufer-{schluessel}.json"

    def _laeufer_lesen(self, schluessel: str) -> dict[str, Any] | None:
        pfad = self._laeufer_pfad(schluessel)
        if not pfad.is_file():
            return None
        try:
            roh = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return roh if isinstance(roh, dict) else None

    def _laeufer_aktiv(self, schluessel: str, laeufer: dict[str, Any] | None) -> bool:
        proc = self._prozesse.get(schluessel)
        if proc is not None:
            if proc.poll() is None:
                return True
            self._prozesse.pop(schluessel, None)
        if not laeufer:
            return False
        pid = laeufer.get("pid")
        erwartet_start = laeufer.get("startzeit")
        start_int = erwartet_start if isinstance(erwartet_start, int) else None
        if not isinstance(pid, int) or pid <= 0 or not prozessbaum.aktiv(pid, erwartet_start=start_int):
            return False
        start_ts = laeufer.get("zeit")
        if isinstance(start_ts, (int, float)):
            alter = prozessbaum.alter_s(pid)
            if alter is not None and (time.time() - alter) > float(start_ts) + 5.0:
                return False
        return True

    @staticmethod
    def _transkript_zeilen(pfad: Path | None) -> list[str]:
        if pfad is None or not pfad.is_file():
            return []
        try:
            with pfad.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                groesse = fh.tell()
                start = max(0, groesse - _TRANSKRIPT_TAIL_BYTES)
                fh.seek(start)
                roh = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        rohe_zeilen = roh.splitlines()
        if start > 0 and rohe_zeilen:
            rohe_zeilen = rohe_zeilen[1:]  # abgeschnittene erste Zeile überspringen
        aus: list[str] = []
        for zeile in rohe_zeilen:
            zeile = zeile.strip()
            if not zeile:
                continue
            try:
                eintrag = json.loads(zeile)
            except ValueError:
                continue
            if not isinstance(eintrag, dict):
                continue
            art = eintrag.get("type")
            inhalt = waechter_lauf._texte(eintrag).strip()
            if not inhalt:
                continue
            if art == "user":
                teile = inhalt.splitlines()
                aus.append(f"❯ {teile[0]}")
                aus.extend(teile[1:])
            elif art in ("assistant", "system"):
                if waechter_lauf.ist_limit_zeile(eintrag):
                    quota = waechter_lauf._aus_quota(eintrag)
                    if quota is not None and waechter_lauf._aus_text(inhalt, quota) is None:
                        uhr = quota.strftime("%I:%M%p").lstrip("0").lower()
                        inhalt = f"{inhalt} · resets {uhr}"
                aus.extend(inhalt.splitlines())
        return aus

    def pane_lesen(self, ziel: str, *, tmux_socket: str | None = None) -> PaneStand:
        info = self._ziel_aufloesen(ziel)
        if info is None:
            return PaneStand(existiert=False, fehler=f"Windows-Session nicht gefunden: {ziel}")
        laeufer = self._laeufer_lesen(info.schluessel)
        laeufer_laeuft = self._laeufer_aktiv(info.schluessel, laeufer)
        transkript_da = bool(info.transkript is not None and info.transkript.is_file())
        session_laeuft = info.pid is not None or laeufer_laeuft or transkript_da
        status = "busy" if (info.status == "busy" or laeufer_laeuft) else info.status
        zeilen = self._transkript_zeilen(info.transkript)
        if laeufer and not laeufer_laeuft:
            ausgabe_str = str(laeufer.get("ausgabe") or "").strip()
            ausgabe_pfad = Path(ausgabe_str) if ausgabe_str else None
            if ausgabe_pfad is not None and ausgabe_pfad.is_file():
                try:
                    ausgabe = ausgabe_pfad.read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    ausgabe = ""
                if ausgabe and limit_zeile(ausgabe) is not None:
                    wecktext = str(laeufer.get("text") or "").strip()
                    erste_zeile = wecktext.splitlines()[0].strip() if wecktext else ""
                    bisher = "\n".join(zeilen)
                    schon_da = bool(
                        erste_zeile and erste_zeile in bisher and limit_zeile(bisher.rsplit(erste_zeile, 1)[1])
                    )
                    if not schon_da:
                        if erste_zeile:
                            zeilen.append(f"❯ {erste_zeile}")
                        zeilen.extend(ausgabe.splitlines())
        text = ("\n".join(zeilen) + "\n" + _LEER_PROMPT) if zeilen else _LEER_PROMPT
        return PaneStand(
            existiert=True,
            session_laeuft=session_laeuft,
            farbig=text,
            text=text,
            status=status,
        )

    def tippen(self, ziel: str, text: str, *, tmux_socket: str | None = None) -> None:
        info = self._ziel_aufloesen(ziel)
        if info is None:
            raise RuntimeError(f"Windows-Session nicht gefunden: {ziel}")
        cmd = werkzeug.startbefehl("claude", resume=info.session_id, prompt=text, interaktiv=False)
        ordner = self._zustand_ordner()
        ordner.mkdir(parents=True, exist_ok=True)
        ausgabe_pfad = ordner / f"win-ausgabe-{info.schluessel}.log"
        cwd = info.cwd if (info.cwd is not None and info.cwd.is_dir()) else None
        umgebung = sitzung_env.ohne_sitzung()
        try:
            with ausgabe_pfad.open("wb") as ausgabe_fh:  # Kind erbt den Handle, Eltern schließen
                proc = starter.starte(
                    cmd,
                    cwd=cwd,
                    env=umgebung,
                    stdin=subprocess.DEVNULL,
                    stdout=ausgabe_fh,
                    stderr=subprocess.STDOUT,
                    notnagel=None,
                    **prozessbaum.ohne_fenster(),
                )
        except (OSError, ValueError, starter.StartGesperrt) as fehler:
            raise RuntimeError(f"Windows-Weckruf nicht gestartet: {fehler}") from fehler
        self._prozesse[info.schluessel] = proc
        startzeit = prozessbaum.startzeit(proc.pid)
        manifest.schreibe_atomar(
            self._laeufer_pfad(info.schluessel),
            {
                "pid": proc.pid,
                "startzeit": startzeit,
                "text": text,
                "ausgabe": str(ausgabe_pfad),
                "zeit": time.time(),
            },
        )

    def _schtasks(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["schtasks", *args],
                capture_output=True,
                **konsole.UNTERPROZESS_TEXT,
                check=False,
                timeout=SCHTASKS_ZEITLIMIT_S,
                **prozessbaum.ohne_fenster(),
            )
        except FileNotFoundError as fehler:
            raise RuntimeError("schtasks nicht gefunden — Windows-Aufgabenplanung fehlt") from fehler
        except subprocess.TimeoutExpired as fehler:
            raise RuntimeError(f"schtasks {args[0]} hängt (über {SCHTASKS_ZEITLIMIT_S} s)") from fehler

    def _abfrage(self) -> str | None:
        lauf = self._schtasks("/Query", "/TN", self.aufgabe_name, "/XML")
        return lauf.stdout if lauf.returncode == 0 else None

    def cron_einrichten(
        self,
        zustand: Path,
        *,
        takt_min: int = CRON_TAKT_MIN,
        trocken: bool = False,
        python: str = sys.executable,
    ) -> str:
        windows_aufgabe_intervall(takt_min)  # prüft 1–60 Minuten
        pythonw = Path(python).with_name("pythonw.exe")
        argumente = windows_aufgabe_argumente(zustand)
        befehl = f'"{pythonw}" {argumente}'
        try:
            vorhanden = self._abfrage()
        except RuntimeError:
            if not trocken:
                raise
            vorhanden = None
        if windows_aufgabe_gleich(vorhanden, str(pythonw), argumente, takt_min):
            return f"Wecker-Aufgabe unverändert: alle {takt_min} min {befehl}"
        if trocken:
            return f"[trocken] Wecker-Aufgabe würde eingerichtet: alle {takt_min} min {befehl}"
        if not pythonw.is_file():
            raise RuntimeError(
                f"pythonw.exe fehlt neben {python} — ohne sie öffnet die geplante Aufgabe bei jedem Takt ein Fenster."
            )
        zustand.mkdir(parents=True, exist_ok=True)
        lauf = self._schtasks(
            "/Create",
            "/F",
            "/SC",
            "MINUTE",
            "/MO",
            str(takt_min),
            "/TN",
            self.aufgabe_name,
            "/TR",
            befehl,
        )
        if lauf.returncode != 0:
            raise RuntimeError(f"schtasks /Create: {(lauf.stderr or lauf.stdout).strip()[:300]}")
        return f"Wecker-Aufgabe eingerichtet: alle {takt_min} min {befehl}"


def system_wecker(plattform: str | None = None, *, zustand: Path | None = None) -> SystemWecker:
    """Automatische Wahl des System-Werkzeugkastens (L6)."""
    ziel_plattform = sys.platform if plattform is None else plattform
    if ziel_plattform == "win32":
        return WindowsWecker(zustand=zustand)
    return LinuxTmuxWecker()


# --- Fachlicher Kern (systemunabhängig) -----------------------------------------


def pruefe_auftrag(
    auftrag: Weckauftrag,
    werkzeug: SystemWecker,
    *,
    jetzt: float,
    trocken: bool = False,
    nachpruef_s: float = NACHPRUEF_S,
    puffer_s: float = RESET_PUFFER_S,
    schlafen: Callable[[float], None] = time.sleep,
) -> Ergebnis:
    """Prüft genau einen Weckauftrag und stößt bei Fälligkeit an (mutiert ``auftrag``)."""
    takt_s = auftrag.takt_min * 60.0

    # 1) Aktives Limit-Wartefenster prüfen, bevor erneut getippt wird.
    if auftrag.warten_bis > 0 and jetzt < auftrag.warten_bis:
        stand = werkzeug.pane_lesen(auftrag.ziel, tmux_socket=auftrag.tmux_socket)
        if not stand.existiert:
            auftrag.zuletzt_gescheitert = jetzt
            auftrag.letzter_grund = stand.fehler or f"Fenster/Pane nicht gefunden: {auftrag.ziel}"
            return Ergebnis(auftrag.name, auftrag.ziel, "gescheitert", auftrag.letzter_grund)
        if not auftrag.limit_hash or aufpasser.pane_hash(stand.text) == auftrag.limit_hash:
            rest_min = max(1, int((auftrag.warten_bis - jetzt + 59) // 60))
            grund = (
                f"Limit-Reset noch ~{rest_min} min entfernt "
                f"(bis {_jetzt_iso(auftrag.warten_bis)}; {auftrag.letzter_grund})"
            )
            return Ergebnis(auftrag.name, auftrag.ziel, "wartet_auf_reset", grund, warten_bis=auftrag.warten_bis)
        # Pane hat sich verändert (z. B. manuelle Fortsetzung) → Limit-Wartezeit erledigt.
        auftrag.warten_bis = 0.0
        auftrag.limit_hash = ""
    else:
        # Kein aktives Limit-Wartefenster (entweder normaler Takt oder Reset-Zeit erreicht).
        reset_faellig = auftrag.warten_bis > 0 and jetzt >= auftrag.warten_bis
        letzte_aktion = max(auftrag.zuletzt_geweckt, auftrag.zuletzt_gescheitert)
        if not reset_faellig and letzte_aktion > 0 and (jetzt - letzte_aktion) < takt_s:
            return Ergebnis(auftrag.name, auftrag.ziel, "nicht_faellig", "Takt noch nicht fällig")
        stand = werkzeug.pane_lesen(auftrag.ziel, tmux_socket=auftrag.tmux_socket)

    if not stand.existiert:
        auftrag.zuletzt_gescheitert = jetzt
        auftrag.letzter_grund = stand.fehler or f"Fenster/Pane nicht gefunden: {auftrag.ziel}"
        return Ergebnis(auftrag.name, auftrag.ziel, "gescheitert", auftrag.letzter_grund)
    if not stand.session_laeuft:
        return Ergebnis(auftrag.name, auftrag.ziel, "uebersprungen", "keine laufende Session im Pane (nur Shell)")
    if aufpasser.arbeitet_echt(stand.text, stand.status):
        return Ergebnis(auftrag.name, auftrag.ziel, "uebersprungen", "Session arbeitet oder wartet auf Rückfrage")
    if not aufpasser.eingabe_leer(stand.farbig):
        return Ergebnis(auftrag.name, auftrag.ziel, "uebersprungen", "Eingabefeld nicht leer")

    # Steht schon vor dem Tippen eine NEUE Limit-Meldung mit bekanntem Reset im Bild
    # (z. B. vom letzten internen /loop), warten wir sauber auf deren Reset.
    bild_hash = aufpasser.pane_hash(stand.text)
    sichtbares_limit = limit_zeile(stand.text)
    if sichtbares_limit and bild_hash != auftrag.limit_hash:
        warten_bis = _warten_bis_aus_zeile(sichtbares_limit, jetzt, puffer_s)
        if warten_bis > jetzt:
            auftrag.zuletzt_gescheitert = jetzt
            auftrag.letzter_grund = sichtbares_limit
            auftrag.warten_bis = warten_bis
            auftrag.limit_hash = bild_hash
            grund = f"Limit im Pane erkannt ({sichtbares_limit}) — warte bis Reset"
            return Ergebnis(auftrag.name, auftrag.ziel, "gescheitert", grund, warten_bis=warten_bis)

    if trocken:
        return Ergebnis(auftrag.name, auftrag.ziel, "trocken", f"würde wecken: {auftrag.text[:80]}")

    vorher_text = stand.text
    try:
        werkzeug.tippen(auftrag.ziel, auftrag.text, tmux_socket=auftrag.tmux_socket)
    except (RuntimeError, OSError) as fehler:
        auftrag.zuletzt_gescheitert = jetzt
        auftrag.letzter_grund = str(fehler)
        return Ergebnis(auftrag.name, auftrag.ziel, "gescheitert", f"Tippen gescheitert: {fehler}")

    if nachpruef_s > 0:
        schlafen(nachpruef_s)

    nachher = werkzeug.pane_lesen(auftrag.ziel, tmux_socket=auftrag.tmux_socket)
    if not nachher.existiert:
        auftrag.zuletzt_gescheitert = jetzt
        auftrag.letzter_grund = nachher.fehler or "Pane nach Weckruf verschwunden"
        return Ergebnis(auftrag.name, auftrag.ziel, "gescheitert", auftrag.letzter_grund)

    frisches_limit = neue_limit_zeile(vorher_text, nachher.text, auftrag.text)
    if frisches_limit is not None:
        warten_bis = _warten_bis_aus_zeile(frisches_limit, jetzt, puffer_s)
        auftrag.zuletzt_gescheitert = jetzt
        auftrag.letzter_grund = frisches_limit
        auftrag.warten_bis = warten_bis
        auftrag.limit_hash = aufpasser.pane_hash(nachher.text)
        grund = f"Weckruf am Limit gescheitert: {frisches_limit}"
        return Ergebnis(auftrag.name, auftrag.ziel, "gescheitert", grund, warten_bis=warten_bis)

    auftrag.zuletzt_geweckt = jetzt
    auftrag.warten_bis = 0.0
    auftrag.limit_hash = ""
    auftrag.letzter_grund = ""
    return Ergebnis(auftrag.name, auftrag.ziel, "geweckt", "Session angestoßen")


def durchgang(
    zustand: Path | None = None,
    *,
    werkzeug: SystemWecker | None = None,
    tmux_socket: str | None = None,
    trocken: bool = False,
    jetzt: float | None = None,
    nachpruef_s: float = NACHPRUEF_S,
    puffer_s: float = RESET_PUFFER_S,
    schlafen: Callable[[float], None] = time.sleep,
) -> list[Ergebnis]:
    """Ein Prüfdurchgang über alle registrierten Weckaufträge.

    Fehlt ``auftraege.json``, endet der Durchgang sofort mit ``[]`` (kein Ordner,
    keine Unterprozesse — wichtig als schneller Null-Aufwand im Aufpasser-Lauf).
    """
    ordner = zustand if zustand is not None else zustand_standard()
    if not auftraege_pfad(ordner).is_file():
        return []
    jetzt_s = time.time() if jetzt is None else jetzt
    kasten = werkzeug if werkzeug is not None else system_wecker(zustand=ordner)
    ergebnisse: list[Ergebnis] = []
    stapel = ExitStack()
    if not trocken:
        try:
            gehalten = stapel.enter_context(sperre.halte(ordner / "lock"))
        except OSError as fehler:
            log.warning("Wecker-Sperre %s nicht nutzbar (%s) — übersprungen", ordner / "lock", fehler)
            return []
        if not gehalten:
            log.info("Wecker läuft schon (Sperre belegt) — dieser Lauf endet")
            return []
    try:
        auftraege = lade_auftraege(ordner)
        if not auftraege:
            return []
        for auftrag in auftraege.values():
            if tmux_socket and not auftrag.tmux_socket:
                auftrag = Weckauftrag(**{**asdict(auftrag), "tmux_socket": tmux_socket})
            erg = pruefe_auftrag(
                auftrag,
                kasten,
                jetzt=jetzt_s,
                trocken=trocken,
                nachpruef_s=nachpruef_s,
                puffer_s=puffer_s,
                schlafen=schlafen,
            )
            auftraege[auftrag.name] = auftrag
            ergebnisse.append(erg)
            _log_ereignis(ordner, erg, jetzt=jetzt_s, trocken=trocken)
        if not trocken:
            speichere_auftraege(ordner, auftraege)
    finally:
        stapel.close()
    return ergebnisse


# --- CLI ------------------------------------------------------------------------


def richte_parser_ein(unter: Any) -> None:
    """Registriert ``to_spawn.py wecker <setzen|loeschen|liste|lauf|einrichten>``."""
    p = unter.add_parser(
        "wecker",
        help="externer Wecker für Leit-/Aufsichts-Sessions außerhalb der Session (#287)",
    )
    sub = p.add_subparsers(dest="wecker_befehl", required=True)

    p_set = sub.add_parser("setzen", help="Weckauftrag anlegen oder aktualisieren")
    p_set.add_argument("name", help="eindeutiger Name (z. B. leit-bau)")
    p_set.add_argument(
        "--ziel",
        required=True,
        help="Ziel-Pane (Linux: =leit:0 / %%12) oder Session-Ziel (Windows: Sessions-JSON, Session-ID, Name, pid:<N>)",
    )
    p_set.add_argument("--text", required=True, help="Weckruf-Text")
    p_set.add_argument(
        "--takt-min",
        type=int,
        default=STANDARD_TAKT_MIN,
        help=f"Abstand in Minuten (Vorgabe: {STANDARD_TAKT_MIN})",
    )
    p_set.add_argument("--tmux-socket", default=None, help="optionaler tmux-Socket (-L)")
    p_set.add_argument("--zustand", type=Path, default=None, help="Zustandsordner")

    p_del = sub.add_parser("loeschen", help="Weckauftrag entfernen")
    p_del.add_argument("name", help="Name des Weckauftrags")
    p_del.add_argument("--zustand", type=Path, default=None, help="Zustandsordner")

    p_lst = sub.add_parser("liste", help="registrierte Weckaufträge anzeigen")
    p_lst.add_argument("--zustand", type=Path, default=None, help="Zustandsordner")

    p_lauf = sub.add_parser("lauf", help="alle fälligen Weckaufträge prüfen und anstoßen")
    p_lauf.add_argument("--trocken", action="store_true", help="nur zeigen, nichts tippen/schreiben")
    p_lauf.add_argument("--tmux-socket", default=None, help="optionaler tmux-Socket (-L)")
    p_lauf.add_argument("--zustand", type=Path, default=None, help="Zustandsordner")

    p_ein = sub.add_parser("einrichten", help="System-Taktgeber einrichten (Linux: Cron, Windows: geplante Aufgabe)")
    p_ein.add_argument(
        "--takt-min",
        type=int,
        default=CRON_TAKT_MIN,
        help=f"Takt-Abstand in Minuten (1–60, Vorgabe: {CRON_TAKT_MIN})",
    )
    p_ein.add_argument("--trocken", action="store_true", help="nur zeigen, nichts schreiben")
    p_ein.add_argument("--zustand", type=Path, default=None, help="Zustandsordner")


def _logging_einrichten(zustand: Path, trocken: bool) -> logging.Handler:
    handler: logging.Handler
    if trocken:
        handler = logging.StreamHandler(sys.stderr)
    else:
        zustand.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(zustand / "wecker.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return handler


def lauf(args: argparse.Namespace) -> int:
    """Einstieg für ``to_spawn.py wecker …``."""
    zustand = args.zustand.expanduser() if args.zustand else zustand_standard()
    aktion = args.wecker_befehl
    if aktion == "setzen":
        try:
            auftrag = auftrag_setzen(
                zustand,
                name=args.name,
                ziel=args.ziel,
                text=args.text,
                takt_min=args.takt_min,
                tmux_socket=args.tmux_socket,
            )
        except (ValueError, TypeError, RuntimeError, OSError) as fehler:
            print(f"FEHLER: {fehler}", file=sys.stderr)
            return 1
        print(f"Wecker gesetzt: {auftrag.name} → {auftrag.ziel} (alle {auftrag.takt_min} min)")
        return 0
    if aktion == "loeschen":
        try:
            entfernt = auftrag_loeschen(zustand, args.name)
        except (ValueError, TypeError, RuntimeError, OSError) as fehler:
            print(f"FEHLER: {fehler}", file=sys.stderr)
            return 1
        if not entfernt:
            print(f"Wecker nicht gefunden: {args.name}", file=sys.stderr)
            return 1
        print(f"Wecker gelöscht: {args.name}")
        return 0
    if aktion == "liste":
        try:
            alle = lade_auftraege(zustand)
        except (ValueError, TypeError, OSError) as fehler:
            print(f"FEHLER: {fehler}", file=sys.stderr)
            return 1
        if not alle:
            print("Keine Weckaufträge registriert.")
            return 0
        for a in alle.values():
            status = (
                f"wartet bis {_jetzt_iso(a.warten_bis)} ({a.letzter_grund})"
                if a.warten_bis > 0
                else (f"zuletzt gescheitert: {a.letzter_grund}" if a.letzter_grund else "bereit")
            )
            print(f"- {a.name} · Ziel {a.ziel} · alle {a.takt_min} min · {status}")
        return 0
    if aktion == "einrichten":
        try:
            print(system_wecker(zustand=zustand).cron_einrichten(zustand, takt_min=args.takt_min, trocken=args.trocken))
            return 0
        except NotImplementedError as fehler:
            print(f"OFFEN: {fehler}", file=sys.stderr)
            return 2
        except (ValueError, RuntimeError, OSError) as fehler:
            print(f"FEHLER: {fehler}", file=sys.stderr)
            return 1
    if aktion == "lauf":
        handler = _logging_einrichten(zustand, args.trocken)
        try:
            ergebnisse = durchgang(zustand, tmux_socket=args.tmux_socket, trocken=args.trocken)
        except NotImplementedError as fehler:
            log.warning("%s", fehler)
            print(f"OFFEN: {fehler}", file=sys.stderr)
            return 2
        except Exception as fehler:  # Cron darf nie still sterben
            log.exception("Wecker-Lauf abgebrochen")
            print(f"FEHLER: {fehler}", file=sys.stderr)
            return 1
        finally:
            log.removeHandler(handler)
            handler.close()
        for erg in ergebnisse:
            if args.trocken or erg.ereignis in ("geweckt", "gescheitert", "wartet_auf_reset"):
                print(f"{erg.name}: {erg.ereignis}" + (f" — {erg.grund}" if erg.grund else ""))
        return 0
    print(f"Unbekannter wecker-Befehl: {aktion}", file=sys.stderr)
    return 2
