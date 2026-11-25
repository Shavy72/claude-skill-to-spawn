"""#477: Start-Text der Bau-Session nennt den erlaubten Beleg-Weg ohne ``curl``.

Probe-Lauf 2 (24.11.2026, ohne-staging, Fund F17): in den Tickets #12 und #14 wurde
``curl`` per Schutz-Hook gesperrt, sodass die Sessions erst nach Fehlversuch auf Python
auswichen. Der Hinweis steht an der einzigen Quelle (``to_spawn/anleitung.py``, Punkt 6
der Entscheidungs-Linie) und landet damit automatisch im Start-Text jeder Bau-Session
und im Aufseher-Maßstab — ohne geschweifte Klammern (``str.format``-sicher).
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

#: Kern des Hinweises in Punkt 6: kein Umweg über curl, sondern direkt Python (urllib.request).
MERKMAL_KEIN_CURL = "HTTP-Abrufe für Belege nicht über `curl`"
MERKMAL_PYTHON = "direkt über Python (`urllib.request`)"


def _lade(name: str, datei: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, datei)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _punkt_6(text: str) -> str:
    return next(z for z in text.splitlines() if z.startswith("6)"))


def test_punkt_6_nennt_beleg_weg_ohne_curl() -> None:
    from to_spawn import anleitung

    linie = anleitung.entscheidungs_linie()
    punkt_6 = _punkt_6(linie)
    assert MERKMAL_KEIN_CURL in punkt_6, punkt_6
    assert MERKMAL_PYTHON in punkt_6, punkt_6
    assert "{" not in linie and "}" not in linie, linie


def test_start_text_der_bau_session_nennt_beleg_weg_ohne_curl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))["prompt_template"]
    bau = _lade("bau_beleg_477", SKILL / "skripte" / "bau.py")
    text = bau.build_prompt(vorlage, "12", "1", "T", "- k", konfig={})
    assert MERKMAL_KEIN_CURL in text and MERKMAL_PYTHON in text


def test_aufseher_massstab_traegt_beleg_weg_ohne_curl() -> None:
    """Beide Aufseher-Texte tragen denselben Hinweis über die gemeinsame Quelle."""
    wache = _lade("wache_beleg_477", SKILL / "skripte" / "wache.py")
    aufseher_text = wache.prompt_bauen(900, "o/r", 1800, {})
    assert MERKMAL_KEIN_CURL in aufseher_text and MERKMAL_PYTHON in aufseher_text
    from to_spawn import waechter_takt

    assert MERKMAL_KEIN_CURL in waechter_takt.PROMPT and MERKMAL_PYTHON in waechter_takt.PROMPT


def test_beleg_hinweis_nur_an_einer_stelle() -> None:
    """Wissen an genau einer Stelle: keine zweite Kopie im Skill außerhalb von Tests/Doku."""
    fundorte: list[str] = []
    for datei in SKILL.rglob("*"):
        teile = datei.relative_to(SKILL).parts
        if not datei.is_file() or teile[0] in {".git", ".review", "tests", "docs"}:
            continue
        try:
            inhalt = datei.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if MERKMAL_KEIN_CURL in inhalt:
            fundorte.append(datei.relative_to(SKILL).as_posix())
    assert fundorte == ["to_spawn/anleitung.py"], fundorte
