"""#553: Eine Attrappe startet per Pfad und per Name (PATH) auf jedem Betriebssystem."""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import attrappe


def test_attrappe_startet_per_rueckgabepfad(tmp_path: Path) -> None:
    skript = attrappe(tmp_path, "gh", "import sys\nprint('hallo', *sys.argv[1:])\n")
    erg = subprocess.run([str(skript), "x"], capture_output=True, text=True, check=True)
    assert erg.stdout.split() == ["hallo", "x"]
