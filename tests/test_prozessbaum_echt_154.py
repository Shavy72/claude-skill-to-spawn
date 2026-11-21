"""Skill-Issue #154: der Kernel-Weg unter Windows gegen einen echten Prozessbaum.

Keine Attrappen: ein Elternprozess startet ein Kind, beide müssen im Baum, in
``prozesse()`` (mit Kommandozeile und Startzeit) und in der Vorfahren-Kette auftauchen;
nach dem Beenden dürfen sie nirgends mehr stehen.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest

from to_spawn import prozessbaum

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="echter Windows-Prozessbaum")

#: Basis-Python statt venv-Starter: sonst schöbe sich ein Zwischenprozess in den Baum.
PYTHON = getattr(sys, "_base_executable", sys.executable)


def _kind_skript(marke: str) -> str:
    """Eltern-Skript: startet ein schlafendes Kind mit ``marke`` in der Kommandozeile."""
    return (
        "import subprocess,sys,time; "
        f"k=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)','{marke}-kind']); "
        "print(k.pid, flush=True); time.sleep(60)"
    )


def test_echter_baum_mit_kommandozeile_und_ende() -> None:
    marke = f"p154-{uuid.uuid4().hex[:8]}"
    eltern = subprocess.Popen(
        [PYTHON, "-c", _kind_skript(marke), f"{marke}-eltern"],
        stdout=subprocess.PIPE,
        text=True,
        **prozessbaum.ohne_fenster(),
    )
    kind = 0
    try:
        assert eltern.stdout is not None
        kind = int(eltern.stdout.readline().strip())

        # Unter Windows hängt am Konsolen-Prozess zusätzlich ``conhost.exe`` — daher kein ``==``.
        teile = prozessbaum.baum(eltern.pid)
        assert teile[0] == eltern.pid and kind in teile
        liste = prozessbaum.prozesse()
        assert liste[kind][0] == eltern.pid
        assert f"{marke}-kind" in liste[kind][1]
        assert f"{marke}-eltern" in liste[eltern.pid][1]
        assert liste[os.getpid()][0] == os.getppid()
        # PID-Wiederverwendung erkennt das Modul an der Startzeit: Kind startet nach Eltern.
        assert 0 < liste[eltern.pid][2] <= liste[kind][2]
        # Vorfahren-Kette lädt die Kommandozeile nur der Vorfahren nach.
        vorfahren = dict(zip(range(3), prozessbaum._vorfahren_windows(kind)))
        assert vorfahren[0][0] == eltern.pid and f"{marke}-eltern" in vorfahren[0][1]
        assert vorfahren[1][0] == os.getpid()

        assert prozessbaum.beenden([kind, eltern.pid], ((prozessbaum.signal.SIGTERM, 5.0),))
        liste = prozessbaum.prozesse()
        assert eltern.pid not in liste or f"{marke}-eltern" not in liste[eltern.pid][1]
        assert kind not in liste or f"{marke}-kind" not in liste[kind][1]
        assert not prozessbaum.lebt(eltern.pid) and not prozessbaum.lebt(kind)
        assert kind not in prozessbaum.baum(eltern.pid)
    finally:
        for pid in (kind, eltern.pid):
            if pid and prozessbaum.lebt(pid):
                prozessbaum.senden([pid], prozessbaum.signal.SIGTERM)
        eltern.wait(timeout=10)
        if eltern.stdout is not None:
            eltern.stdout.close()
