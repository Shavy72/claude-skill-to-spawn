"""Setup zeigt Remote Control so, wie der Wächter startet (duoplus-management#213).

Befund verify-hard Lauf 3: ``setup --zeigen`` meldete „noch nicht wählbar“,
obwohl ``waechter.remote_control`` Standard an ist und ``wache`` mit
``--remote-control`` startet. Weg-Test über die echte Befehlszeile.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    subprocess.run(["git", "init"], cwd=str(arbeit), check=True, capture_output=True)
    return arbeit


def _zeigen(repo: Path) -> str:
    ergebnis = subprocess.run(
        [sys.executable, str(CLI), "setup", "--plattform", "linux", "--zeigen"],
        cwd=str(repo),
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    return ergebnis.stdout


def _remote_zeile(text: str) -> str:
    zeilen = [z for z in text.splitlines() if "Remote Control" in z]
    assert len(zeilen) == 1, text
    return zeilen[0]


def test_weg_remote_control_standard_an(repo: Path) -> None:
    zeile = _remote_zeile(_zeigen(repo))
    assert "noch nicht wählbar" not in zeile
    assert "geplant" not in zeile
    assert ": an" in zeile


def test_weg_remote_control_aus_laut_konfig(repo: Path) -> None:
    datei = repo / ".to-spawn" / "config.json"
    datei.parent.mkdir(parents=True)
    datei.write_text(
        json.dumps({"waechter": {"remote_control": False}}), encoding="utf-8"
    )
    zeile = _remote_zeile(_zeigen(repo))
    assert ": aus" in zeile
