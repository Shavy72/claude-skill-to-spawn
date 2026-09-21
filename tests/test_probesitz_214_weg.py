"""Probesitz (#214) — echter Weg ohne Attrappe: Login-Abfrage und Sandbox-Sperre.

Übersprungen nur, wenn das Werkzeug auf der Maschine fehlt (``claude``, ``srt``/``bwrap``).
Der Wegwerf-Lauf (Punkte 2/5/6) und die Mail (Punkt 7) laufen hier bewusst nicht:
sie kosten Token/Mail und gehören zum Live-Probesitz der Hauptsession.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import config, probesitz  # noqa: E402


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(arbeit), check=True, capture_output=True)
    monkeypatch.setenv("TO_SPAWN_WAECHTER_ORDNER", str(tmp_path / "zustand"))
    return arbeit


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude nicht installiert")
def test_weg_p1_echter_login_status() -> None:
    erg = probesitz.pruefe_login()
    # Kein Urteil über den Login selbst — nur: die Prüfung liefert ein belastbares Ergebnis.
    assert erg.nummer == 1
    assert erg.grund
    if not erg.ok:
        assert erg.fehlt_noch


@pytest.mark.skipif(
    shutil.which("srt") is None or shutil.which("bwrap") is None,
    reason="srt/bwrap nicht installiert",
)
def test_weg_p4_echte_sandbox_sperrt_aussen(repo: Path) -> None:
    erg = probesitz.pruefe_sandbox(config.lade(repo), repo)
    assert erg.ok, f"{erg.grund} · fehlt noch: {erg.fehlt_noch}"
    assert "innen Exit 0" in erg.beleg and "außen Exit" in erg.beleg
    assert not list(Path.home().glob(".probesitz-*")), "Temp-Ordner werden aufgeräumt"
