"""Annahme nach Frist deckt nur die Entscheidungs-Liste, Live braucht ein eigenes Ja (#490).

Probe-Lauf 2 (F18): Der Aufseher nahm am Abnahme-Ticket nach 60 min an, die Bau-Session
wartete trotzdem auf einen zweiten Kommentar. Die Regel steht an genau einer Stelle
(``capo.annahme_grenze``); Annahme-Kommentar des Aufsehers und Start-Text der Bau-Session
lesen beide dort.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
import test_capo_checkpoint_285 as cp
from test_waechter_213 import _capo, _text, welt  # noqa: F401  (welt = Fixture)

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import capo


def _lade(name: str, datei: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, datei)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_regel_nennt_liste_live_grenze_und_den_befehl() -> None:
    text = capo.annahme_grenze(15, "to_spawn.py")
    assert "deckt nur die Entscheidungs-Liste" in text
    assert "nie den Live-Schritt" in text
    assert "eigenes Ja des Menschen" in text
    # #531: der eine kurze Befehl statt des langen (alter Weg läuft weiter, wird aber nicht mehr genannt)
    assert "`! ~/.claude/bin/ja 15`" in text


def test_annahme_kommentar_traegt_die_regel(welt: dict[str, Path]) -> None:  # noqa: F811
    cp._checkpoint_stellen(welt)
    cp._kommentar(welt, "902", "bau-bot", f"{cp.FRAGE}\n\n{cp.VORSCHLAG}", 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    annahme = [t for t in cp._texte(welt, "902") if t.startswith("Aufseher: Annahme nach")]
    assert len(annahme) == 1, cp._texte(welt, "902")
    befehl = f"python {(SKILL / 'to_spawn.py').as_posix()}"
    assert capo.annahme_grenze(902, befehl) in annahme[0], annahme[0]


def test_start_text_traegt_dieselbe_regel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))["prompt_template"]
    bau = _lade("bau_annahme_490", SKILL / "skripte" / "bau.py")
    text = bau.build_prompt(vorlage, "7", "9", "T", "- k", konfig={})
    assert "{ANNAHME_GRENZE}" not in text
    assert bau.capo.annahme_grenze("7") in text
    # Nach der Annahme kein zweiter Kommentar, sondern direkt das Live-Ja abwarten.
    assert "nicht auf einen zweiten Kommentar warten" in text
    assert "to_spawn.py antwort 7 --id <id>" in text
