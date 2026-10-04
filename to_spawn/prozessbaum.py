"""Prozessbaum eines tmux-Panes ermitteln, prüfen und beenden — an genau einer Stelle.

respawn (alte Session ablösen) und aufpasser (Fenster schließen/fortsetzen) beenden
beide den ganzen Prozessbaum eines Panes. Wie der Baum aus ``/proc`` gelesen wird,
was „lebt“ heißt, wie eine Claude-Session erkannt wird und wie Signale stufenweise
eskalieren, steht nur hier. Die Aufrufer wählen nur Stufen (Signal + Wartezeit),
Reihenfolge der PIDs und worauf gewartet wird.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from collections.abc import Callable, Sequence
from pathlib import Path

log = logging.getLogger(__name__)

_PROC = Path("/proc")

Stufe = tuple[signal.Signals, float]
"""Ein Signal und wie lange danach höchstens gewartet wird (Sekunden)."""


def _kinder() -> dict[int, list[int]]:
    """Eltern-PID → Kind-PIDs aus ``/proc/*/stat``; ``/proc`` selbst unlesbar = Fehler."""
    kinder: dict[int, list[int]] = {}
    for eintrag in _PROC.iterdir():
        if not eintrag.name.isdigit():
            continue
        try:
            stat = (eintrag / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # ``pid (comm) zustand ppid …`` — comm darf Leerzeichen und Klammern enthalten.
        rest = stat[stat.rfind(")") + 2 :].split()
        if len(rest) < 2:
            continue
        kinder.setdefault(int(rest[1]), []).append(int(eintrag.name))
    return kinder


def baum(pid: int) -> list[int]:
    """``pid`` selbst und alle Nachfahren (Breite zuerst, ``pid`` vorn)."""
    kinder = _kinder()
    raus, warteschlange = [], [pid]
    while warteschlange:
        p = warteschlange.pop(0)
        raus.append(p)
        warteschlange.extend(kinder.get(p, []))
    return raus


def lebt(pid: int) -> bool:
    """Prozess existiert noch (``/proc/<pid>`` vorhanden, auch Zombies)."""
    return (_PROC / str(pid)).exists()


def _proc(pid: int, datei: str) -> bytes:
    """Rohinhalt von ``/proc/<pid>/<datei>`` (Naht für Tests)."""
    return (_PROC / str(pid) / datei).read_bytes()


def ist_claude(pid: int) -> bool:
    """Ist ``pid`` eine Claude-Session — nativ (``claude``) oder per npm (``node … claude``)?

    Prozess weg (``FileNotFoundError``/``ProcessLookupError``) heißt „kein Claude“.
    Unlesbar aus anderem Grund zählt als Claude: lieber warten als eine lebende
    Session für beendet halten.
    """
    try:
        name = _proc(pid, "comm").decode(errors="replace").strip()
        argv = [
            teil.decode(errors="replace") for teil in _proc(pid, "cmdline").split(b"\0")
        ]
    except (FileNotFoundError, ProcessLookupError):
        return False
    except OSError as fehler:
        log.warning("/proc/%s nicht lesbar (%s) — zählt als Claude.", pid, fehler)
        return True
    argv0 = argv[0] if argv else ""
    if name == "claude" or Path(argv0).name == "claude":
        return True
    if name != "node" and Path(argv0).name != "node":
        return False
    skript = argv[1] if len(argv) > 1 else ""
    return Path(skript).name == "claude" or skript.endswith("claude-code/cli.js")


def senden(pids: Sequence[int], sig: signal.Signals) -> None:
    """Schickt ``sig`` an jede PID in Listenfolge; schon beendete zählen nicht als Fehler."""
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            continue


def _warte_tot(
    pids: Sequence[int],
    warte_s: float,
    takt_s: float,
    schlafen: Callable[[float], None],
) -> bool:
    """True, sobald keiner der ``pids`` mehr lebt; höchstens ``warte_s / takt_s`` Takte."""
    for _ in range(round(warte_s / takt_s)):
        if not any(lebt(pid) for pid in pids):
            return True
        schlafen(takt_s)
    return False


def beenden(
    pids: Sequence[int],
    stufen: Sequence[Stufe],
    *,
    warten_auf: Sequence[int] | None = None,
    takt_s: float = 0.1,
    schlafen: Callable[[float], None] | None = None,
    eskalation: Callable[[signal.Signals, list[int]], None] | None = None,
) -> bool:
    """Beendet ``pids`` stufenweise (z. B. SIGTERM, dann SIGKILL).

    Je Stufe bekommen die noch lebenden ``pids`` das Signal — in der Reihenfolge der
    Liste —, dann wird bis zur Wartezeit der Stufe im Takt ``takt_s`` gewartet, bis
    keiner aus ``warten_auf`` (Vorgabe: alle ``pids``) mehr lebt. ``eskalation`` hört
    vor jeder Folgestufe, welches Signal an welche Überlebenden geht (für Logzeilen).
    ``schlafen`` (Vorgabe: ``time.sleep``) ist die Uhr-Naht für Tests.
    Ergebnis: True, wenn am Ende keiner aus ``warten_auf`` mehr lebt.
    """
    schlafen = schlafen or time.sleep
    ziel = list(pids) if warten_auf is None else list(warten_auf)
    for nr, (sig, warte_s) in enumerate(stufen):
        lebende = [pid for pid in pids if lebt(pid)]
        if not lebende:
            return True
        if nr and eskalation is not None:
            eskalation(sig, lebende)
        senden(lebende, sig)
        if _warte_tot(ziel, warte_s, takt_s, schlafen):
            return True
    return not any(lebt(pid) for pid in ziel)
