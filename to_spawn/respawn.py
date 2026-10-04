"""Bau-Session nach fester SOP ablösen (#431, Spec #399: E2, E6, E7, E16).

Eine Tür: :func:`abloesen`. Sie löst die Claude-Session im tmux-Fenster ``bau <N>``
durch eine frische ab, immer in dieser Reihenfolge:

0. Duplikat-Prüfung (A5): genau ein Fenster ``bau <N>``, kein ``bau <N> neu``,
   höchstens ein ``bau.py`` für N — sonst Exit 3, nichts anfassen.
a) Handoff-Auftrag ins alte Fenster (entfällt, wenn ihn die Leiter #432 seit
   ``handoff_seit`` getippt hat und beide Dateien frisch da sind).
b) neues Fenster ``bau <N> neu`` mit ``bau <N> --sofort --ohne-prompt`` wie spawn/capo
   (Konfiguration allein aus ``bau.py``; nie ein Start-Prompt als Argument, V4).
c) warten bis bereit und ruhig (Speicher-Wartezeit zählt gegen ``--warte-max``),
   ``/remote-control`` tippen (E7), Bestätigung prüfen (ein Nachschub-Enter) — sonst Exit 1.
d) warten, bis Handoff UND Start-Prompt frisch und nicht leer da sind (G5) — sonst Exit 2.
e) Start-Prompt ins neue Fenster tippen, Bildschirm prüfen (G4) — erst dann alte
   Session beenden (ganzer Prozessbaum) und das neue Fenster in ``bau <N>`` umbenennen.

Jeder Abbruch nach Schritt a schließt das neue Fenster, setzt ``.to-spawn/sessions/<N>.json``
auf den Stand vor Schritt b zurück und sagt der alten Session, weiterzuarbeiten.

Exit-Codes: 0 abgelöst · 1 neue Session nicht bewiesen · 2 Handoff/Start-Prompt
fehlt · 3 Duplikat/alte Session fehlt. :class:`Ergebnis` trägt genau eine Zeile (E16).

Außenwelt (tmux, Prozesse, Uhr) nur über das Protokoll :class:`Werkzeug`; echt ist
:class:`TmuxWerkzeug`, Tests geben ein Fake hinein.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Literal, Protocol

from to_spawn import capo, config, prozessbaum, sessions_datei, speicher, tmux_aufruf
from to_spawn.tmux_aufruf import FensterWeg, TmuxFehler

log = logging.getLogger(__name__)

#: Bildschirm-Text, an dem eine frisch gestartete Claude-Session eingabebereit ist.
BEREIT_MARKER: tuple[str, ...] = ("❯", "? for shortcuts")
#: Startdialoge, die auch „❯“ zeigen, aber keine Eingabe annehmen (Vertrauens-Abfrage).
DIALOG_MARKER: tuple[str, ...] = ("trust this folder", "Enter to confirm")
#: Claude arbeitet gerade: Unterbrechen-Hinweis oder Spinner-Zeile „✻ Tut etwas…“.
#: Begrüßung, „✻ Worked for …“ und abgeschlossene Werkzeug-Aufrufe (●/⏺) sind kein
#: Arbeitszustand; Zeilen mit dem Eingabezeichen „❯“ (Echo) zählen nie.
ARBEIT_HINWEIS = "esc to interrupt"
SPINNER_ZEILE = re.compile(r"^\s*[✻✶✳✢✽]\s+\S.*…")
#: Texte, die Claude nach erfolgreichem ``/remote-control`` zeigt (klein geschrieben).
#: Claude Code 2.1 (Echtlauf 2, 04.10.2026): „/remote-control is active · Continue here …“.
REMOTE_MARKER: tuple[str, ...] = ("remote-control is active", "remote control active")
#: Frist für „bereit“ ab Fensterstart. Zeit, in der bau.py noch an der Speicher-Sperre
#: wartet (:data:`speicher.WARTE_TEXT` auf dem Schirm), zählt nicht hierzu, sondern
#: gegen ``--warte-max``.
BEREIT_MAX_S = 120.0
#: So lange muss der bereite Bildschirm unverändert stehen, bevor getippt wird.
EINGABE_RUHE_S = 3.0
REMOTE_MAX_S = 10.0
BILDSCHIRM_MAX_S = 30.0
TAKT_S = 2.0
#: Pause zwischen eingefügtem Text und Enter.
TIPP_PAUSE_S = 1.0
#: Frist nach SIGTERM, danach SIGKILL.
BEENDEN_MAX_S = 30.0
BEENDEN_TAKT_S = 1.0
#: So lange darf die alte Session nach Schritt e noch arbeiten (Commit), bevor sie endet.
ALT_RUHE_MAX_S = 120.0
WARTE_MAX_VORGABE = 1800.0
REMOTE_CONTROL = "/remote-control"
HANDOFF_ORDNER = "docs/handoffs"

EXIT_OK = 0
EXIT_NICHT_BEWIESEN = 1
EXIT_HANDOFF_FEHLT = 2
EXIT_DUPLIKAT = 3

#: Ausgänge von :meth:`Werkzeug.alte_session_beenden`.
Beendet = Literal["beendet", "schon_weg", "lebt"]
BEENDET: Beendet = "beendet"
SCHON_WEG: Beendet = "schon_weg"
LEBT: Beendet = "lebt"

#: Wird der alten Session getippt, wenn die Ablösung nach Schritt a abbricht.
WEITER_AUFTRAG = (
    "Ablösung abgebrochen (respawn). Die neue Session startet nicht — arbeite normal "
    "am Ticket weiter. Handoff-Datei und Start-Prompt dürfen liegen bleiben."
)


@dataclass(frozen=True)
class FensterInfo:
    """Ein tmux-Fenster: Sitzung, Fenstername, Ziel für ``-t`` und PID des Panes."""

    sitzung: str
    name: str
    ziel: str
    pane_pid: int


@dataclass(frozen=True)
class Ergebnis:
    """Ausgang der Ablösung: Exit-Code und genau eine Zeile Klartext."""

    exit: int
    zeile: str


@dataclass(frozen=True)
class Auftrag:
    """Was abgelöst wird und wie — die Eingaben von :func:`abloesen` an einer Stelle."""

    repo: Path
    spec: int
    ticket: int
    warte_max: float = WARTE_MAX_VORGABE
    dry_run: bool = False
    handoff_seit: float | None = None

    @property
    def kopf(self) -> str:
        return f"respawn #{self.ticket}"

    @property
    def name_alt(self) -> str:
        return f"bau {self.ticket}"

    @property
    def name_neu(self) -> str:
        return f"bau {self.ticket} neu"

    @property
    def sitzung(self) -> str:
        return f"spec-{self.spec}"


class Werkzeug(Protocol):
    """Alle Außenwelt-Zugriffe der Ablösung (Naht für Tests).

    ``bildschirm`` wirft :class:`FensterWeg`, wenn das Fenster verschwunden ist.
    """

    def fenster_liste(self) -> list[FensterInfo]: ...
    def bau_prozesse(self) -> list[str]: ...
    def fenster_starten(
        self, sitzung: str, name: str, cwd: str, befehl: str
    ) -> str: ...
    def tippen(self, ziel: str, text: str) -> None: ...
    def taste(self, ziel: str, taste: str) -> None: ...
    def bildschirm(self, ziel: str) -> str | None:
        """Sichtbarer Text des Panes; ``None`` = nicht lesbar (tmux-Fehler)."""
        ...

    def fenster_umbenennen(self, ziel: str, name: str) -> None: ...
    def fenster_schliessen(self, ziel: str) -> None: ...
    def alte_session_beenden(self, pane_pid: int) -> Beendet: ...
    def committet(self, wt: Path, pfade: list[Path]) -> bool:
        """Alle Pfade committet; git-Fehler (``OSError``/``SubprocessError``) fliegen."""
        ...

    def claude_laeuft(self, pane_pid: int) -> bool: ...
    def jetzt(self) -> float: ...
    def schlafen(self, s: float) -> None: ...


class TmuxWerkzeug:
    """Echte Umsetzung: tmux über ``subprocess``, Prozesse über ``pgrep``/``/proc``."""

    def _tmux(self, *argumente: str, eingabe: str | None = None) -> str:
        """Ein tmux-Aufruf (Naht für Tests); Fehler siehe :mod:`to_spawn.tmux_aufruf`."""
        return tmux_aufruf.aufrufen(*argumente, eingabe=eingabe)

    def fenster_liste(self) -> list[FensterInfo]:
        """Je Fenster das aktive Pane (unabhängig von ``pane-base-index``)."""
        try:
            roh = self._tmux(
                "list-panes",
                "-a",
                "-F",
                "#{session_name}\t#{window_name}\t#{window_id}\t#{pane_pid}\t#{pane_active}",
            )
        except TmuxFehler as fehler:
            meldung = fehler.stderr
            if "no server running" in meldung or "error connecting" in meldung:
                return []  # Kein tmux-Server = keine Fenster.
            log.error("tmux list-panes scheitert: %s", meldung.strip())
            raise
        fenster: list[FensterInfo] = []
        for zeile in roh.splitlines():
            teile = zeile.split("\t")
            if len(teile) != 5 or teile[4] != "1":
                continue
            sitzung, name, fid, pid, _ = teile
            fenster.append(
                FensterInfo(sitzung, name, f"={sitzung}:{fid}", int(pid or 0))
            )
        return fenster

    def bau_prozesse(self) -> list[str]:
        fertig = subprocess.run(
            ["pgrep", "-af", "bau.py"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if fertig.returncode not in (0, 1):
            raise RuntimeError(f"pgrep nicht nutzbar: {fertig.stderr.strip()}")
        return [z for z in fertig.stdout.splitlines() if z.strip()]

    def fenster_starten(self, sitzung: str, name: str, cwd: str, befehl: str) -> str:
        raus = self._tmux(
            "new-window",
            "-d",
            "-P",
            "-F",
            "=#{session_name}:#{window_id}",
            "-t",
            f"={sitzung}:",
            "-n",
            name,
            "-c",
            cwd,
            befehl,
        )
        return raus.strip()

    def tippen(self, ziel: str, text: str) -> None:
        # Text als Bracketed Paste (ein Block, Zeilenumbrüche schicken nichts ab),
        # dann Enter getrennt — sonst schluckt die TUI das Enter im Einfügen.
        puffer = f"respawn-{os.getpid()}"
        self._tmux("load-buffer", "-b", puffer, "-", eingabe=text)
        self._tmux("paste-buffer", "-p", "-d", "-b", puffer, "-t", ziel)
        self.schlafen(TIPP_PAUSE_S)
        try:
            self._tmux("send-keys", "-t", ziel, "Enter")
        except TmuxFehler:
            # Kein halber Auftrag im Eingabefeld: Zeile leeren, Fehler weitergeben.
            try:
                self._tmux("send-keys", "-t", ziel, "C-u")
            except TmuxFehler as leeren:
                log.warning("Eingabe in %s nicht geleert: %s", ziel, leeren)
            raise

    def taste(self, ziel: str, taste: str) -> None:
        self._tmux("send-keys", "-t", ziel, taste)

    def bildschirm(self, ziel: str) -> str | None:
        """Sichtbarer Text; :class:`FensterWeg` wenn das Fenster fehlt, ``None`` bei Fehlern."""
        try:
            return self._tmux("capture-pane", "-p", "-t", ziel)
        except FensterWeg:
            raise
        except TmuxFehler as fehler:
            log.warning("Bildschirm %s nicht lesbar: %s", ziel, fehler)
            return None

    def fenster_umbenennen(self, ziel: str, name: str) -> None:
        self._tmux("rename-window", "-t", ziel, name)

    def fenster_schliessen(self, ziel: str) -> None:
        try:
            self._tmux("kill-window", "-t", ziel)
        except FensterWeg:
            log.info("Fenster %s schon zu (Session hat sich selbst beendet)", ziel)
        except TmuxFehler as fehler:
            log.warning("Fenster %s ließ sich nicht schließen: %s", ziel, fehler)

    def committet(self, wt: Path, pfade: list[Path]) -> bool:
        """Jede Datei hat einen Commit und keine offene Änderung im Worktree ``wt``.

        git-Fehler werden weitergereicht: „nicht prüfbar“ ist nicht „nicht committet“.
        """
        for pfad in pfade:
            letzter = self._git(wt, "log", "-1", "--format=%H", "--", str(pfad))
            if not letzter.strip():
                return False
        return not self._git(
            wt, "status", "--porcelain", "--", *map(str, pfade)
        ).strip()

    def _git(self, wt: Path, *argumente: str) -> str:
        return subprocess.run(
            ["git", "-C", str(wt), *argumente],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout

    def alte_session_beenden(self, pane_pid: int) -> Beendet:
        """Beendet den Prozessbaum des alten Panes: erst bau.py & Co., dann claude (keine Folge-Runde).

        ``schon_weg``: kein claude-Prozess mehr (Session hat sich selbst beendet).
        """
        baum = prozessbaum.baum(pane_pid)
        claude = [pid for pid in baum if prozessbaum.ist_claude(pid)]
        andere = [pid for pid in baum if pid not in claude]
        tot = prozessbaum.beenden(
            [*andere, *claude],
            ((signal.SIGTERM, BEENDEN_MAX_S), (signal.SIGKILL, 5 * BEENDEN_TAKT_S)),
            warten_auf=claude,
            takt_s=BEENDEN_TAKT_S,
            schlafen=self.schlafen,
            eskalation=lambda _sig, _lebende: log.warning(
                "claude %s lebt %s s nach SIGTERM — SIGKILL.", claude, BEENDEN_MAX_S
            ),
        )
        if not claude:
            log.info("Unter Pane-PID %s läuft kein claude-Prozess mehr.", pane_pid)
            return SCHON_WEG
        if tot:
            return BEENDET
        log.error("claude-Prozess(e) %s leben auch nach SIGKILL.", claude)
        return LEBT

    def claude_laeuft(self, pane_pid: int) -> bool:
        """Läuft im Prozessbaum des Panes eine Claude-Session (sonst nur Shell)?"""
        return any(prozessbaum.ist_claude(pid) for pid in prozessbaum.baum(pane_pid))

    def jetzt(self) -> float:
        return time.time()

    def schlafen(self, s: float) -> None:
        time.sleep(s)


# --- Reine Hilfen -----------------------------------------------------------------


def _bau_pid_fuer(zeile: str, ticket: int) -> bool:
    """``pgrep -af``-Zeile gehört zu ``bau.py <ticket>``."""
    return re.search(rf"bau\.py\s+{ticket}(\s|$)", zeile) is not None


def _dateien(wt: Path, ticket: int, seit: float) -> tuple[Path, Path]:
    """Feste Pfade für Handoff und Start-Prompt (Datum aus ``seit``)."""
    tag = time.strftime("%Y-%m-%d", time.localtime(seit))
    ordner = wt / HANDOFF_ORDNER
    return ordner / f"HANDOFF_{tag}_{ticket}.md", ordner / f"START_{tag}_{ticket}.txt"


def _frisch(pfad: Path, seit: float) -> bool:
    """Datei da, nicht leer und nach ``seit`` geschrieben (G5)."""
    try:
        stat = pfad.stat()
    except OSError:
        return False
    return stat.st_size > 0 and stat.st_mtime >= seit - 1


def _finde(soll: Path, schluessel: int | str, seit: float) -> Path | None:
    """Frische Datei: erst der feste Pfad, sonst gleiches Muster mit anderem Datum.

    Über Mitternacht schreibt die alte Session evtl. mit neuem Datum (Befund 13).
    ``schluessel``: Ticket-Nummer oder ``waechter_<S>`` (Aufseher-Tür, #436).
    """
    if _frisch(soll, seit):
        return soll
    praefix, endung = soll.name.split("_", 1)[0], soll.suffix
    kandidaten = [
        p for p in soll.parent.glob(f"{praefix}_*_{schluessel}{endung}") if _frisch(p, seit)
    ]
    return max(kandidaten, key=lambda p: p.stat().st_mtime, default=None)


def _groesse(pfad: Path | None) -> int:
    try:
        return pfad.stat().st_size if pfad else -1
    except OSError:
        return -1


def _handoff_auftrag(handoff: str, start: str) -> str:
    return (
        "Ablösung dieser Session (respawn, Skill handoff, Bau-Variante): Schreib jetzt den "
        f"Handoff nach {handoff} und den Start-Prompt für die neue Session nach {start} "
        "(nur Text, die neue Session liest ihn als ersten Auftrag). Gibt es die Dateien schon, "
        "sind sie aus einer früheren Runde veraltet: komplett mit dem jetzigen Stand "
        "überschreiben, auch wenn sich wenig geändert hat. Beide Dateien committen. "
        "Keine Zeile „Staffel: weiter“ ausgeben — die neue Session übernimmt, eine "
        "Folge-Runde in diesem Fenster wäre eine zweite Session. Danach nichts mehr tun."
    )


def handoff_auftrag_fuer(wt: Path, ticket: int, jetzt: float) -> str:
    """Handoff-Auftrag von Schritt a — einzige Pfadquelle, auch für die Leiter (#432)."""
    return _handoff_auftrag(
        *(f"{HANDOFF_ORDNER}/{p.name}" for p in _dateien(wt, ticket, jetzt))
    )


def start_prompt_da(wt: Path, ticket: int, seit: float) -> bool:
    """Frische, nicht leere Start-Prompt-Datei ``START_*_<ticket>.txt`` seit ``seit`` (#432)."""
    return _finde(_dateien(wt, ticket, seit)[1], ticket, seit) is not None


def _beide_da(wt: Path, ticket: int, seit: float) -> bool:
    return all(_finde(p, ticket, seit) for p in _dateien(wt, ticket, seit))


def _startbefehl(repo: Path, ticket: int) -> str:
    """Startbefehl des neuen Fensters: ``bau <N> --sofort`` wie spawn/capo.

    Alle Konfiguration kommt aus ``bau.py``; ``--ohne-prompt``: der Start-Prompt
    wird erst in Schritt e ins Fenster getippt.
    """
    return shlex.join(
        ["bash", "-lc", capo.bau_startzeile(repo, ticket, "", "--ohne-prompt")]
    )


def _bereit(text: str) -> bool:
    """Eingabebereit: Prompt-Zeichen sichtbar, aber kein Startdialog."""
    if any(dialog in text for dialog in DIALOG_MARKER):
        return False
    return any(marker in text for marker in BEREIT_MARKER)


def _wartet_auf_speicher(text: str) -> bool:
    """bau.py wartet an der Speicher-Sperre (letzte „Speicher“-Zeile; „wieder frei“ = nein)."""
    zeilen = [z for z in text.splitlines() if "Speicher" in z]
    return bool(zeilen) and speicher.WARTE_TEXT in zeilen[-1]


def _remote_bestaetigt(text: str) -> bool:
    """``/remote-control`` ist ausgeführt: Bestätigung sichtbar, Befehl nicht mehr in der Eingabe.

    Steht ``/remote-control`` noch in der letzten Eingabezeile, ist das Slash-Menü offen
    (dessen Beschreibung kann „Remote Control“ enthalten — zählt nicht).
    """
    eingaben = [z for z in text.splitlines() if z.lstrip().startswith("❯")]
    if eingaben and REMOTE_CONTROL in eingaben[-1]:
        return False
    klein = text.lower()
    return any(marker in klein for marker in REMOTE_MARKER)


def _schirm(w: Werkzeug, ziel: str) -> str:
    """Bildschirmtext fürs Warten auf ein Zeichen: unlesbar zählt wie „noch nichts da“."""
    return w.bildschirm(ziel) or ""


def _arbeitet(text: str) -> bool:
    """Claude arbeitet gerade: Unterbrechen-Hinweis oder Spinner-Zeile, nie Eingabezeilen (``❯``)."""
    for zeile in text.splitlines():
        if "❯" in zeile:
            continue
        if ARBEIT_HINWEIS in zeile or SPINNER_ZEILE.match(zeile):
            return True
    return False


class _Stabil:
    """Merkt, seit wann ein Wert unverändert gilt (``None`` = Bedingung verletzt)."""

    def __init__(self, w: Werkzeug) -> None:
        self._w = w
        self._wert: object = None
        self._seit = 0.0

    def seit_mindestens(self, wert: object, dauer: float) -> bool:
        """True, wenn ``wert`` (nicht None) seit ≥ ``dauer`` s gleich ist und schon einmal so gesehen wurde."""
        jetzt = self._w.jetzt()
        if wert is None or wert != self._wert:
            self._wert, self._seit = wert, jetzt
            return False
        return jetzt - self._seit >= dauer


def _warte(werkzeug: Werkzeug, max_s: float, bedingung: Callable[[], bool]) -> bool:
    """Prüft ``bedingung`` im Takt, bis sie gilt (True) oder ``max_s`` um ist (False)."""
    ende = werkzeug.jetzt() + max_s
    while True:
        if bedingung():
            return True
        if werkzeug.jetzt() >= ende:
            return False
        werkzeug.schlafen(TAKT_S)


# --- Ablauf -----------------------------------------------------------------------


@dataclass
class _DateiSicherung:
    """Inhalt einer Datei vor einem Schritt — ``zuruecklegen`` stellt ihn wieder her.

    ``inhalt is None`` heißt: vorher gab es die Datei nicht → zurücklegen löscht sie.
    ``lesbar=False``: der alte Stand ist unbekannt → zurücklegen fasst nichts an und
    meldet False, damit der Abbruch „Handarbeit nötig“ sagt statt still zu schweigen.
    """

    pfad: Path
    inhalt: bytes | None
    lesbar: bool = True

    @classmethod
    def merken(cls, pfad: Path) -> _DateiSicherung:
        """Sicherung des jetzigen Stands; unlesbar → ``lesbar=False`` (geloggt)."""
        try:
            return cls(pfad, pfad.read_bytes())
        except FileNotFoundError:
            return cls(pfad, None)
        except OSError:
            log.exception(
                "%s nicht lesbar — wird bei Abbruch nicht zurückgesetzt.", pfad
            )
            return cls(pfad, None, lesbar=False)

    def zuruecklegen(self) -> bool:
        """Alten Stand wiederherstellen; False bei Dateifehler oder unbekanntem Stand (geloggt)."""
        if not self.lesbar:
            log.error("%s war vorher unlesbar — nicht zurückgesetzt.", self.pfad)
            return False
        try:
            if self.inhalt is None:
                self.pfad.unlink(missing_ok=True)
            elif not self.pfad.exists() or self.pfad.read_bytes() != self.inhalt:
                self.pfad.parent.mkdir(parents=True, exist_ok=True)
                self.pfad.write_bytes(self.inhalt)
        except OSError:
            log.exception("%s nicht zurückgesetzt.", self.pfad)
            return False
        return True


@dataclass
class _Stand:
    """Was bisher passiert ist — entscheidet, wie ein Abbruch aufräumt."""

    alt: FensterInfo | None = None
    auftrag_getippt: bool = False
    neu: str | None = None
    #: Ab hier arbeitet die neue Session schon — Abbruch schließt sie nicht mehr.
    beenden_begonnen: bool = False
    #: Name des ersten abfangenen Signals (weitere Signale werden dann ignoriert).
    signal_name: str | None = None
    #: Aufräumen läuft — ein Signal wird jetzt nur noch gemerkt, nie mehr geworfen.
    raeumt_auf: bool = False
    #: Sessions-Datei vor Schritt b: die neue bau.py schreibt ihre Gesprächs-ID schon
    #: vor Schritt c — bei Abbruch zurück, sonst setzt der Aufpasser die verworfene fort.
    sessions: _DateiSicherung | None = None
    #: Hinweise für die Ergebniszeile (z. B. Speicher-Wartezeit), Exit bleibt davon unberührt.
    hinweise: list[str] = field(default_factory=list)


class _Abbruch(Exception):
    """Geordneter Abbruch nach Schritt a: Exit-Code + Grund für die Zeile."""

    def __init__(self, exit_code: int, grund: str) -> None:
        super().__init__(grund)
        self.exit_code = exit_code
        self.grund = grund


_SignalHandler = Callable[[int, FrameType | None], object] | int | signal.Handlers
# SIGHUP gibt es unter Windows nicht — dort nur SIGTERM/SIGINT, sonst bricht schon der Import
# (to_spawn.py nest am Laptop, setup_bau_server_push.sh).
_ABGEFANGENE_SIGNALE: tuple[signal.Signals, ...] = tuple(
    getattr(signal, name) for name in ("SIGTERM", "SIGHUP", "SIGINT") if hasattr(signal, name)
)


def abloesen(
    repo: Path,
    spec: int,
    ticket: int,
    *,
    werkzeug: Werkzeug | None = None,
    warte_max: float = WARTE_MAX_VORGABE,
    dry_run: bool = False,
    handoff_seit: float | None = None,
) -> Ergebnis:
    """Löst die Session in ``bau <ticket>`` nach SOP a–e ab (siehe Modul-Kommentar).

    Gibt nie eine Ausnahme weiter: jeder Fehler und SIGTERM/SIGHUP/SIGINT enden als
    :class:`Ergebnis` mit passendem Exit-Code und genau einer Zeile. Nur eine
    ``BaseException`` von außen (z. B. ``KeyboardInterrupt``) wird nach dem Aufräumen
    weitergereicht.
    """
    w = werkzeug or TmuxWerkzeug()
    auftrag = Auftrag(repo, spec, ticket, warte_max, dry_run, handoff_seit)
    stand = _Stand()
    alte_handler = _signale_abfangen(stand)
    try:
        erg = _ablauf(auftrag, w, stand)
    except _Abbruch as abbruch:
        erg = _abbruch_ergebnis(auftrag, w, stand, abbruch.exit_code, abbruch.grund)
    except Exception as fehler:  # Tür gibt nie eine Ausnahme weiter (BLE001: geloggt)
        log.exception("respawn #%s abgebrochen.", ticket)
        text = f"Fehler — {type(fehler).__name__}: {fehler}"
        erg = _abbruch_ergebnis(auftrag, w, stand, EXIT_NICHT_BEWIESEN, text)
    except BaseException:
        log.warning("respawn #%s unterbrochen — räume auf und reiche weiter.", ticket)
        if not stand.beenden_begonnen:
            _aufraeumen(auftrag, w, stand, EXIT_NICHT_BEWIESEN, "unterbrochen")
        raise
    finally:
        _signale_zuruecksetzen(alte_handler)
    return Ergebnis(erg.exit, " ".join(erg.zeile.split()))


def _signale_abfangen(stand: _Stand) -> dict[signal.Signals, _SignalHandler]:
    """SIGTERM/SIGHUP/SIGINT werden zu :class:`_Abbruch` (nur im Haupt-Thread möglich)."""
    if threading.current_thread() is not threading.main_thread():
        return {}

    def handler(signum: int, frame: FrameType | None) -> None:
        name = signal.Signals(signum).name
        if stand.raeumt_auf or stand.signal_name:
            # Aufräumen läuft schon — nicht unterbrechen, nur merken (Docstring abloesen).
            log.warning("respawn: %s während des Aufräumens — räume weiter auf.", name)
            stand.signal_name = stand.signal_name or name
            stand.hinweise.append(f"{name} während des Aufräumens erhalten")
            return
        stand.signal_name = name
        raise _Abbruch(EXIT_NICHT_BEWIESEN, f"abgebrochen durch {name}")

    alte: dict[signal.Signals, _SignalHandler] = {}
    for sig in _ABGEFANGENE_SIGNALE:
        vorher = signal.getsignal(sig)
        alte[sig] = vorher if vorher is not None else signal.SIG_DFL
        signal.signal(sig, handler)
    return alte


def _signale_zuruecksetzen(alte: dict[signal.Signals, _SignalHandler]) -> None:
    for sig, vorher in alte.items():
        signal.signal(sig, vorher)


def _abbruch_ergebnis(
    a: Auftrag, w: Werkzeug, stand: _Stand, exit_code: int, grund: str
) -> Ergebnis:
    """Vor dem Beenden: aufräumen. Danach arbeitet die neue Session schon → Handarbeit."""
    stand.raeumt_auf = True
    if not stand.beenden_begonnen:
        return _aufraeumen(a, w, stand, exit_code, grund)
    return Ergebnis(
        EXIT_NICHT_BEWIESEN,
        f"{a.kopf}: {grund} beim Beenden der alten Session — neue Session arbeitet in "
        f"„{a.name_neu}“, alte evtl. noch offen, Handarbeit nötig"
        + "".join(f" — {h}" for h in stand.hinweise),
    )


def _ablauf(a: Auftrag, w: Werkzeug, stand: _Stand) -> Ergebnis:
    """Schritte 0, a–e; Abbrüche nach a als :class:`_Abbruch`."""
    alt = _pruefe_duplikat(a, w)
    if isinstance(alt, Ergebnis):
        return alt
    stand.alt = alt

    wt = Path(config.worktree_pfad(a.ticket, a.repo))
    befehl = _startbefehl(a.repo, a.ticket)
    if a.dry_run:
        return Ergebnis(
            EXIT_OK,
            f"{a.kopf}: dry-run — alt {alt.ziel}, neu „{a.name_neu}“ in {a.sitzung}, cwd {a.repo}, Befehl: {befehl}",
        )

    # a) Handoff-Auftrag ins alte Fenster (außer die Leiter hat ihn schon getippt und
    # beide Dateien sind frisch). Schon vor dem Tippen gesetzt: scheitert nur das Enter,
    # kann der Auftrag im Eingabefeld stehen — nie „unangetastet“ melden.
    stand.auftrag_getippt = True
    seit = a.handoff_seit
    if seit is None or not _beide_da(wt, a.ticket, seit):
        seit = w.jetzt()
        w.tippen(alt.ziel, handoff_auftrag_fuer(wt, a.ticket, seit))
        log.info("respawn #%s: Handoff-Auftrag an %s getippt.", a.ticket, alt.ziel)
    handoff, start = _dateien(wt, a.ticket, seit)

    # b) neues Fenster: bau.py wie spawn (cwd Hauptbaum, bau.py wechselt selbst in den Worktree)
    stand.sessions = _DateiSicherung.merken(sessions_datei.pfad(a.repo, str(a.ticket)))
    stand.neu = w.fenster_starten(a.sitzung, a.name_neu, str(a.repo), befehl)
    log.info("respawn #%s: neues Fenster %s gestartet.", a.ticket, stand.neu)

    try:
        speicher_s = _remote_control(w, stand.neu, a.warte_max)  # c)
        if speicher_s:
            stand.hinweise.append(
                f"neue Session wartete {int(speicher_s)} s auf Speicher"
            )
        gefunden_h, gefunden_s, prompt = _warte_dateien(
            w, handoff, start, seit, a.ticket, a.warte_max
        )  # d)
        _start_prompt(w, stand.neu, prompt)  # e)
    except FensterWeg as fehler:
        raise _Abbruch(
            EXIT_NICHT_BEWIESEN, f"neue Session beendet sich selbst ({fehler})"
        ) from fehler
    return _alte_abloesen(a, w, stand, alt, stand.neu, wt, gefunden_h, gefunden_s)


def _pruefe_duplikat(a: Auftrag, w: Werkzeug) -> FensterInfo | Ergebnis:
    """Schritt 0 (A5): genau ein ``bau N`` mit Claude, kein ``bau N neu``, ≤ 1 bau.py — sonst Exit 3."""
    fenster = w.fenster_liste()
    alte = [f for f in fenster if f.name == a.name_alt]
    neue = [f for f in fenster if f.name == a.name_neu]
    prozesse = [z for z in w.bau_prozesse() if _bau_pid_fuer(z, a.ticket)]
    if neue:
        return Ergebnis(
            EXIT_DUPLIKAT,
            f"{a.kopf}: Duplikat — Fenster „{a.name_neu}“ gibt es schon, nichts angefasst",
        )
    if len(alte) != 1:
        grund = "kein Fenster" if not alte else f"{len(alte)} Fenster"
        return Ergebnis(
            EXIT_DUPLIKAT, f"{a.kopf}: {grund} „{a.name_alt}“ — nichts angefasst"
        )
    if len(prozesse) > 1:
        return Ergebnis(
            EXIT_DUPLIKAT,
            f"{a.kopf}: {len(prozesse)} bau.py-Prozesse für #{a.ticket} — nichts angefasst",
        )
    if not w.claude_laeuft(alte[0].pane_pid):  # sonst landet der Auftrag in der Shell
        return Ergebnis(
            EXIT_DUPLIKAT,
            f"{a.kopf}: alte Session fehlt — in „{a.name_alt}“ läuft kein Claude, nichts angefasst",
        )
    return alte[0]


def _remote_control(w: Werkzeug, neu: str, warte_max: float) -> float:
    """Schritt c: bereit + ruhig, ``/remote-control`` tippen; Rückgabe: Sekunden an der Speicher-Sperre."""
    bereit, speicher_s = _warte_ruhig_bereit(w, neu, warte_max)
    if not bereit:
        if speicher_s >= warte_max:
            grund = f"neue Session wartet nach {int(speicher_s)} s noch auf Speicher"
        else:
            grund = f"neue Session nach {int(BEREIT_MAX_S)} s nicht bereit"
            if speicher_s:
                grund += f" (davor {int(speicher_s)} s auf Speicher gewartet)"
        raise _Abbruch(EXIT_NICHT_BEWIESEN, grund)
    w.tippen(neu, REMOTE_CONTROL)
    if _warte(w, REMOTE_MAX_S, lambda: _remote_bestaetigt(_schirm(w, neu))):
        return speicher_s
    # Slash-Menü kann das erste Enter als Auswahl schlucken → ein Nachschub-Enter.
    w.taste(neu, "Enter")
    if not _warte(w, REMOTE_MAX_S, lambda: _remote_bestaetigt(_schirm(w, neu))):
        raise _Abbruch(
            EXIT_NICHT_BEWIESEN,
            "Remote Control im neuen Fenster nicht bestätigt (kein Hinweis auf dem Bildschirm)",
        )
    return speicher_s


def _warte_ruhig_bereit(w: Werkzeug, ziel: str, warte_max: float) -> tuple[bool, float]:
    """Bereit-Bildschirm, der :data:`EINGABE_RUHE_S` lang unverändert steht.

    Zwei Uhren: Zeit mit Speicher-Wartezeile zählt gegen ``warte_max``, alle übrige
    gegen :data:`BEREIT_MAX_S`. Jeder Takt zählt zu dem Zustand, der an seinem Anfang
    sichtbar war. Gibt (bereit, Sekunden an der Speicher-Sperre) zurück.
    """
    ruhe = _Stabil(w)
    speicher_s = sonst_s = 0.0
    letzte = w.jetzt()
    wartete = False
    while True:
        text = _schirm(w, ziel)
        jetzt = w.jetzt()
        if wartete:
            speicher_s += jetzt - letzte
        else:
            sonst_s += jetzt - letzte
        letzte, wartete = jetzt, _wartet_auf_speicher(text)
        if ruhe.seit_mindestens(text if _bereit(text) else None, EINGABE_RUHE_S):
            return True, speicher_s
        if speicher_s >= warte_max or sonst_s >= BEREIT_MAX_S:
            return False, speicher_s
        w.schlafen(TAKT_S)


def _warte_dateien(
    w: Werkzeug, handoff: Path, start: Path, seit: float, schluessel: int | str, warte_max: float
) -> tuple[Path, Path, str]:
    """Schritt d (G5): Handoff + Start-Prompt frisch, nicht leer, Größe stabil.

    Gibt die gefundenen Pfade (Handoff, Start-Prompt) und den Prompt-Text zurück.
    ``schluessel`` wie bei :func:`_finde` — auch die Aufseher-Tür wartet hiermit (#436).
    """
    stabil = _Stabil(w)
    fund: list[tuple[Path, Path]] = []

    def bedingung() -> bool:
        h, s = _finde(handoff, schluessel, seit), _finde(start, schluessel, seit)
        if h is None or s is None:
            stabil.seit_mindestens(None, 0)
            return False
        if stabil.seit_mindestens((h, s, _groesse(h), _groesse(s)), 0):
            fund.append((h, s))
            return True
        return False

    if not _warte(w, warte_max, bedingung):
        fehlt = [
            p.name for p in (handoff, start) if _finde(p, schluessel, seit) is None
        ] or ["stabile Dateien"]
        raise _Abbruch(
            EXIT_HANDOFF_FEHLT, f"nach {int(warte_max)} s fehlt {', '.join(fehlt)}"
        )
    h, s = fund[-1]
    prompt = s.read_text(encoding="utf-8").strip()
    if not prompt:
        raise _Abbruch(EXIT_HANDOFF_FEHLT, f"Start-Prompt {s.name} ist leer")
    return h, s, prompt


def _start_prompt(w: Werkzeug, neu: str, prompt: str) -> None:
    """Schritt e (G4): Prompt tippen, auf echten Arbeitszustand warten (Echo zählt nicht)."""
    w.tippen(neu, prompt)
    if not _warte(w, BILDSCHIRM_MAX_S, lambda: _arbeitet(_schirm(w, neu))):
        raise _Abbruch(
            EXIT_NICHT_BEWIESEN,
            f"neue Session zeigt {int(BILDSCHIRM_MAX_S)} s nach dem Start-Prompt keine Arbeit",
        )


def _alte_unruhe(w: Werkzeug, ziel: str) -> str | None:
    """Wartet, bis die alte Session :data:`EINGABE_RUHE_S` lang nicht mehr arbeitet.

    ``None`` = ruhig (Fenster weg zählt als ruhig). Sonst nach :data:`ALT_RUHE_MAX_S`
    der Hinweis für die Ergebniszeile. Ein unlesbarer Bildschirm zählt nie als ruhig —
    sonst würde eine womöglich noch schreibende Session ohne Wartezeit beendet.
    """
    ruhe = _Stabil(w)
    unlesbar = False  # nur der letzte Zustand zählt für den Hinweis

    def bedingung() -> bool:
        nonlocal unlesbar
        try:
            text = w.bildschirm(ziel)
        except FensterWeg:
            return True
        unlesbar = text is None
        if text is None:
            return ruhe.seit_mindestens(None, EINGABE_RUHE_S)
        ruhig = not _arbeitet(text)
        return ruhe.seit_mindestens(True if ruhig else None, EINGABE_RUHE_S)

    if _warte(w, ALT_RUHE_MAX_S, bedingung):
        return None
    if unlesbar:
        return (
            f"Bildschirm der alten Session nicht lesbar, nach {int(ALT_RUHE_MAX_S)} s "
            "beendet trotzdem"
        )
    return (
        f"alte Session arbeitete nach {int(ALT_RUHE_MAX_S)} s noch — beendet trotzdem"
    )


def _alte_abloesen(
    a: Auftrag,
    w: Werkzeug,
    stand: _Stand,
    alt: FensterInfo,
    neu: str,
    wt: Path,
    handoff: Path,
    start: Path,
) -> Ergebnis:
    """Warten bis die alte ruhig ist, sie beenden, altes Fenster zu, neues umbenennen.

    Unfertiger Commit oder noch aktive alte Session stehen als Hinweis in der Zeile
    (Exit bleibt 0 — die neue Session arbeitet bewiesen).
    """
    stand.beenden_begonnen = True
    hinweise = list(stand.hinweise)
    unruhe = _alte_unruhe(w, alt.ziel)
    if unruhe:
        hinweise.append(unruhe)
    try:
        if not w.committet(wt, [handoff, start]):
            hinweise.append(
                f"Handoff/Start-Prompt nicht committet ({handoff.name}, {start.name})"
            )
    except (
        OSError,
        subprocess.SubprocessError,
    ) as fehler:  # unklar ≠ „nicht committet“
        log.warning("respawn #%s: Commit-Stand nicht prüfbar: %s", a.ticket, fehler)
        hinweise.append(f"Commit-Stand nicht prüfbar ({fehler})")
    ausgang = w.alte_session_beenden(alt.pane_pid)
    if ausgang == LEBT:
        return Ergebnis(
            EXIT_NICHT_BEWIESEN,
            f"{a.kopf}: neue Session arbeitet in „{a.name_neu}“, alte (Pane-PID {alt.pane_pid}) "
            "lebt noch — zwei Sessions offen, Handarbeit nötig"
            + "".join(f"; {h}" for h in hinweise),
        )
    if ausgang == SCHON_WEG:
        log.info("respawn #%s: alte Session war schon beendet.", a.ticket)
    # Altes Fenster zu: kein zweites „bau N“.
    w.fenster_schliessen(alt.ziel)
    probleme: list[str] = []
    if _fenster_offen(w, alt.ziel):
        probleme.append(f"altes Fenster „{a.name_alt}“ noch offen, nicht umbenannt")
    else:
        try:
            w.fenster_umbenennen(neu, a.name_alt)
        except RuntimeError as fehler:  # tmux-Fehler: Zustand gehört in die Zeile
            log.exception("respawn #%s: Umbenennen gescheitert.", a.ticket)
            probleme.append(f"Umbenennen in „{a.name_alt}“ gescheitert ({fehler})")
    if probleme:
        return Ergebnis(
            EXIT_NICHT_BEWIESEN,
            f"{a.kopf}: alte Session beendet, neue arbeitet in „{a.name_neu}“ — "
            f"{'; '.join([*probleme, *hinweise])}; Handarbeit nötig",
        )
    zeile = f"{a.kopf}: ok — neue Session im Fenster {a.name_alt}, Handoff {HANDOFF_ORDNER}/{handoff.name}"
    if hinweise:
        zeile += f" — Hinweis: {'; '.join(hinweise)}"
    return Ergebnis(EXIT_OK, zeile)


def _fenster_offen(w: Werkzeug, ziel: str) -> bool:
    """Fenster noch da? Unklar (tmux-Fehler) zählt als offen."""
    try:
        return any(f.ziel == ziel for f in w.fenster_liste())
    except RuntimeError:  # tmux-Fehler: Unklarheit ehrlich als „offen“ melden
        log.exception("Fensterliste nicht lesbar — %s gilt als offen.", ziel)
        return True


_UNBEKANNT = "unbekannt"
"""Fensterliste unlesbar: ob „bau N neu“ offen ist, weiß niemand (zählt als offen)."""


def _halb_gestartet(a: Auftrag, w: Werkzeug) -> str | None:
    """Ziel eines Fensters „bau N neu“, das ``fenster_starten`` trotz Fehler anlegte.

    ``None``: keins da · :data:`_UNBEKANNT`: Fensterliste unlesbar (wie :func:`_fenster_offen`).
    """
    try:
        return next((f.ziel for f in w.fenster_liste() if f.name == a.name_neu), None)
    except RuntimeError:  # tmux-Fehler: unklar = evtl. offen, die Zeile sagt es
        log.exception("respawn #%s: Fensterliste nicht lesbar.", a.ticket)
        return _UNBEKANNT


def _aufraeumen(
    a: Auftrag, w: Werkzeug, stand: _Stand, exit_code: int, grund: str
) -> Ergebnis:
    """Abbruch vor dem Beenden: neues Fenster zu, alte Session weiterarbeiten lassen.

    Die Zeile sagt nur, was wirklich geklappt hat.
    """
    stand.raeumt_auf = True
    teile = [f"{a.kopf}: {grund}"]
    # Start gescheitert (z. B. Zeitüberschreitung), tmux legte das Fenster evtl. trotzdem an.
    neu = stand.neu or (_halb_gestartet(a, w) if stand.sessions else None)
    if neu == _UNBEKANNT:
        teile.append(
            f"Fenster „{a.name_neu}“ evtl. offen (Fensterliste unlesbar), Handarbeit nötig"
        )
    elif neu:
        try:
            w.fenster_schliessen(neu)
        except RuntimeError:  # Prüfung folgt über die Fensterliste
            log.exception("respawn #%s: neues Fenster nicht schließbar.", a.ticket)
        if _fenster_offen(w, neu):
            teile.append(
                f"zwei Sessions offen („{a.name_neu}“ ließ sich nicht schließen), Handarbeit nötig"
            )
        else:
            teile.append("neues Fenster zu")
    if stand.sessions and not stand.sessions.zuruecklegen():
        teile.append(
            f"Sessions-Datei {stand.sessions.pfad.name} nicht zurückgesetzt, Handarbeit nötig"
        )
    if stand.auftrag_getippt and stand.alt:
        try:
            w.tippen(stand.alt.ziel, WEITER_AUFTRAG)
            teile.append("Ablösung abgebrochen, alte Session arbeitet weiter")
        except RuntimeError:  # tmux-Fehler: Zustand gehört in die Zeile
            log.exception("respawn #%s: Weiter-Auftrag nicht getippt.", a.ticket)
            teile.append(
                "alte Session hat den Handoff-Auftrag evtl. erhalten und wartet untätig, "
                "Handarbeit nötig"
            )
    elif stand.alt:
        teile.append("alte Session unangetastet")
    teile.extend(stand.hinweise)
    return Ergebnis(exit_code, " — ".join(teile))
