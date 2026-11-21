"""#37: spawn_local.ps1 nimmt die Ticket-Liste auch als einen String „51,52“ (Aufruf per -File) an."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SKRIPT = Path(__file__).resolve().parents[1] / "spawn_local.ps1"
PS = shutil.which("pwsh") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(PS is None, reason="weder pwsh noch powershell vorhanden")


def _lauf(tmp_path: Path, tickets: str) -> subprocess.CompletedProcess[str]:
    # Leeres Wegwerf-Repo: der Regularien-Prüfer weigert/bricht ab, es startet nie ein Fenster.
    return subprocess.run(
        [str(PS), "-NoProfile", "-File", str(SKRIPT), "-Spec", "48", "-Tickets", tickets, "-DryRun"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False,
    )


def test_liste_als_ein_string_wird_nicht_als_int_array_abgelehnt(tmp_path: Path) -> None:
    lauf = _lauf(tmp_path, "51,52")
    assert "Int32" not in lauf.stdout + lauf.stderr


def test_keine_zahl_gibt_klare_meldung_und_exit_2(tmp_path: Path) -> None:
    lauf = _lauf(tmp_path, "51,abc")
    assert lauf.returncode == 2
    assert "abc" in lauf.stdout + lauf.stderr
