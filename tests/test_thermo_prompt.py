"""Aufbau-Prüfung (Thermo, #366-Folge 2026-09-27) in beiden Wächter-Prompts.

Prüft ``skripte/wache.py`` (Dauer-Wächter) und ``to_spawn/waechter_takt.py``
(Einmal-Takt): beide rufen ``thermo_lauf.py plan``/``sammeln`` NACH dem
Abschluss-Paket ``--stand abnahme`` auf, mit parallelen Opus-Prüfern.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"

sys.path.insert(0, str(SKILL))


def _prompts() -> dict[str, str]:
    from to_spawn import waechter_takt

    modul_spec = importlib.util.spec_from_file_location("wache_thermo", SKRIPTE / "wache.py")
    assert modul_spec and modul_spec.loader
    wache = importlib.util.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(wache)
    return {"wache": wache.PROMPT, "takt": waechter_takt.PROMPT}


@pytest.mark.parametrize("name", ["wache", "takt"])
def test_prompt_enthaelt_thermo_aufruf(name: str) -> None:
    prompt = _prompts()[name]
    assert "thermo_lauf.py plan" in prompt
    assert "thermo_lauf.py sammeln" in prompt
    assert "model: opus" in prompt
    assert "thermo_{S}.md" in prompt


@pytest.mark.parametrize("name", ["wache", "takt"])
def test_thermo_nach_abnahme_paket(name: str) -> None:
    prompt = _prompts()[name]
    pos_abnahme = prompt.find("--stand abnahme")
    pos_thermo = prompt.find("thermo_lauf.py plan")
    assert pos_abnahme != -1 and pos_thermo != -1
    assert pos_thermo > pos_abnahme


@pytest.mark.parametrize("name", ["wache", "takt"])
def test_thermo_satz_nennt_exit_3(name: str) -> None:
    prompt = _prompts()[name]
    pos_sammeln = prompt.find("thermo_lauf.py sammeln")
    pos_exit3 = prompt.find("Exit 3")
    assert pos_sammeln != -1 and pos_exit3 != -1
    assert pos_exit3 > pos_sammeln


def test_wache_prompt_format_ohne_fehler() -> None:
    _prompts()  # bereits geladen — Format-Aufruf mit echten Platzhaltern
    from importlib import util as _u

    modul_spec = _u.spec_from_file_location("wache_thermo2", SKRIPTE / "wache.py")
    assert modul_spec and modul_spec.loader
    wache = _u.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(wache)
    wache.PROMPT.format(S=900, REPO="x/y", DATUM="2026-09-24", TAKT=1800, SKILL="/skill")


def test_takt_prompt_format_ohne_fehler() -> None:
    from to_spawn import waechter_takt

    waechter_takt.PROMPT.format(S=900, REPO="x/y", SKILL="/skill", LISTE="- x", NOTIZ="(leer)")
