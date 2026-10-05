"""Aufpasser lädt auf jeder Plattform, auch ohne SIGHUP/SIGKILL (#592).

``BEENDEN_STUFEN`` las beim Import ``signal.SIGHUP``; das gibt es unter Windows nicht.
Folge: ``to_spawn.aufpasser`` ließ sich dort nicht laden, ``wache`` stürzte lokal ab.

Weg über einen echten Python-Prozess, der das Modul frisch importiert. Die Plattform
wird nur am ``signal``-Modul nachgestellt (Signale entfernen bzw. setzen) — reine
Logik, kein Prozess bekommt ein Signal.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
OHNE_FENSTER = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _stufen_im_frischen_prozess(vorbereitung: str) -> list[list[float]]:
    """Importiert ``to_spawn.aufpasser`` frisch, liefert ``BEENDEN_STUFEN``."""
    code = (
        "import json, signal, sys\n"
        f"sys.path.insert(0, {str(SKILL)!r})\n"
        f"{vorbereitung}\n"
        "from to_spawn import aufpasser\n"
        "print(json.dumps([[int(s), w] for s, w in aufpasser.BEENDEN_STUFEN]))\n"
    )
    lauf = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        creationflags=OHNE_FENSTER,
    )
    assert lauf.returncode == 0, lauf.stderr
    return json.loads(lauf.stdout.strip().splitlines()[-1])


def test_import_ohne_sighup_und_sigkill_wie_windows() -> None:
    """Fehlen SIGHUP und SIGKILL (Windows), bleibt SIGTERM als einzige Stufe."""
    ohne = (
        "for name in ('SIGHUP', 'SIGKILL'):\n"
        "    if hasattr(signal, name):\n"
        "        delattr(signal, name)"
    )
    assert _stufen_im_frischen_prozess(ohne) == [[int(signal.SIGTERM), 5.0]]


def test_mit_allen_signalen_stufen_wie_auf_linux() -> None:
    """Gibt es SIGHUP und SIGKILL (Linux), bleiben die drei Stufen samt Reihenfolge."""
    mit = "signal.SIGHUP = 1\nsignal.SIGKILL = 9"
    assert _stufen_im_frischen_prozess(mit) == [
        [1, 5.0],
        [int(signal.SIGTERM), 5.0],
        [9, 5.0],
    ]
