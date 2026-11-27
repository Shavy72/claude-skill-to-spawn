"""Fix-Commits für roten Hauptzweig tragen das Kennzeichen (master-rot) (#900, aus App #1532).

Prüft, dass:
- ``to_spawn/anleitung.py`` das Kennzeichen ``MASTER_ROT_KENNZEICHEN = "(master-rot)"`` und die
  Regel :func:`master_rot_regel` an genau einer Stelle hält (keine zweite Kopie im Skill).
- ``repo-scripts/_default.json`` den Platzhalter ``{MASTER_ROT_REGEL}`` trägt und
  ``skripte/bau.py:build_prompt`` ihn mit dem Hauptzweig und dem Kennzeichen (bzw. dem
  konfigurierten ``regularien.master_rot_kennzeichen``) füllt.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from conftest import repo_dateien

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import anleitung  # noqa: E402


def _lade(name: str, datei: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, datei)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_master_rot_kennzeichen_und_regel_in_anleitung() -> None:
    assert anleitung.MASTER_ROT_KENNZEICHEN == "(master-rot)"
    regel = anleitung.master_rot_regel("master")
    assert anleitung.MASTER_ROT_KENNZEICHEN in regel
    assert "origin/master" in regel
    assert "Spitzen-Commits" in regel


def test_start_text_traegt_master_rot_regel() -> None:
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))["prompt_template"]
    assert "{MASTER_ROT_REGEL}" in vorlage
    bau = _lade("bau_master_rot_900", SKILL / "skripte" / "bau.py")
    text = bau.build_prompt(vorlage, "900", "53", "Fix roter master", "- k", konfig={})
    assert anleitung.MASTER_ROT_KENNZEICHEN in text
    assert "{MASTER_ROT_REGEL}" not in text


def test_start_text_nutzt_konfiguriertes_kennzeichen() -> None:
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))["prompt_template"]
    bau = _lade("bau_master_rot_konfig_900", SKILL / "skripte" / "bau.py")
    text = bau.build_prompt(
        vorlage,
        "900",
        "53",
        "Fix roter main",
        "- k",
        konfig={"regularien": {"master_rot_kennzeichen": "(main-rot)"}},
    )
    assert "(main-rot)" in text
    assert "{MASTER_ROT_REGEL}" not in text


def test_master_rot_kennzeichen_nur_an_einer_stelle_im_skill() -> None:
    """Keine zweite Kopie von ``(master-rot)`` außerhalb von ``to_spawn/anleitung.py``."""
    fundorte: list[str] = []
    for datei in repo_dateien(SKILL):
        teile = datei.relative_to(SKILL).parts
        if teile[0] in {".review", "tests", "docs"}:
            continue
        try:
            inhalt = datei.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "(master-rot)" in inhalt:
            fundorte.append(datei.relative_to(SKILL).as_posix())
    assert fundorte == ["to_spawn/anleitung.py"], fundorte
