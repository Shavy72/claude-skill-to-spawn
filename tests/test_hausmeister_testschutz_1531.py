"""Testlauf-Schutz für den Hausmeister (#873, aus App #1531).

Prüft zwei Schutzschichten:
1. ``TO_SPAWN_HAUSMEISTER_WURZELN=""`` liefert eine leere Wurzel-Liste und fällt
   niemals still auf die echten Maschinen-Wurzeln zurück.
2. Unter einem laufenden Test (``PYTEST_CURRENT_TEST`` / ``TO_SPAWN_TESTLAUF``)
   ohne ausdrückliche Test-Wurzel-Freigabe löst kein Einstieg (``rundgang``,
   ``aufraeumen_alles``, ``start_erlaubt``, ``speicher._platte_pruefen``) einen
   echten Löschlauf aus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from to_spawn import hausmeister, speicher


def test_leere_wurzel_vorgabe_faellt_nicht_auf_maschine_zurueck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leer gesetztes ``TO_SPAWN_HAUSMEISTER_WURZELN`` ergibt ``[]``, nicht die Maschinen-Wurzeln."""
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", "")
    assert hausmeister._wurzeln() == []

    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", "   ")
    assert hausmeister._wurzeln() == []


@pytest.mark.parametrize("wert", [None, "", "   "])
def test_rundgang_loeschen_unter_test_ohne_freigabe_blockiert(
    monkeypatch: pytest.MonkeyPatch,
    wert: str | None,
) -> None:
    """``rundgang(loeschen=True)`` und ``aufraeumen_alles(loeschen=True)`` laufen nicht ohne Freigabe."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_hausmeister_testschutz_1531.py::test")
    if wert is None:
        monkeypatch.delenv("TO_SPAWN_HAUSMEISTER_WURZELN", raising=False)
    else:
        monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", wert)

    aufgerufen = False

    def _darf_nicht_laufen(*, loeschen: bool, jetzt: float | None, docker: bool) -> hausmeister.Rundgang:
        nonlocal aufgerufen
        aufgerufen = True
        return hausmeister.Rundgang(True, "")

    monkeypatch.setattr(hausmeister, "_rundgang", _darf_nicht_laufen)

    erg = hausmeister.rundgang(loeschen=True)
    assert not erg.gelaufen
    assert "Test" in erg.grund or "TO_SPAWN_HAUSMEISTER_WURZELN" in erg.grund
    assert not aufgerufen

    assert hausmeister.aufraeumen_alles(loeschen=True) == []
    assert not aufgerufen


def test_start_erlaubt_und_speicher_loeschen_nicht_ohne_freigabe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auch bei Platznot löst ``start_erlaubt`` / ``speicher._platte_pruefen`` keinen Löschlauf aus."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_hausmeister_testschutz_1531.py::test")
    monkeypatch.delenv("TO_SPAWN_HAUSMEISTER_WURZELN", raising=False)
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "0.08")

    rundgang_aufgerufen = False
    hintergrund_aufgerufen = False

    def _kein_rundgang(*, loeschen: bool, jetzt: float | None, docker: bool) -> hausmeister.Rundgang:
        nonlocal rundgang_aufgerufen
        rundgang_aufgerufen = True
        return hausmeister.Rundgang(True, "")

    def _kein_hintergrund() -> bool:
        nonlocal hintergrund_aufgerufen
        hintergrund_aufgerufen = True
        return True

    monkeypatch.setattr(hausmeister, "_rundgang", _kein_rundgang)
    monkeypatch.setattr(hausmeister, "_hintergrund_anstossen", _kein_hintergrund)

    ok1, _ = hausmeister.start_erlaubt(loeschen=True)
    ok2, _ = hausmeister.start_erlaubt(loeschen=True, synchron_nur_bei_sperre=True)
    ok3, _ = speicher._platte_pruefen(loeschen=True)

    assert ok1 and ok2 and ok3
    assert not rundgang_aufgerufen
    assert not hintergrund_aufgerufen


def test_rundgang_loeschen_mit_test_wurzel_freigabe_laeuft(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Mit ausdrücklich gesetzter Test-Wurzel läuft ``rundgang(loeschen=True)`` normal."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_hausmeister_testschutz_1531.py::test")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", str(tmp_path / "leer" / "*"))

    erg = hausmeister.rundgang(loeschen=True)
    assert erg.gelaufen
    assert erg.ergebnisse == ()


def test_probelauf_mit_leerer_wurzel_bleibt_moeglich(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probelauf (``loeschen=False``) mit leerer Wurzel-Vorgabe läuft sauber und leer."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_hausmeister_testschutz_1531.py::test")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", "")

    erg = hausmeister.rundgang(loeschen=False)
    assert erg.gelaufen
    assert erg.ergebnisse == ()
