"""Bau-Session nach fester SOP ablösen (#431, Spec #399: E2, E6, E7, E16).

Eine Tür: :func:`abloesen`. Sie löst die laufende Claude-Session im tmux-Fenster
``bau <N>`` durch eine frische ab — immer in derselben Reihenfolge:

0. Duplikat-Prüfung (A5): genau ein Fenster ``bau <N>``, kein ``bau <N> neu``,
   höchstens ein ``bau.py``-Prozess für N — sonst Exit 3, nichts anfassen.
a) Handoff-Auftrag ins alte Fenster (Handoff + Start-Prompt mit festen Pfaden).
b) neues Fenster ``bau <N> neu`` mit nacktem ``claude --model … --effort …``
   (nie ein Start-Prompt als Argument, V4).
c) warten bis bereit und ruhig, dann ``/remote-control`` tippen (E7) und die
   Bestätigung auf dem Bildschirm prüfen (ein Nachschub-Enter, falls das
   Slash-Menü das erste schluckt) — sonst Exit 1.
d) warten, bis Handoff UND Start-Prompt frisch (nach Schritt a) und nicht leer da
   sind (G5) — sonst Exit 2, neues Fenster zu, alte Session unangetastet.
e) Start-Prompt ins neue Fenster tippen, Bildschirm prüfen (G4) — erst dann alte
   Session beenden (ganzer Prozessbaum des alten Panes) und das neue Fenster in
   ``bau <N>`` umbenennen.

Jeder Abbruch nach Schritt a schließt das neue Fenster und sagt der alten Session,
dass sie weiterarbeiten soll; die Ergebniszeile sagt ehrlich, was davon geklappt hat.

Exit-Codes: 0 abgelöst · 1 neue Session nicht bewiesen · 2 Handoff/Start-Prompt
fehlt · 3 Duplikat/alte Session fehlt. :class:`Ergebnis` trägt dazu genau eine
Zeile für den Ablöse-Subagenten (E16).

Alle Außenwelt-Zugriffe (tmux, Prozesse, Uhr) laufen über das Protokoll
:class:`Werkzeug`; echt ist :class:`TmuxWerkzeug`, Tests geben ein Fake hinein.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from to_spawn import capo, config

log = logging.getLogger(__name__)

#: Bildschirm-Text, an dem eine frisch gestartete Claude-Session eingabebereit ist.
BEREIT_MARKER: tuple[str, ...] = ("❯", "? for shortcuts")
#: Startdialoge, die auch „❯“ zeigen, aber keine Eingabe annehmen (Vertrauens-Abfrage).
DIALOG_MARKER: tuple[str, ...] = ("trust this folder", "Enter to confirm")
#: Zeichen, dass Claude wirklich arbeitet (Spinner, Werkzeug-Aufruf) — Paste-Echo zählt nicht.
ARBEIT_MARKER: tuple[str, ...] = ("esc to interrupt", "✻", "●", "⏺")
#: Texte, die Claude nach erfolgreichem ``/remote-control`` zeigt (klein geschrieben).
#: Claude Code 2.1 (Echtlauf 2, 04.10.2026): „/remote-control is active · Continue here …“.
REMOTE_MARKER = ("remote-control is active", "remote control active")
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
    konfig: dict[str, Any] = field(hash=False)
    warte_max: float = WARTE_MAX_VORGABE
    dry_run: bool = False

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
    """Alle Außenwelt-Zugriffe der Ablösung (Naht für Tests)."""

    def fenster_liste(self) -> list[FensterInfo]: ...
    def bau_prozesse(self) -> list[str]: ...
    def fenster_starten(
        self, sitzung: str, name: str, cwd: str, befehl: str
    ) -> str: ...
    def tippen(self, ziel: str, text: str) -> None: ...
    def taste(self, ziel: str, taste: str) -> None: ...
    def bildschirm(self, ziel: str) -> str: ...
    def fenster_umbenennen(self, ziel: str, name: str) -> None: ...
    def fenster_schliessen(self, ziel: str) -> None: ...
    def alte_session_beenden(self, pane_pid: int) -> Beendet: ...
    def jetzt(self) -> float: ...
    def schlafen(self, s: float) -> None: ...


class TmuxWerkzeug:
    """Echte Umsetzung: tmux über ``subprocess``, Prozesse über ``pgrep``/``/proc``."""

    def _tmux(self, *argumente: str, eingabe: str | None = None) -> str:
        fertig = subprocess.run(
            [*capo._tmux_befehl(), *argumente],
            input=eingabe,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return fertig.stdout

    def fenster_liste(self) -> list[FensterInfo]:
        """Je Fenster das aktive Pane (unabhängig von ``pane-base-index``)."""
        try:
            roh = self._tmux(
                "list-panes",
                "-a",
                "-F",
                "#{session_name}\t#{window_name}\t#{window_id}\t#{pane_pid}\t#{pane_active}",
            )
        except subprocess.CalledProcessError as fehler:
            meldung = str(fehler.stderr or "")
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
        self._tmux("send-keys", "-t", ziel, "Enter")

    def taste(self, ziel: str, taste: str) -> None:
        self._tmux("send-keys", "-t", ziel, taste)

    def bildschirm(self, ziel: str) -> str:
        try:
            return self._tmux("capture-pane", "-p", "-t", ziel)
        except subprocess.CalledProcessError:
            return ""

    def fenster_umbenennen(self, ziel: str, name: str) -> None:
        self._tmux("rename-window", "-t", ziel, name)

    def fenster_schliessen(self, ziel: str) -> None:
        try:
            self._tmux("kill-window", "-t", ziel)
        except subprocess.CalledProcessError:
            log.warning("Fenster %s ließ sich nicht schließen (schon weg?).", ziel)

    def alte_session_beenden(self, pane_pid: int) -> Beendet:
        """Beendet den ganzen Prozessbaum des alten Panes (Pane-PID eingeschlossen).

        Erst bau.py & Co., dann claude — so startet bau.py keine Folge-Runde.
        ``schon_weg``: kein claude-Prozess mehr (Session hat sich selbst beendet).
        """
        baum = [pane_pid, *_nachkommen(pane_pid)]
        claude = [pid for pid in baum if _ist_claude(pid)]
        andere = [pid for pid in baum if pid not in claude]
        _signal([*andere, *claude], signal.SIGTERM)
        if not claude:
            log.info("Unter Pane-PID %s läuft kein claude-Prozess mehr.", pane_pid)
            return SCHON_WEG
        if self._warte_tot(claude, BEENDEN_MAX_S):
            return BEENDET
        log.warning(
            "claude %s lebt %s s nach SIGTERM — SIGKILL.", claude, BEENDEN_MAX_S
        )
        _signal([pid for pid in baum if _lebt(pid)], signal.SIGKILL)
        if self._warte_tot(claude, 5 * BEENDEN_TAKT_S):
            return BEENDET
        log.error("claude-Prozess(e) %s leben auch nach SIGKILL.", claude)
        return LEBT

    def _warte_tot(self, pids: list[int], max_s: float) -> bool:
        """True, sobald keiner der ``pids`` mehr lebt (Uhr über die Naht)."""
        ende = self.jetzt() + max_s
        while any(_lebt(pid) for pid in pids):
            if self.jetzt() >= ende:
                return False
            self.schlafen(BEENDEN_TAKT_S)
        return True

    def jetzt(self) -> float:
        return time.time()

    def schlafen(self, s: float) -> None:
        time.sleep(s)


def _nachkommen(pid: int) -> list[int]:
    """Alle Nachkommen von ``pid`` (rekursiv über ``pgrep -P``)."""
    gefunden: list[int] = []
    offen = [pid]
    while offen:
        eltern = offen.pop()
        fertig = subprocess.run(
            ["pgrep", "-P", str(eltern)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if fertig.returncode not in (0, 1):
            raise RuntimeError(f"pgrep -P {eltern} scheitert: {fertig.stderr.strip()}")
        for zeile in fertig.stdout.split():
            if zeile.isdigit() and int(zeile) not in gefunden:
                gefunden.append(int(zeile))
                offen.append(int(zeile))
    return gefunden


def _signal(pids: list[int], sig: int) -> None:
    """Schickt ``sig`` an jede PID; schon beendete Prozesse zählen nicht als Fehler."""
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            continue


def _ist_claude(pid: int) -> bool:
    """Prozessname oder argv[0] ist ``claude``."""
    try:
        name = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
        argv0 = (
            Path(f"/proc/{pid}/cmdline")
            .read_bytes()
            .split(b"\0", 1)[0]
            .decode(errors="replace")
        )
    except OSError:
        return False
    return name == "claude" or Path(argv0).name == "claude"


def _lebt(pid: int) -> bool:
    """Prozess existiert noch (auch wenn er uns nicht gehört)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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


