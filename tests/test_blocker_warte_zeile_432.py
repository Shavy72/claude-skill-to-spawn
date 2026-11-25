"""#432 Fix-Runde: Warte-Zeile = Aufpasser-Muster, Herzschlag statt Log-Flut, Abstand wächst bei Abfrage-Fehlern."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from test_blocker_staging_1527 import _lade_bau

from to_spawn import aufpasser


def _lauf(bau, antworten, caplog, takt=30):
    """Lässt auf_blocker_warten mit gefälschter Uhr laufen; liefert (Schlafabstände, Log-Zeilen, Zeiten)."""
    uhr = [0.0]
    it = iter(antworten)
    schlaefe: list[float] = []
    zeiten: list[float] = []
    bau.blocker_offen = lambda *_a, **_k: next(it)
    handler = logging.Handler()
    handler.emit = lambda r: zeiten.append(uhr[0]) if "wartet" in r.getMessage() else None
    bau.log.addHandler(handler)
    bau.log.setLevel(logging.INFO)

    def schlafen(s: float) -> None:
        schlaefe.append(s)
        uhr[0] += s

    try:
        bau.auf_blocker_warten("9", takt, schlafen=schlafen, uhr=lambda: uhr[0])
    finally:
        bau.log.removeHandler(handler)
    return schlaefe, zeiten


def test_echte_warte_zeile_passt_zum_aufpasser_muster(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    zeile = bau.WARTE_ZEILE % ("9", "#8 offen", 30, bau.WARTE_HERZSCHLAG_S // 60, "08:00")
    assert aufpasser.BLOCKER_WARTEN.search(zeile)
    assert aufpasser.arbeitet(zeile, "idle")


def test_herzschlag_statt_jede_runde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    # 45 Runden à 30 s = 1350 s, gleiche Gründe, dann frei
    _s, zeiten = _lauf(bau, [["#8 offen"]] * 45 + [[]], caplog)
    assert zeiten == [0.0, 600.0, 1200.0]


def test_aenderung_der_gruende_loggt_sofort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    _s, zeiten = _lauf(bau, [["#8 offen"], ["#8 offen"], ["#7 offen"], []], caplog)
    assert zeiten == [0.0, 60.0]


def test_abstand_waechst_bei_fehlern_und_faellt_zurueck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    f = [bau.ABFRAGE_FEHLER[0]]
    schlaefe, _z = _lauf(bau, [f, f, f, f, f, ["#8 offen"], []], caplog)
    assert schlaefe == [30, 60, 120, 240, 300, 30]
