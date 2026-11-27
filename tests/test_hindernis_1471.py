"""#896 (aus App #1471, Rest aus #1433): Hindernis-Log offene(), kurzzeile(), erledigt().

Wissen über das Log (Format, 24-h-Fenster, „offen“, „erledigt“) lebt in
``to_spawn/hindernis.py``. Ein gesunder Start-Check des Hausmeisters schließt
alle offenen Start-Check-Arten über ``hindernis.erledigt(*HINDERNIS_ARTEN)``.
``erledigt`` zählt nur bei echtem ``True``.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from test_hausmeister_1368 import Welt, welt  # noqa: F401 — ``welt`` ist ein Fixture
from test_hausmeister_1369 import _platte

from to_spawn import hausmeister, hindernis

JETZT = datetime(2026, 11, 21, 12, 0).astimezone()


def _schreiben(datei: Path, zeilen: list[object]) -> None:
    datei.write_text(
        "\n".join(z if isinstance(z, str) else json.dumps(z) for z in zeilen) + "\n",
        encoding="utf-8",
    )


def _zeile(stunden_her: float, art: str = "platte_voll", **felder: object) -> dict[str, object]:
    zeit = (JETZT - timedelta(hours=stunden_her)).isoformat(timespec="seconds")
    return {"zeit": zeit, "art": art, "text": f"{art} vor {stunden_her} h", **felder}


# --- (a) offene() und kurzzeile() (#1433) -----------------------------------------


def test_offene_nur_letzte_24h_und_nicht_erledigt(tmp_path: Path) -> None:
    datei = tmp_path / "hindernisse.jsonl"
    _schreiben(
        datei,
        [
            _zeile(30),  # zu alt
            _zeile(5, art="platte_unmessbar", erledigt=True),  # erledigt
            _zeile(3),
            _zeile(1),
        ],
    )
    offen = hindernis.offene(jetzt=JETZT, datei=datei)
    assert [z["text"] for z in offen] == ["platte_voll vor 1 h"]  # je Art die jüngste
    assert offen[0]["anzahl"] == 2


def test_offene_kaputte_zeilen_und_fehlende_datei_leer(tmp_path: Path) -> None:
    assert hindernis.offene(jetzt=JETZT, datei=tmp_path / "fehlt.jsonl") == []
    datei = tmp_path / "hindernisse.jsonl"
    _schreiben(
        datei,
        ["{kaputt", "[1, 2]", {"art": "x", "text": "ohne Zeit"}, {"zeit": "Unsinn"}],
    )
    assert hindernis.offene(jetzt=JETZT, datei=datei) == []
    assert hindernis.kurzzeile(jetzt=JETZT, datei=datei) is None


def test_kurzzeile_eine_zeile_fuer_mehrere_arten(tmp_path: Path) -> None:
    datei = tmp_path / "hindernisse.jsonl"
    _schreiben(datei, [_zeile(2), _zeile(1), _zeile(4, art="platte_unmessbar")])
    zeile = hindernis.kurzzeile(jetzt=JETZT, datei=datei)
    assert zeile is not None and "\n" not in zeile
    assert zeile.startswith("Hindernisse (24 h): ")
    assert "platte_voll vor 1 h (2×" in zeile and "platte_unmessbar vor 4 h" in zeile


# --- (b) erledigt() streng nur bei echtem True (#1471) ----------------------------


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


def test_erledigt_schreibt_nur_fuer_offene_arten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    datei = tmp_path / "h.jsonl"
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(datei))
    assert hindernis.erledigt("platte_voll") == []  # nichts offen → keine Zeile
    assert hindernis.lesen() == []
    hindernis.melden("platte_voll", "Platte voll")
    assert hindernis.erledigt("platte_voll", "platte_unmessbar") == ["platte_voll"]
    assert hindernis.offene() == []
    assert hindernis.erledigt("platte_voll") == []  # schon zu → Log wächst nicht
    assert len(hindernis.lesen()) == 2


# --- (c) Hausmeister-Start-Check schließt offene Arten ----------------------------


def test_gesunder_start_check_schliesst_hindernisse(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(welt.tmp / "h.jsonl"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_INODES", "0.10")
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
    """Prüf-Test: jeder ``_spur``-Aufruf nutzt eine Konstante aus ``HINDERNIS_ARTEN``."""
    baum = ast.parse(inspect.getsource(hausmeister))
    aufrufe = [
        k for k in ast.walk(baum) if isinstance(k, ast.Call) and isinstance(k.func, ast.Name) and k.func.id == "_spur"
    ]
    assert aufrufe, "keine _spur-Aufrufe gefunden"
    for aufruf in aufrufe:
        arg = aufruf.args[0]
        assert isinstance(arg, ast.Name), f"Zeile {aufruf.lineno}: Art als Literal statt Konstante"
        assert getattr(hausmeister, arg.id) in hausmeister.HINDERNIS_ARTEN, (
            f"Zeile {aufruf.lineno}: {arg.id} fehlt in HINDERNIS_ARTEN"
        )