def _finde(soll: Path, ticket: int, seit: float) -> Path | None:
    """Frische Datei: erst der feste Pfad, sonst gleiches Muster mit anderem Datum.

    Über Mitternacht schreibt die alte Session evtl. mit neuem Datum (Befund 13).
    """
    if _frisch(soll, seit):
        return soll
    praefix, endung = soll.name.split("_", 1)[0], soll.suffix
    kandidaten = [
        p for p in soll.parent.glob(f"{praefix}_*_{ticket}{endung}") if _frisch(p, seit)
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
        "(nur Text, die neue Session liest ihn als ersten Auftrag). Beide Dateien committen. "
        "Danach nichts mehr tun."
    )


def _startbefehl(
    repo: Path, spec: int, ticket: int, wt: str, konfig: dict[str, Any]
) -> str:
    modell = str((konfig.get("modelle") or {}).get("ticket") or "claude-opus-5-5")
    effort = str((konfig.get("effort") or {}).get("ticket") or "medium")
    teile = [
        "env",
        f"TO_SPAWN_TICKET={ticket}",
        f"TO_SPAWN_SPEC={spec}",
        f"BAU_TICKET={ticket}",
        f"TO_SPAWN_LOG_REPO={wt}",
        f"TO_SPAWN_LOG_RUECKFALL={repo}",
        "claude",
        "--model",
        modell,
        "--effort",
        effort,
    ]
    return shlex.join(teile)


