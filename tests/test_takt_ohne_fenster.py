"""Wächter-Takt öffnet unter Windows keine Fenster (Befund 27.09.2026).

Der Takt lief mit ``DETACHED_PROCESS`` — ohne Konsole. Jeder Kind-Aufruf
(git, gh, ssh) bekam dann eine eigene neue Konsole, und Windows Terminal
riss dafür ~1 Fenster pro Sekunde nach vorn. Soll: der Takt hat eine
unsichtbare Konsole, die alle Kinder erben — kein Fenster, kein Fokus-Klau.

Weg-Test ohne Attrappen: echter Kind-Prozess mit denselben Startoptionen
wie ``starte_abgeloest``, der seine eigene Konsole prüft.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from to_spawn import waechter_takt

PRUEFER = r"""
import ctypes, json, os, subprocess, sys
LISTE = (
    "import ctypes\n"
    "a = (ctypes.c_uint * 64)()\n"
    "n = ctypes.windll.kernel32.GetConsoleProcessList(a, 64)\n"
    "print(','.join(str(a[i]) for i in range(min(n, 64))))\n"
)
k = ctypes.windll.kernel32
u = ctypes.windll.user32
a = (ctypes.c_uint * 64)()
n = k.GetConsoleProcessList(a, 64)   # 0 = an keiner Konsole
h = k.GetConsoleWindow()
# Ein Konsolen-Kind startet: hängt es an derselben Konsole wie wir, entsteht
# kein neues Fenster.
kind = subprocess.run([sys.executable, "-c", LISTE], capture_output=True, text=True, check=True)
kind_pids = {int(x) for x in kind.stdout.strip().split(",") if x}
print(json.dumps({
    "konsole": n > 0,
    "sichtbar": bool(h and u.IsWindowVisible(h)),
    "kind_gleiche_konsole": os.getpid() in kind_pids,
}))
"""


@pytest.mark.skipif(sys.platform != "win32", reason="nur Windows-Konsolen")
def test_takt_startoptionen_geben_unsichtbare_geerbte_konsole() -> None:
    optionen = waechter_takt.abgeloest_optionen()
    lauf = subprocess.run(  # noqa: S603 — fester Befehl
        [sys.executable, "-c", PRUEFER],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        **optionen,
    )
    stand = json.loads(lauf.stdout.strip().splitlines()[-1])
    assert stand["konsole"], "Takt ohne Konsole → jedes git/gh/ssh öffnet ein Fenster"
    assert not stand["sichtbar"], "Konsole des Takts ist sichtbar"
    assert stand["kind_gleiche_konsole"], "Kind-Aufruf bekam eine eigene Konsole"


@pytest.mark.skipif(sys.platform != "win32", reason="nur Windows-Konsolen")
def test_takt_startoptionen_ohne_detached_process() -> None:
    flags = waechter_takt.abgeloest_optionen()["creationflags"]
    assert not flags & subprocess.DETACHED_PROCESS
    assert flags & subprocess.CREATE_NO_WINDOW
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
