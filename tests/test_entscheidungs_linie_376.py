"""Entscheidungs-Linie (#376): fest im Start-Text jeder Bau-Session und im Aufseher-Auftrag.

Die acht Punkte stehen an genau einer Stelle im Skill (``to_spawn/anleitung.py``); der
Start-Text (``skripte/bau.py`` + ``repo-scripts/_default.json``) und beide Aufseher-Texte
(``skripte/wache.py``, ``to_spawn/waechter_takt.py``) lesen von dort — ohne ``BAU_AUFTRAG``.
Repo-Begriffe (Hauptzweig) kommen aus der Konfiguration (``gh.hauptzweig``).
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

#: Je Punkt ein Satzteil, der nur in der Linie vorkommt (Reihenfolge = Punkte 1–8).
MERKMALE = (
    "Blocker erledigt = geschlossen ODER",
    "Dann selbst claimen und den Skill `implement` starten",
    "Keine Fragen an den Nutzer:",
    "Staging- und Test-Daten sind frei, auch Löschen",
    "Nie ohne den Nutzer: Live, Live-Daten, Live-Freigabe, Kosten, Konten echter Personen",
    "Fertig = grüne Schlange + echter Klickweg auf Staging + Beleg",
    "Vor jedem Push die Vor-Prüfungen lokal grün",
    "(was · warum · Quelle) in die Entscheidungs-Liste der Spec",
)


def _lade(name: str, datei: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, datei)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _alle_punkte(text: str) -> None:
    fehlt = [m for m in MERKMALE if m not in text]
    assert not fehlt, f"Linie unvollständig, fehlt: {fehlt}"


def test_start_text_traegt_linie_ohne_zusatz_auftrag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))["prompt_template"]
    bau = _lade("bau_linie_376", SKILL / "skripte" / "bau.py")
    text = bau.build_prompt(vorlage, "7", "9", "T", "- k", konfig={})
    _alle_punkte(text)
    assert "staging:fertig" in text
    assert f"origin/{bau.gh.hauptzweig(bau.REPO)}" in text.split("Blocker erledigt", 1)[1].split("\n", 1)[0]
    assert "{" + "ENTSCHEIDUNGS_LINIE}" not in text


def test_aufseher_auftrag_traegt_linie_als_massstab() -> None:
    wache = _lade("wache_linie_376", SKILL / "skripte" / "wache.py")
    text = wache.prompt_bauen(900, "o/r", 1800, {})
    _alle_punkte(text)
    assert "Maßstab" in text
    from to_spawn import waechter_takt

    _alle_punkte(waechter_takt.PROMPT)


def test_linie_nur_an_einer_stelle_gepflegt() -> None:
    """Zweite Kopie im Skill (außer Tests/Belegen) → rot."""
    fundorte: dict[str, list[str]] = {m: [] for m in MERKMALE}
    for datei in SKILL.rglob("*"):
        teile = datei.relative_to(SKILL).parts
        if (
            not datei.is_file()
            or teile[0] in {".git", "tests", "docs"}
            or datei.suffix not in {".py", ".json", ".md", ".txt", ".sh", ".ps1"}
        ):
            continue
        inhalt = datei.read_text(encoding="utf-8", errors="replace")
        for m in MERKMALE:
            if m in inhalt:
                fundorte[m].append(datei.relative_to(SKILL).as_posix())
    assert all(orte == ["to_spawn/anleitung.py"] for orte in fundorte.values()), fundorte