def _bereit(text: str) -> bool:
    """Eingabebereit: Prompt-Zeichen sichtbar, aber kein Startdialog."""
    if any(dialog in text for dialog in DIALOG_MARKER):
        return False
    return any(marker in text for marker in BEREIT_MARKER)


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


def _arbeitszeichen(text: str) -> int:
    return sum(text.count(m) for m in ARBEIT_MARKER)


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
class _Stand:
    """Was bisher passiert ist — entscheidet, wie ein Abbruch aufräumt."""

    alt: FensterInfo | None = None
    auftrag_getippt: bool = False
    neu: str | None = None
    beenden_begonnen: bool = False


class _Abbruch(Exception):
    """Geordneter Abbruch nach Schritt a: Exit-Code + Grund für die Zeile."""

    def __init__(self, exit_code: int, grund: str) -> None:
        super().__init__(grund)
        self.exit_code = exit_code
        self.grund = grund


def abloesen(
    repo: Path,
    spec: int,
    ticket: int,
    konfig: dict[str, Any],
    *,
    werkzeug: Werkzeug | None = None,
    warte_max: float = WARTE_MAX_VORGABE,
    dry_run: bool = False,
) -> Ergebnis:
    """Löst die Session in ``bau <ticket>`` nach SOP a–e ab (siehe Modul-Kommentar).

    Gibt nie eine Ausnahme weiter: jeder Fehler endet als :class:`Ergebnis` mit
    passendem Exit-Code und genau einer Zeile.
    """
    w = werkzeug or TmuxWerkzeug()
    auftrag = Auftrag(repo, spec, ticket, konfig, warte_max, dry_run)
    stand = _Stand()
    try:
        erg = _ablauf(auftrag, w, stand)
    except _Abbruch as abbruch:
        erg = _aufraeumen(auftrag, w, stand, abbruch.exit_code, abbruch.grund)
    except Exception as fehler:  # Tür gibt nie eine Ausnahme weiter
        log.exception("respawn #%s abgebrochen.", ticket)
        text = f"Fehler — {type(fehler).__name__}: {fehler}"
        if stand.beenden_begonnen:
            erg = Ergebnis(
                EXIT_NICHT_BEWIESEN,
                f"{auftrag.kopf}: {text} beim Beenden der alten Session — neue Session "
                f"arbeitet in „{auftrag.name_neu}“, alte evtl. noch offen, Handarbeit nötig",
            )
        else:
            erg = _aufraeumen(auftrag, w, stand, EXIT_NICHT_BEWIESEN, text)
    return Ergebnis(erg.exit, " ".join(erg.zeile.split()))


