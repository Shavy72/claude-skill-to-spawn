"""Tests für #111 — Weiterleitungs-Vorlage auf App-Stand (``einrichten --pruefen`` Exit 0).

Die Vorlage ``repo-scripts/_to_spawn_weiterleitung.py`` ist die Quelle der Weiterleitung im
App-Repo. Die App nutzt ``lade_skript`` (``scripts/wirkungskreis.py``); fehlt die Funktion in
der Vorlage, meldet ``einrichten --pruefen`` die App-Fassung als veraltet, und ein Überschreiben
würde ``wirkungskreis`` brechen.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from to_spawn import einrichten


def _vorlage() -> ModuleType:
    datei = einrichten._VORLAGEN / "_to_spawn_weiterleitung.py"
    spec = importlib.util.spec_from_file_location("_vorlage_weiterleitung_111", datei)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_lade_skript_laedt_skill_skript_als_modul(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "skripte").mkdir()
    (tmp_path / "skripte" / "thermo_lauf.py").write_text("WERT = 111\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_HOME", str(tmp_path))
    modul = _vorlage().lade_skript("thermo_lauf")
    assert modul.WERT == 111


def test_lade_skript_ohne_skill_skript_wirft_importerror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TO_SPAWN_HOME", str(tmp_path))
    with pytest.raises(ImportError, match="Skill to-spawn fehlt"):
        _vorlage().lade_skript("thermo_lauf")


def test_capo_vorlage_kopfzeile_wie_app() -> None:
    # Kopfzeile verweist auf #213, Rollenname nach #428 (E1) „Aufseher“. Die Vorlage ist die
    # Quelle: eine App-Fassung mit altem Rollennamen ist veraltet und wird nachgezogen.
    kopf = (einrichten._VORLAGEN / "capo.py").read_text(encoding="utf-8").splitlines()[0]
    assert kopf == '"""Weiterleitung auf den Skill to-spawn (#205, Aufseher-Tick #213).'
