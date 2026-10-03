"""Bau-Session nach fester SOP ablösen (#431, Spec #399: E2, E6, E7, E16).

Eine Tür: :func:`abloesen`. Sie löst die laufende Claude-Session im tmux-Fenster
``bau <N>`` durch eine frische ab — immer in derselben Reihenfolge:

0. Duplikat-Prüfung (A5): genau ein Fenster ``bau <N>``, kein ``bau <N> neu``,
   höchstens ein ``bau.py``-Prozess für N — sonst Exit 3, nichts anfassen.
a) Handoff-Auftrag ins alte Fenster (Handoff + Start-Prompt mit festen Pfaden).
b) neues Fenster ``bau <N> neu`` mit nacktem ``claude --model … --effort …``
   (nie ein Start-Prompt als Argument, V4).
c) warten bis bereit, dann ``/remote-control`` tippen (E7).
d) warten, bis Handoff UND Start-Prompt frisch (nach Schritt a) und nicht leer da
   sind (G5) — sonst Exit 2, neues Fenster zu, alte Session unangetastet.
e) Start-Prompt ins neue Fenster tippen, Bildschirm prüfen (G4) — erst dann alte
   Session beenden und das neue Fenster in ``bau <N>`` umbenennen.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from to_spawn import capo, config

log = logging.getLogger(__name__)

#: Bildschirm-Text, an dem eine frisch gestartete Claude-Session eingabebereit ist.
BEREIT_MARKER: tuple[str, ...] = ("❯", "? for shortcuts")
BEREIT_MAX_S = 120.0
BILDSCHIRM_MAX_S = 30.0
TAKT_S = 2.0
WARTE_MAX_VORGABE = 1800.0
REMOTE_CONTROL = "/remote-control"
HANDOFF_ORDNER = "docs/handoffs"

EXIT_OK = 0
EXIT_NICHT_BEWIESEN = 1
EXIT_HANDOFF_FEHLT = 2
EXIT_DUPLIKAT = 3


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


class Werkzeug(Protocol):
    """Alle Außenwelt-Zugriffe der Ablösung (Naht für Tests)."""

    def fenster_liste(self) -> list[FensterInfo]: ...
    def bau_prozesse(self) -> list[str]: ...
    def fenster_starten(
        self, sitzung: str, name: str, cwd: str, befehl: str
    ) -> str: ...
    def tippen(self, ziel: str, text: str) -> None: ...
    def bildschirm(self, ziel: str) -> str: ...
    def fenster_umbenennen(self, ziel: str, name: str) -> None: ...
    def fenster_schliessen(self, ziel: str) -> None: ...
    def alte_session_beenden(self, pane_pid: int) -> bool: ...
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
        try:
            roh = self._tmux(
                "list-panes",
                "-a",
                "-F",
                "#{session_name}\t#{window_name}\t#{window_id}\t#{pane_pid}\t#{pane_index}",
            )
        except subprocess.CalledProcessError:
            # Kein tmux-Server = keine Fenster.
            return []
        fenster: list[FensterInfo] = []
        for zeile in roh.splitlines():
            teile = zeile.split("\t")
            if len(teile) != 5 or teile[4] != "0":
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
        time.sleep(1)
        self._tmux("send-keys", "-t", ziel, "Enter")

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

    def alte_session_beenden(self, pane_pid: int) -> bool:
        opfer = [pid for pid in _nachkommen(pane_pid) if _ist_claude(pid)]
        if not opfer:
            log.warning("Unter Pane-PID %s läuft kein claude-Prozess.", pane_pid)
            return False
        for pid in opfer:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
        ende = time.monotonic() + 30
        while time.monotonic() < ende:
            if not any(_lebt(pid) for pid in opfer):
                return True
            time.sleep(1)
        log.error("claude-Prozess(e) %s leben 30 s nach SIGTERM noch.", opfer)
        return False

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
        for zeile in fertig.stdout.split():
            if zeile.isdigit() and int(zeile) not in gefunden:
                gefunden.append(int(zeile))
                offen.append(int(zeile))
    return gefunden


def _ist_claude(pid: int) -> bool:
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
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --- Kern ------------------------------------------------------------------------


def _bau_pid_fuer(zeile: str, ticket: int) -> bool:
    return re.search(rf"bau\.py\s+{ticket}(\s|$)", zeile) is not None


def _dateien(wt: Path, ticket: int, seit: float) -> tuple[Path, Path]:
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
    return any(marker in text for marker in BEREIT_MARKER)


def _warte(werkzeug: Werkzeug, max_s: float, bedingung: Any) -> bool:
    ende = werkzeug.jetzt() + max_s
    while True:
        if bedingung():
            return True
        if werkzeug.jetzt() >= ende:
            return False
        werkzeug.schlafen(TAKT_S)


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
    kopf = f"respawn #{ticket}"
    try:
        erg = _abloesen(repo, spec, ticket, konfig, w, warte_max, dry_run, kopf)
        return Ergebnis(erg.exit, " ".join(erg.zeile.split()))
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as fehler:
        log.exception("respawn #%s abgebrochen.", ticket)
        text = " ".join(str(fehler).split())
        return Ergebnis(EXIT_NICHT_BEWIESEN, f"{kopf}: Fehler — {text}")


def _abloesen(
    repo: Path,
    spec: int,
    ticket: int,
    konfig: dict[str, Any],
    w: Werkzeug,
    warte_max: float,
    dry_run: bool,
    kopf: str,
) -> Ergebnis:
    # 0. Duplikat-Prüfung (A5)
    name_alt, name_neu = f"bau {ticket}", f"bau {ticket} neu"
    fenster = w.fenster_liste()
    alte = [f for f in fenster if f.name == name_alt]
    neue = [f for f in fenster if f.name == name_neu]
    prozesse = [z for z in w.bau_prozesse() if _bau_pid_fuer(z, ticket)]
    if neue:
        return Ergebnis(
            EXIT_DUPLIKAT,
            f"{kopf}: Duplikat — Fenster „{name_neu}“ gibt es schon, nichts angefasst",
        )
    if len(alte) != 1:
        grund = "kein Fenster" if not alte else f"{len(alte)} Fenster"
        return Ergebnis(
            EXIT_DUPLIKAT, f"{kopf}: {grund} „{name_alt}“ — nichts angefasst"
        )
    if len(prozesse) > 1:
        return Ergebnis(
            EXIT_DUPLIKAT,
            f"{kopf}: {len(prozesse)} bau.py-Prozesse für #{ticket} — nichts angefasst",
        )
    alt = alte[0]

    wt_text = config.worktree_pfad(ticket, repo)
    wt = Path(wt_text)
    befehl = _startbefehl(repo, spec, ticket, wt_text, konfig)
    sitzung = f"spec-{spec}"
    if dry_run:
        return Ergebnis(
            EXIT_OK,
            f"{kopf}: dry-run — alt {alt.ziel}, neu „{name_neu}“ in {sitzung}, cwd {wt_text}, Befehl: {befehl}",
        )

    # a) Handoff-Auftrag ins alte Fenster
    seit = w.jetzt()
    handoff, start = _dateien(wt, ticket, seit)
    rel_handoff = f"{HANDOFF_ORDNER}/{handoff.name}"
    rel_start = f"{HANDOFF_ORDNER}/{start.name}"
    w.tippen(alt.ziel, _handoff_auftrag(rel_handoff, rel_start))
    log.info("respawn #%s: Handoff-Auftrag an %s getippt.", ticket, alt.ziel)

    # b) neues Fenster, nackter Startbefehl
    neu = w.fenster_starten(sitzung, name_neu, wt_text, befehl)
    log.info("respawn #%s: neues Fenster %s gestartet.", ticket, neu)

    # c) bereit → /remote-control
    if not _warte(w, BEREIT_MAX_S, lambda: _bereit(w.bildschirm(neu))):
        w.fenster_schliessen(neu)
        return Ergebnis(
            EXIT_NICHT_BEWIESEN,
            f"{kopf}: neue Session nach {int(BEREIT_MAX_S)} s nicht bereit — neues Fenster zu, alte läuft weiter",
        )
    w.tippen(neu, REMOTE_CONTROL)

    # d) Handoff + Start-Prompt frisch da?
    if not _warte(
        w, warte_max, lambda: _frisch(handoff, seit) and _frisch(start, seit)
    ):
        w.fenster_schliessen(neu)
        fehlt = [p.name for p in (handoff, start) if not _frisch(p, seit)]
        return Ergebnis(
            EXIT_HANDOFF_FEHLT,
            f"{kopf}: nach {int(warte_max)} s fehlt {', '.join(fehlt)} — neues Fenster zu, alte läuft weiter",
        )

    # e) Start-Prompt tippen, Bildschirm prüfen (G4)
    prompt = start.read_text(encoding="utf-8").strip()
    vorher = w.bildschirm(neu)
    w.tippen(neu, prompt)

    def arbeitet() -> bool:
        jetzt_text = w.bildschirm(neu)
        return bool(jetzt_text.strip()) and jetzt_text != vorher

    if not _warte(w, BILDSCHIRM_MAX_S, arbeitet):
        return Ergebnis(
            EXIT_NICHT_BEWIESEN,
            f"{kopf}: neue Session ({neu}) zeigt nach dem Start-Prompt keine Arbeit — alte Session läuft weiter",
        )

    # Erst jetzt: alte Session beenden, neues Fenster umbenennen
    if not w.alte_session_beenden(alt.pane_pid):
        return Ergebnis(
            EXIT_NICHT_BEWIESEN,
            f"{kopf}: neue Session läuft in „{name_neu}“, alte Session (Pane-PID {alt.pane_pid}) nicht beendet",
        )
    # Altes Fenster zu: kein zweites „bau N“, und bau.py dort startet keine Folge-Runde.
    w.fenster_schliessen(alt.ziel)
    w.fenster_umbenennen(neu, name_alt)
    return Ergebnis(
        EXIT_OK,
        f"{kopf}: ok — neue Session im Fenster {name_alt}, Handoff {rel_handoff}",
    )