def _ablauf(a: Auftrag, w: Werkzeug, stand: _Stand) -> Ergebnis:
    """Schritte 0, a–e; Abbrüche nach a als :class:`_Abbruch`."""
    alt = _pruefe_duplikat(a, w)
    if isinstance(alt, Ergebnis):
        return alt
    stand.alt = alt

    wt_text = config.worktree_pfad(a.ticket, a.repo)
    befehl = _startbefehl(a.repo, a.spec, a.ticket, wt_text, a.konfig)
    if a.dry_run:
        return Ergebnis(
            EXIT_OK,
            f"{a.kopf}: dry-run — alt {alt.ziel}, neu „{a.name_neu}“ in {a.sitzung}, cwd {wt_text}, Befehl: {befehl}",
        )

    # a) Handoff-Auftrag ins alte Fenster
    seit = w.jetzt()
    handoff, start = _dateien(Path(wt_text), a.ticket, seit)
    rel_handoff = f"{HANDOFF_ORDNER}/{handoff.name}"
    w.tippen(alt.ziel, _handoff_auftrag(rel_handoff, f"{HANDOFF_ORDNER}/{start.name}"))
    stand.auftrag_getippt = True
    log.info("respawn #%s: Handoff-Auftrag an %s getippt.", a.ticket, alt.ziel)

    # b) neues Fenster, nackter Startbefehl
    stand.neu = w.fenster_starten(a.sitzung, a.name_neu, wt_text, befehl)
    log.info("respawn #%s: neues Fenster %s gestartet.", a.ticket, stand.neu)

    _remote_control(w, stand.neu)  # c)
    gefunden, prompt = _warte_dateien(a, w, handoff, start, seit)  # d)
    _start_prompt(w, stand.neu, prompt)  # e)
    return _alte_abloesen(a, w, stand, alt, stand.neu, gefunden.name)


def _pruefe_duplikat(a: Auftrag, w: Werkzeug) -> FensterInfo | Ergebnis:
    """Schritt 0 (A5): genau ein ``bau N``, kein ``bau N neu``, ≤ 1 bau.py — sonst Exit 3."""
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
    return alte[0]


def _remote_control(w: Werkzeug, neu: str) -> None:
    """Schritt c: bereit + ruhig abwarten, ``/remote-control`` tippen, Bestätigung prüfen."""
    if not _warte_ruhig_bereit(w, neu):
        raise _Abbruch(
            EXIT_NICHT_BEWIESEN,
            f"neue Session nach {int(BEREIT_MAX_S)} s nicht bereit",
        )
    w.tippen(neu, REMOTE_CONTROL)
    if _warte(w, REMOTE_MAX_S, lambda: _remote_bestaetigt(w.bildschirm(neu))):
        return
    # Slash-Menü kann das erste Enter als Auswahl schlucken → ein Nachschub-Enter.
    w.taste(neu, "Enter")
    if not _warte(w, REMOTE_MAX_S, lambda: _remote_bestaetigt(w.bildschirm(neu))):
        raise _Abbruch(
            EXIT_NICHT_BEWIESEN,
            "Remote Control im neuen Fenster nicht bestätigt (kein Hinweis auf dem Bildschirm)",
        )


def _warte_ruhig_bereit(w: Werkzeug, ziel: str) -> bool:
    """Bereit-Bildschirm, der :data:`EINGABE_RUHE_S` lang unverändert steht."""
    ruhig: dict[str, Any] = {"text": None, "seit": 0.0}

    def bedingung() -> bool:
        text = w.bildschirm(ziel)
        if not _bereit(text):
            ruhig["text"] = None
            return False
        if text != ruhig["text"]:
            ruhig["text"], ruhig["seit"] = text, w.jetzt()
            return False
        return w.jetzt() - ruhig["seit"] >= EINGABE_RUHE_S

    return _warte(w, BEREIT_MAX_S, bedingung)


def _warte_dateien(
    a: Auftrag, w: Werkzeug, handoff: Path, start: Path, seit: float
) -> tuple[Path, str]:
    """Schritt d (G5): Handoff + Start-Prompt frisch, nicht leer, Größe stabil."""
    letzte: dict[str, Any] = {"stand": None}
    fund: dict[str, Path] = {}

    def bedingung() -> bool:
        h, s = _finde(handoff, a.ticket, seit), _finde(start, a.ticket, seit)
        stand = (h, s, _groesse(h), _groesse(s))
        stabil = h is not None and s is not None and stand == letzte["stand"]
        letzte["stand"] = stand
        if stabil and h and s:
            fund["handoff"], fund["start"] = h, s
        return stabil

    if not _warte(w, a.warte_max, bedingung):
        fehlt = [
            p.name for p in (handoff, start) if _finde(p, a.ticket, seit) is None
        ] or ["stabile Dateien"]
        raise _Abbruch(
            EXIT_HANDOFF_FEHLT, f"nach {int(a.warte_max)} s fehlt {', '.join(fehlt)}"
        )
    prompt = fund["start"].read_text(encoding="utf-8").strip()
    if not prompt:
        raise _Abbruch(
            EXIT_HANDOFF_FEHLT, f"Start-Prompt {fund['start'].name} ist leer"
        )
    return fund["handoff"], prompt


