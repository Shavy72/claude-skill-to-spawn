"""#1369 Nachfix: „wartet“ nur, wenn wirklich gewartet wird.

Bei genug Platz auf der Platte bleibt ``speicher.auf_platte_warten`` still — sonst
liest respawn/der Umzug-Test eine Warte-Zeile, obwohl sofort gestartet wird.
"""

from __future__ import annotations

import pytest

from to_spawn import speicher


def test_genug_platz_keine_warte_zeile(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "0.50")
    pausen: list[float] = []
    zyklen = speicher.auf_platte_warten(wer="bau 1", schlafen=pausen.append)
    assert zyklen == 0 and pausen == []
    assert speicher.WARTE_TEXT not in capsys.readouterr().err


def test_platznot_vorab_zeile_bleibt(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "0.10")  # Platznot, über Sperre
    speicher.auf_platte_warten(
        wer="bau 1",
        pruefen=lambda: (True, "Platte frei"),
        schlafen=lambda _s: None,
        knapp=speicher._platte_knapp,
    )
    assert speicher.WARTE_TEXT in capsys.readouterr().err
