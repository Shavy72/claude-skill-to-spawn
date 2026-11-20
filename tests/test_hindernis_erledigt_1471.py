"""#1471: Ein Hindernis verschwindet aus ``/stand``, sobald es behoben ist.

Einzige meldende Stelle ist der Start-Check des Hausmeisters (``platte_voll``,
``platte_unmessbar``, ``hausmeister_fehler``). Ein gesunder Start-Check schreibt über
``hindernis.erledigt()`` je offene Art eine Ende-Zeile; ``offene()`` wertet sie aus.
``erledigt`` zählt nur bei echtem ``True``.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

from pathlib import Path

import pytest
from test_hausmeister_1368 import Welt, welt  # noqa: F401 — ``welt`` ist ein Fixture
from test_hausmeister_1369 import _platte
from test_stand_hindernis_1433 import JETZT, _schreiben, _zeile

from to_spawn import hausmeister, hindernis


def test_erledigt_nur_bei_echtem_true(tmp_path: Path) -> None:
    datei = tmp_path / "h.jsonl"
    _schreiben(
        datei,
        [_zeile(2, erledigt="false"), _zeile(1, art="platte_unmessbar", erledigt=1)],
    )
    arten = sorted(z["art"] for z in hindernis.offene(jetzt=JETZT, datei=datei))
    assert arten == ["platte_unmessbar", "platte_voll"]


def test_ende_zeile_schliesst_fruehere_und_neue_meldung_oeffnet_wieder(
    tmp_path: Path,
) -> None:
    datei = tmp_path / "h.jsonl"
    _schreiben(
        datei,
        [
            _zeile(5),
            _zeile(4),
            _zeile(3, erledigt=True),  # Ende für platte_voll
            _zeile(3, art="platte_unmessbar"),
            _zeile(2, art="platte_unmessbar", erledigt=True),
            _zeile(1, art="platte_unmessbar"),  # wieder offen
        ],
    )
    offen = hindernis.offene(jetzt=JETZT, datei=datei)
    assert [(z["art"], z["anzahl"]) for z in offen] == [("platte_unmessbar", 1)]


def test_erledigt_schreibt_nur_fuer_offene_arten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    datei = tmp_path / "h.jsonl"
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(datei))
    assert hindernis.erledigt("platte_voll") == []  # nichts offen → keine Zeile
    assert hindernis.lesen() == []
    hindernis.melden("platte_voll", "Platte voll")
    assert hindernis.erledigt("platte_voll", "platte_unmessbar") == ["platte_voll"]
    assert hindernis.offene() == []
    assert hindernis.erledigt("platte_voll") == []  # schon zu → Log wächst nicht
    assert len(hindernis.lesen()) == 2


def test_gesunder_start_check_schliesst_hindernisse(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(welt.tmp / "h.jsonl"))
    _platte(welt, monkeypatch, 0.01)
    ok, _ = hausmeister.start_erlaubt(loeschen=False)
    assert not ok
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", str(welt.tmp / "gibt-es-nicht"))
    assert hausmeister.start_erlaubt(loeschen=False)[0]
    assert sorted(z["art"] for z in hindernis.offene()) == [
        "platte_unmessbar",
        "platte_voll",
    ]
    _platte(welt, monkeypatch, 0.9)
    ok, satz = hausmeister.start_erlaubt(loeschen=False)
    assert ok, satz
    assert hindernis.offene() == []
    assert hindernis.kurzzeile() is None


def test_jede_gemeldete_art_wird_geschlossen() -> None:
    """Jeder ``_spur``-Aufruf nennt eine Art-Konstante aus ``HINDERNIS_ARTEN`` — kein
    Text-Literal. Sonst bliebe eine neue Art ohne Tupel-Eintrag für immer offen."""
    import ast
    import inspect

    baum = ast.parse(inspect.getsource(hausmeister))
    aufrufe = [
        k
        for k in ast.walk(baum)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Name)
        and k.func.id == "_spur"
    ]
    assert aufrufe, "keine _spur-Aufrufe gefunden"
    for aufruf in aufrufe:
        arg = aufruf.args[0]
        assert isinstance(arg, ast.Name), (
            f"Zeile {aufruf.lineno}: Art als Literal statt Konstante"
        )
        assert getattr(hausmeister, arg.id) in hausmeister.HINDERNIS_ARTEN, (
            f"Zeile {aufruf.lineno}: {arg.id} fehlt in HINDERNIS_ARTEN"
        )
