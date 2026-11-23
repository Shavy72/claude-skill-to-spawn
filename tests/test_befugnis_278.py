"""Befugnis-Klassen im Start-Auftrag (#278 P1, Lastenheft L8 A43–A49).

Prüft: der gerenderte Bau-Prompt trägt Klasse 1/2/3, Sicherung, die Grenze „Live, Kosten,
Konten echter Personen“, den Briefkasten-Ort und die Erlaubnis zu ``/implement`` (Abnahme 10);
``einordnen`` ordnet die Kernfälle fest ein (Abnahme 1, 2); die Tabelle steht nur in
``to_spawn/befugnis.py``; die Ablöse-SOP lässt den Abschnitt in den Startprompt übernehmen.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

GRENZE = "Live, Kosten, Konten echter Personen"


def _lade(name: str, datei: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, datei)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture(scope="module")
def befugnis() -> ModuleType:
    from to_spawn import befugnis as modul

    return modul


@pytest.fixture(scope="module")
def bau_prompt() -> str:
    vorlage = json.loads(
        (SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8")
    )["prompt_template"]
    bau = _lade("bau_befugnis_278", SKILL / "skripte" / "bau.py")
    return bau.build_prompt(vorlage, "7", "9", "T", "- k", konfig={})


def test_bau_prompt_traegt_befugnis_abschnitt(bau_prompt: str) -> None:
    for teil in (
        "Klasse 1",
        "Klasse 2",
        "Klasse 3",
        "Sicherung",
        GRENZE,
        ".to-spawn/entscheidung-7.json",
    ):
        assert teil in bau_prompt, teil
    assert "/implement" in bau_prompt and "eigene Ticket" in bau_prompt
    assert "{BEFUGNIS}" not in bau_prompt
    assert "keine Rückfragen stellen" not in bau_prompt


def test_bau_prompt_enthaelt_start_text_wortgleich(
    befugnis: ModuleType, bau_prompt: str
) -> None:
    assert befugnis.start_text("7") in bau_prompt


@pytest.mark.parametrize(
    ("ziel", "art", "gesichert", "klasse"),
    [
        ("staging", "ändern", True, 1),  # Abnahme 1
        ("Staging", "lösen", True, 1),
        ("testdaten", "entfernen", True, 1),
        ("staging", "ändern", False, 3),  # ohne Sicherung: an den Aufseher
        ("live", "ändern", True, 2),  # Abnahme 2
        ("live", "warten", False, 2),
        ("staging", "kosten", True, 2),
        ("staging", "konto", True, 2),
        ("arbeitsordner", "ändern", False, 1),
        ("zweig", "entfernen", False, 1),
        ("ticket", "implement", False, 1),
        ("engpass", "warten", False, 1),
        ("engpass", "wiederholen", False, 1),
        ("irgendwo", "irgendwas", True, 3),  # Unbekanntes
        ("", "", False, 3),
    ],
)
def test_einordnen_kernfaelle(
    befugnis: ModuleType, ziel: str, art: str, gesichert: bool, klasse: int
) -> None:
    assert befugnis.einordnen(ziel, art, gesichert) == klasse


def test_tabellentext_nur_in_befugnis() -> None:
    treffer = [
        p.relative_to(SKILL).as_posix()
        for ordner in ("to_spawn", "skripte", "repo-scripts")
        for p in (SKILL / ordner).rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".json", ".md", ".txt"}
        and GRENZE in p.read_text(encoding="utf-8")
    ]
    assert treffer == ["to_spawn/befugnis.py"]


def test_start_text_ohne_personennamen(befugnis: ModuleType) -> None:
    text = befugnis.start_text("7")
    assert "der Nutzer" in text or "den Nutzer" in text
    assert not re.search(r"\b(David|Manuel|Cedrik)\b", text)


def test_abloese_sop_uebernimmt_befugnis_in_startprompt(befugnis: ModuleType) -> None:
    from to_spawn import anleitung

    assert befugnis.KOPF in anleitung.ABLOESE_SOP
    assert befugnis.start_text("7").startswith(befugnis.KOPF)