def _start_prompt(w: Werkzeug, neu: str, prompt: str) -> None:
    """Schritt e (G4): Prompt tippen, auf echtes Arbeitszeichen warten (Echo zählt nicht)."""
    vorher = _arbeitszeichen(w.bildschirm(neu)) + _arbeitszeichen(prompt)
    w.tippen(neu, prompt)
    if not _warte(
        w, BILDSCHIRM_MAX_S, lambda: _arbeitszeichen(w.bildschirm(neu)) > vorher
    ):
        raise _Abbruch(
            EXIT_NICHT_BEWIESEN,
            f"neue Session zeigt {int(BILDSCHIRM_MAX_S)} s nach dem Start-Prompt keine Arbeit",
        )


def _alte_abloesen(
    a: Auftrag, w: Werkzeug, stand: _Stand, alt: FensterInfo, neu: str, handoff: str
) -> Ergebnis:
    """Alte Session beenden, altes Fenster zu, neues in ``bau N`` umbenennen."""
    stand.beenden_begonnen = True
    ausgang = w.alte_session_beenden(alt.pane_pid)
    if ausgang == LEBT:
        return Ergebnis(
            EXIT_NICHT_BEWIESEN,
            f"{a.kopf}: neue Session arbeitet in „{a.name_neu}“, alte (Pane-PID {alt.pane_pid}) "
            "lebt noch — zwei Sessions offen, Handarbeit nötig",
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
        except Exception as fehler:  # Zustand gehört in die Zeile
            log.exception("respawn #%s: Umbenennen gescheitert.", a.ticket)
            probleme.append(f"Umbenennen in „{a.name_alt}“ gescheitert ({fehler})")
    if probleme:
        return Ergebnis(
            EXIT_NICHT_BEWIESEN,
            f"{a.kopf}: alte Session beendet, neue arbeitet in „{a.name_neu}“ — "
            f"{'; '.join(probleme)}; Handarbeit nötig",
        )
    return Ergebnis(
        EXIT_OK,
        f"{a.kopf}: ok — neue Session im Fenster {a.name_alt}, Handoff {HANDOFF_ORDNER}/{handoff}",
    )


def _fenster_offen(w: Werkzeug, ziel: str) -> bool:
    """Fenster noch da? Unklar (tmux-Fehler) zählt als offen."""
    try:
        return any(f.ziel == ziel for f in w.fenster_liste())
    except Exception:  # Unklarheit ehrlich als „offen“ melden
        log.exception("Fensterliste nicht lesbar — %s gilt als offen.", ziel)
        return True


def _aufraeumen(
    a: Auftrag, w: Werkzeug, stand: _Stand, exit_code: int, grund: str
) -> Ergebnis:
    """Abbruch vor dem Beenden: neues Fenster zu, alte Session weiterarbeiten lassen.

    Die Zeile sagt nur, was wirklich geklappt hat.
    """
    teile = [f"{a.kopf}: {grund}"]
    if stand.neu:
        try:
            w.fenster_schliessen(stand.neu)
        except Exception:  # Prüfung folgt über die Fensterliste
            log.exception("respawn #%s: neues Fenster nicht schließbar.", a.ticket)
        if _fenster_offen(w, stand.neu):
            teile.append(
                f"zwei Sessions offen („{a.name_neu}“ ließ sich nicht schließen), Handarbeit nötig"
            )
        else:
            teile.append("neues Fenster zu")
    if stand.auftrag_getippt and stand.alt:
        try:
            w.tippen(stand.alt.ziel, WEITER_AUFTRAG)
            teile.append("Ablösung abgebrochen, alte Session arbeitet weiter")
        except Exception:  # Zustand gehört in die Zeile
            log.exception("respawn #%s: Weiter-Auftrag nicht getippt.", a.ticket)
            teile.append(
                "alte Session hat den Handoff-Auftrag und wartet untätig, Handarbeit nötig"
            )
    elif stand.alt:
        teile.append("alte Session unangetastet")
    return Ergebnis(exit_code, " — ".join(teile))
