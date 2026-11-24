"""#553: Eine Attrappe startet per Pfad und per Name (PATH) auf jedem Betriebssystem."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import attrappe


def test_attrappe_startet_per_rueckgabepfad(tmp_path: Path) -> None:
    skript = attrappe(tmp_path, "gh", "import sys\nprint('hallo', *sys.argv[1:])\n")
    erg = subprocess.run([str(skript), "x"], capture_output=True, text=True, check=True)
    assert erg.stdout.split() == ["hallo", "x"]


def test_attrappe_startet_per_name_ueber_path_mit_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attrappe(tmp_path, "meinwerkzeug", "import sys\nprint('a', *sys.argv[1:])\nsys.exit(3)\n")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    erg = subprocess.run([shutil.which("meinwerkzeug"), "x", "y"], capture_output=True, text=True, check=False)
    assert (erg.returncode, erg.stdout.split()) == (3, ["a", "x", "y"])
