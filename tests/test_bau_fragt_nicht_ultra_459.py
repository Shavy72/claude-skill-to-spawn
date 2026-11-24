"""#459: Bau-Session endet nie mit der Frage „/code-review ultra starten? Ja/Nein“.

Probe-Lauf 2 (24.11.): der Review-Dirigent schlägt bei Stufe „gross“ + hartem Signal das
kostenpflichtige Cloud-Review als Ja/Nein-Frage vor. Punkt 5 der Entscheidungs-Linie
(„Nie ohne den Nutzer: … Kosten …“) las die Bau-Session als „fragen“ — sie stand 19 min still
am Prompt, Ticket blieb offen. Soll: kostenpflichtiges Extra ohne Auftrag ist in der Bau-Session
einfach Nein (nicht starten, nicht vorschlagen, nicht fragen), höchstens eine Hinweiszeile.
Die Regel steht an der einzigen Quelle (``anleitung.entscheidungs_linie``) und kommt so in den
Start-Text jeder Bau-Session und in den Aufseher-Maßstab.
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

#: Kern der Regel: Name des Befehls und die Vorgabe „Nein“ ohne Frage.
MERKMAL_ULTRA = "`/code-review ultra`"
MERKMAL_NEIN = "ist Nein: nicht starten, nicht vorschlagen, nicht fragen"


def _lade(name: str, datei: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, datei)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _punkt_5(text: str) -> str:
    return next(z for z in text.splitlines() if z.startswith("5)"))


def test_punkt_5_nennt_cloud_review_als_nein_ohne_frage() -> None:
    from to_spawn import anleitung

    punkt_5 = _punkt_5(anleitung.entscheidungs_linie())
    assert MERKMAL_ULTRA in punkt_5, punkt_5
    assert MERKMAL_NEIN in punkt_5, punkt_5
    # Keine Briefkasten-Frage für dieses Extra: die Ausnahme steht nach dem Briefkasten-Befehl.
    assert punkt_5.index(MERKMAL_NEIN) > punkt_5.index("--klasse 2"), punkt_5


def test_start_text_der_bau_session_traegt_ultra_regel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))["prompt_template"]
    bau = _lade("bau_ultra_459", SKILL / "skripte" / "bau.py")
    text = bau.build_prompt(vorlage, "3", "1", "T", "- k", konfig={})
    assert MERKMAL_ULTRA in text and MERKMAL_NEIN in text


def test_aufseher_massstab_traegt_ultra_regel() -> None:
    """Der Aufseher beantwortet eine doch gestellte Frage mit demselben Maßstab (Nein)."""
    from to_spawn import waechter_takt

    assert MERKMAL_ULTRA in waechter_takt.PROMPT and MERKMAL_NEIN in waechter_takt.PROMPT
