"""Aufseher-Anleitung (#500): feste Texte stehen wortgleich an allen Stellen.

Prüft, dass Mindset und Ablöse-SOP aus ``to_spawn/anleitung.py`` im Aufseher-Prompt,
in beiden Anstupsern (Leiter, Aufpasser) und in der Bau-Prompt-Vorlage landen, dass es
nur noch eine Eingriffs-Regel gibt und der Takt bei reiner Abnahme 60 min beträgt.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import anleitung, aufpasser, leiter  # noqa: E402


def _lade(name: str, datei: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, datei)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture(scope="module")
def wache() -> ModuleType:
    return _lade("wache_anleitung_500", SKILL / "skripte" / "wache.py")


def test_mindset_wortgleich_in_prompt_und_anstupsern(wache: ModuleType) -> None:
    assert anleitung.MINDSET in wache.PROMPT
    assert anleitung.MINDSET in leiter.MINDSET_TEXT
    assert anleitung.MINDSET in aufpasser.ANSTUPS_TEXT
    assert anleitung.STOSS in leiter.MINDSET_TEXT
    assert anleitung.STOSS in aufpasser.ANSTUPS_TEXT


def test_anstupser_formatierbar() -> None:
    assert "seit 20 min still" in leiter.MINDSET_TEXT.format(min=20)
    assert "seit 61 min still" in aufpasser.ANSTUPS_TEXT.format(min=61)


def test_abloese_sop_im_aufseher_prompt(wache: ModuleType) -> None:
    text = wache.prompt_bauen(900, "o/r", 1800, {})
    assert anleitung.ABLOESE_SOP in text
    for teil in ("tmux-Fenster", "Windows-Terminal-Tab", "/remote-control", "Selbstneustart"):
        assert teil in text


def test_eine_eingriffs_regel(wache: ModuleType) -> None:
    assert "über 60 min" not in wache.PROMPT
    assert "to_spawn.py leiter" in wache.PROMPT


def test_takt_abnahme_60_min(wache: ModuleType) -> None:
    text = wache.prompt_bauen(900, "o/r", 1800, {})
    assert "ScheduleWakeup 1800 s" in text
    assert "ScheduleWakeup 3600 s" in text
    assert "teilabnahme_nach" in text


def test_bau_vorlage_bekommt_mindset_und_sop() -> None:
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))[
        "prompt_template"
    ]
    assert "{MINDSET}" in vorlage and "{ABLOESE_SOP}" in vorlage
    bau = _lade("bau_anleitung_500", SKILL / "skripte" / "bau.py")
    text = bau.build_prompt(vorlage, "7", "9", "T", "- k", konfig={})
    assert anleitung.MINDSET in text
    assert anleitung.ABLOESE_SOP in text
    assert "{MINDSET}" not in text and "{ABLOESE_SOP}" not in text
