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
