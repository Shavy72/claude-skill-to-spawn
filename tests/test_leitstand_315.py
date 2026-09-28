"""Ticket #315: Leitstand — ``stand`` darf fehlende Rechte nie als „frei“ melden,
und ein Halter (``python -m to_spawn.leitstand halte``) verwaist nie, wenn sein
Elternprozess (Deploy-Dienst) stirbt.

Echte Prozesse, echte Datei-Sperren, eigener Leitstand-Ordner je Test.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
PY = sys.executable

nur_linux = pytest.mark.skipif(
    sys.platform == "win32", reason="Linux-Semantik — Bau-Server"
)


def _umgebung(ordner: Path) -> dict:
    return {**os.environ, "TO_SPAWN_LEITSTAND_ORDNER": str(ordner)}


def _stand(ordner: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            PY,
            str(SKILL / "to_spawn.py"),
            "leitstand",
            "stand",
            "--name",
            "nest",
            "--json",
        ],
        cwd=str(SKILL),
        env=_umgebung(ordner),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _warte_bis(bedingung, zeitlimit_s: float = 20.0) -> bool:
    ende = time.monotonic() + zeitlimit_s
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.05)
    return False


@nur_linux
@pytest.mark.skipif(
    sys.platform != "win32" and os.geteuid() == 0,
    reason="root darf alles — Test nicht aussagekräftig",
)
def test_stand_ohne_schreibrecht_meldet_fehler_statt_frei(tmp_path: Path) -> None:
    sperren = tmp_path / "sperren"
    sperren.mkdir()
    lock = sperren / "nest.lock"
    lock.write_text("", encoding="utf-8")
    lock.chmod(0o444)
    try:
        ausgabe = _stand(tmp_path)
    finally:
        lock.chmod(0o644)
    assert ausgabe.returncode != 0, f"stand meldete still: {ausgabe.stdout!r}"
    assert "nest" in ausgabe.stderr and "Fehler" in ausgabe.stderr


_ELTERN = """
import subprocess, sys
import time
from pathlib import Path
subprocess.Popen([sys.executable, "-m", "to_spawn.leitstand", "halte", "nest", "deploy-schlange", "--bis", sys.argv[1]])
ende = time.monotonic() + 20
while not Path(sys.argv[1] + ".hat").exists() and time.monotonic() < ende:
    time.sleep(0.05)
"""


@nur_linux
def test_halter_gibt_frei_wenn_elternprozess_stirbt(tmp_path: Path) -> None:
    stop = tmp_path / "halter.stop"
    subprocess.run(
        [PY, "-c", _ELTERN, str(stop)],
        cwd=str(SKILL),
        env=_umgebung(tmp_path),
        timeout=30,
        check=True,
    )
    try:
        assert _warte_bis(Path(f"{stop}.hat").exists), (
            "Halter hat das Nest nie genommen"
        )
        assert _warte_bis(
            lambda: json.loads(_stand(tmp_path).stdout)[0]["halter"] is None
        ), "Halter hält das Nest weiter, obwohl sein Elternprozess tot ist"
    finally:
        stop.touch()
