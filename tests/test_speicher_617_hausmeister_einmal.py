"""#617: Der Start-Check räumt höchstens einmal je Start (Sitzung), nicht vor jedem Fenster."""

from __future__ import annotations

from pathlib import Path

import pytest

from to_spawn import hausmeister, speicher


def _welt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, anteil: float) -> list[bool]:
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(tmp_path / "hausmeister.jsonl"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", str(anteil))
    monkeypatch.setattr(speicher, "_urteil_art", lambda *a, **k: (True, "RAM frei", False))
    laeufe: list[bool] = []

    def rundgang(*, loeschen: bool, jetzt: float | None = None) -> hausmeister.Rundgang:
        laeufe.append(loeschen)
        return hausmeister.Rundgang(True, "")

    monkeypatch.setattr(hausmeister, "rundgang", rundgang)
    return laeufe


def test_n_fenster_raeumen_einmal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    laeufe = _welt(monkeypatch, tmp_path, 0.10)  # Platznot, über der Startsperre
    for _ in range(7):
        assert speicher.cli(None, sitzung="spec-9") == 0
    assert laeufe == [True]


def test_andere_sitzung_raeumt_selbst(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    laeufe = _welt(monkeypatch, tmp_path, 0.10)
    speicher.cli(None, sitzung="spec-1")
    speicher.cli(None, sitzung="spec-2")
    assert laeufe == [True, True]


def test_voll_raeumt_erneut(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    laeufe = _welt(monkeypatch, tmp_path, 0.03)  # unter der Startsperre
    assert speicher.cli(None, sitzung="spec-9") == speicher.EXIT_PLATTE
    assert speicher.cli(None, sitzung="spec-9") == speicher.EXIT_PLATTE
    assert laeufe == [True, True]
