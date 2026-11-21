"""#165: Sperr-Datei nicht öffenbar ist ein Fehler, nicht „belegt“.

Nur ein Fehler aus dem Sperr-Versuch selbst (flock/msvcrt) darf als „belegt“ gelten.
Kann ``to_spawn.sperre.halte`` den Ordner oder die Datei gar nicht anlegen/öffnen
(Pfad ist ein Ordner, Eltern-Pfad ist eine Datei, keine Rechte), fliegt
:class:`to_spawn.sperre.SperreNichtOeffenbar`; aufpasser und capo melden das laut.
"""

from __future__ import annotations

import logging
import os
import types
from pathlib import Path

import pytest

from to_spawn import aufpasser, capo, sperre, vorfall


def _nie() -> None:
    raise AssertionError("Lauf darf ohne Sperre nicht starten")


# --- sperre.halte: eine Stelle entscheidet ----------------------------------------------


def test_halte_sperrdatei_ist_ordner_wirft(tmp_path: Path) -> None:
    datei = tmp_path / "lock"
    datei.mkdir()
    with pytest.raises(sperre.SperreNichtOeffenbar) as info:
        with sperre.halte(datei):
            raise AssertionError("darf nicht gehalten werden")
    assert isinstance(
        info.value, OSError
    )  # alte ``except OSError``-Aufrufer bleiben heil
    assert str(datei) in str(info.value)


def test_halte_ordner_nicht_anlegbar_wirft(tmp_path: Path) -> None:
    eltern = tmp_path / "eine-datei"
    eltern.write_text("x", encoding="utf-8")
    with pytest.raises(sperre.SperreNichtOeffenbar):
        with sperre.halte(eltern / "lock"):
            raise AssertionError("darf nicht gehalten werden")


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="Rechte-Probe braucht Nicht-root unter POSIX",
)
def test_halte_excl_rueckfall_ohne_rechte_wirft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sperre, "_fcntl", None)
    monkeypatch.setattr(sperre, "_msvcrt", None)
    ordner = tmp_path / "nur-lesen"
    ordner.mkdir()
    ordner.chmod(0o500)
    try:
        with pytest.raises(sperre.SperreNichtOeffenbar):
            with sperre.halte(ordner / "lock"):
                raise AssertionError("darf nicht gehalten werden")
    finally:
        ordner.chmod(0o700)


def test_halte_belegt_bleibt_false(tmp_path: Path) -> None:
    datei = tmp_path / "lock"
    with sperre.halte(datei) as erster:
        assert erster
        with sperre.halte(datei) as zweiter:
            assert zweiter is False


# --- aufpasser: Exit 1 statt still 0 ----------------------------------------------------


def test_aufpasser_sperrdatei_nicht_oeffenbar_exit_1(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    zustand = tmp_path / "zustand"
    (zustand / "lock").mkdir(parents=True)
    a = aufpasser.Aufpasser.__new__(aufpasser.Aufpasser)
    a.e = types.SimpleNamespace(trocken=False, zustand=zustand)
    a._lauf = _nie  # type: ignore[method-assign]
    a._stand_schreiben = _nie  # type: ignore[method-assign]
    with caplog.at_level(logging.INFO):
        assert a.lauf() == 1
    fehler = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert fehler and "nicht öffenbar" in fehler[0].getMessage(), caplog.text
    assert "Sperre belegt" not in caplog.text


# --- capo: Fehler statt „anderer Lauf“ --------------------------------------------------


def test_capo_sperre_nicht_oeffenbar_wirft(tmp_path: Path) -> None:
    datei = tmp_path / "zustand.json"
    (tmp_path / "zustand.json.lock").mkdir()
    with pytest.raises(sperre.SperreNichtOeffenbar):
        with capo.sperre(datei, warten_s=0):
            raise AssertionError("darf nicht gehalten werden")


def test_capo_tick_sperrdatei_nicht_oeffenbar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    datei = tmp_path / "zustand.json"
    (tmp_path / "zustand.json.lock").mkdir()
    monkeypatch.setattr(capo, "_zustand_datei", lambda *_a: datei)
    monkeypatch.setattr(capo, "_tick", lambda *_a, **_k: _nie())
    erg = capo.tick(tmp_path, 7, "o/r", {})
    assert erg.exit_code == 1
    text = "\n".join(erg.zeilen)
    assert "FEHLER" in text and "nicht öffenbar" in text, text
    assert "läuft schon" not in text


def test_capo_katalog_sperrdatei_nicht_oeffenbar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfad = tmp_path / "FEHLERKATALOG.md"
    (tmp_path / "FEHLERKATALOG.md.lock").mkdir()
    monkeypatch.setattr(vorfall, "katalog_pfad", lambda *_a, **_k: pfad)
    monkeypatch.setattr(vorfall, "in_katalog", lambda *_a, **_k: _nie())
    fall = vorfall.Vorfall(klasse="k", symptom="s", ursache="u", loesung="l")
    zeilen = capo.katalog_pflegen(tmp_path, {}, [fall])
    text = "\n".join(zeilen)
    assert any(z.startswith("FEHLER") for z in zeilen), text
    assert "nicht öffenbar" in text
    assert "anderer Lauf" not in text
