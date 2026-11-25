"""#461: Die Start-Wege der Einrichtungs-Tests lesen nie /proc des echten Rechners.

Rot-Beweis: ``e2._aufbau`` strich alle ``TO_SPAWN*``-Variablen, auch die Speicher-Attrappe
aus ``conftest.py``. ``spawn_srv.sh`` zählte dann die echten Claude-Sessions (≥ 9 = rot).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import test_spec_rand_e2 as e2

SPEICHER = (
    "TO_SPAWN_SPEICHER_MEMINFO",
    "TO_SPAWN_SPEICHER_PROC",
    "TO_SPAWN_SPEICHER_SESSIONS",
    "TO_SPAWN_SPEICHER_PLATZ",
)


@pytest.mark.parametrize("name", SPEICHER)
def test_aufbau_reicht_speicher_attrappe_an_kindprozesse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, str(tmp_path / "attrappe"))
    _repo, _welt, umgebung = e2._aufbau(tmp_path)
    assert umgebung.get(name) == str(tmp_path / "attrappe")


def _claude_proc(ordner: Path, anzahl: int) -> Path:
    """Attrappen-``/proc`` mit ``anzahl`` Claude-Sessions (``argv[0]`` = ``claude``)."""
    ordner.mkdir()
    for pid in range(1000, 1000 + anzahl):
        (ordner / str(pid)).mkdir()
        (ordner / str(pid) / "cmdline").write_bytes(b"claude\0")
    return ordner


def test_probelauf_folgt_der_attrappe_nicht_dem_echten_rechner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_einrichtungs_ticket_410 import _spawn_srv_weg

    # (a) Fixture-Attrappe (leeres /proc): Probelauf grün.
    (tmp_path / "a").mkdir()
    lauf, *_ = _spawn_srv_weg(tmp_path / "a", "--dry-run")
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "Claude-Sessions in spec" not in lauf.stdout + lauf.stderr

    # (b) Attrappe mit 9 Claude-Sessions: Obergrenze greift, nur über die Umgebung gesteuert.
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PROC", str(_claude_proc(tmp_path / "proc9", 9)))
    (tmp_path / "b").mkdir()
    lauf, *_ = _spawn_srv_weg(tmp_path / "b", "--dry-run")
    assert "schon 9 Claude-Sessions in spec" in lauf.stdout + lauf.stderr, lauf.stdout
