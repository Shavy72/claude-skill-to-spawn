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


def test_stempel_aus_der_zukunft_gilt_nicht_als_frisch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import os
    import time

    laeufe = _welt(monkeypatch, tmp_path, 0.10)
    speicher.cli(None, sitzung="spec-9")
    zukunft = time.time() + 3600  # Uhr-Sprung
    os.utime(speicher._stempel("spec-9"), (zukunft, zukunft))
    speicher.cli(None, sitzung="spec-9")
    assert laeufe == [True, True]


def test_stempel_unlesbar_raeumt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    laeufe = _welt(monkeypatch, tmp_path, 0.10)
    datei = tmp_path / "datei"
    datei.write_text("x", encoding="utf-8")  # Ordner des Stempels ist eine Datei ⇒ stat/touch scheitern
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(datei / "hausmeister.jsonl"))
    for _ in range(2):
        assert speicher.cli(None, sitzung="spec-9") == 0
    assert laeufe == [True, True]
