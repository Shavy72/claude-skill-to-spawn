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


@pytest.mark.parametrize(
    "ziel",
    ["produktion", "prod", "production", "vps-live", "vps", "live-app", "Produktion "],
)
def test_live_synonyme_sind_klasse_2(befugnis: ModuleType, ziel: str) -> None:
    assert befugnis.einordnen(ziel, "ändern", True) == 2


@pytest.mark.parametrize("art", ["deploy", "irgendwas", "umbenennen_irgendwas"])
@pytest.mark.parametrize("ziel", ["arbeitsordner", "zweig"])
def test_arbeitsordner_unbekannte_art_ist_klasse_3(
    befugnis: ModuleType, ziel: str, art: str
) -> None:
    assert befugnis.einordnen(ziel, art, True) == 3


def test_kosten_sparen_ist_klasse_2(befugnis: ModuleType) -> None:
    """Sichere Richtung: Teilwort „kosten“ gilt, keine Ausnahme-Liste."""
    assert befugnis.einordnen("arbeitsordner", "kosten_sparen", True) == 2


@pytest.mark.parametrize("art", ["entfernen", "lösen", "löschen"])
def test_live_loeschen_ist_klasse_2(befugnis: ModuleType, art: str) -> None:
    assert befugnis.einordnen("live", art, False) == 2


@pytest.mark.parametrize("ziel", ["LIVE", " live ", "\tLive\n", "Live"])
def test_live_schreibweisen_sind_klasse_2(befugnis: ModuleType, ziel: str) -> None:
    assert befugnis.einordnen(ziel, "ändern", False) == 2


def test_live_mit_sicherung_bleibt_klasse_2(befugnis: ModuleType) -> None:
    assert befugnis.einordnen("live", "anlegen", gesichert=True) == 2


@pytest.mark.parametrize(
    ("ziel", "art"),
    [(None, None), (None, "ändern"), ("arbeitsordner", None), (1, 2), (0, "ändern")],
)
def test_nicht_string_eingaben_nie_klasse_1(
    befugnis: ModuleType, ziel: object, art: object
) -> None:
    assert befugnis.einordnen(ziel, art, True) in (2, 3)  # type: ignore[arg-type]


def test_start_text_nennt_live_synonyme_aus_tabelle(befugnis: ModuleType) -> None:
    text = befugnis.start_text("7")
    for wort in befugnis._KLASSE_2.ziele or ():
        assert wort in text, wort


# --- Prüfer-Runde 2 (#278 P1): eine Klasse-2-Wortmenge, Weg für Klasse 3, Push-Ziele ---


@pytest.mark.parametrize(
    ("ziel", "art"),
    [
        ("konto", "ändern"),
        ("konten", "ändern"),
        ("kosten", "ändern"),
        ("ändern", "live"),
        ("instagram-konto", "ändern"),
        ("arbeitsordner", "konten"),
        ("prod-db", "ändern"),
        ("production-db", "ändern"),
        ("live_app", "ändern"),
        ("liveapp", "ändern"),
        ("zweig", "live-schalten"),
        ("staging", "geld-ausgeben"),
        ("Ｌive", "ändern"),
        ("live app", "ändern"),
        ("vps live", "anlegen"),
    ],
)
def test_klasse_2_teilwort_in_ziel_und_art(
    befugnis: ModuleType, ziel: str, art: str
) -> None:
    assert befugnis.einordnen(ziel, art, True) == 2


@pytest.mark.parametrize("ziel", ["arbeitsordner", "zweig", "staging", "testdaten"])
@pytest.mark.parametrize("art", ["anlegen", "ändern", "testen", "committen"])
def test_harmlose_faelle_nie_klasse_2(
    befugnis: ModuleType, ziel: str, art: str
) -> None:
    assert befugnis.einordnen(ziel, art, True) != 2


@pytest.mark.parametrize(
    ("ziel", "klasse"),
    [
        ("wip-zweig", 1),
        ("wip/278-sicherung", 1),
        ("ticket-zweig", 3),
        ("ticket/<n>-…", 3),
        ("wip/../master", 3),
        ("ticket/7-../master", 3),
        ("ticket/7-befugnis", 1),
        ("ticket/8-fremd", 3),
        ("zweig", 3),
        ("hauptzweig", 3),
        ("master", 3),
        ("main", 3),
    ],
)
def test_pushen_nur_auf_eigene_zweige(
    befugnis: ModuleType, monkeypatch: pytest.MonkeyPatch, ziel: str, klasse: int
) -> None:
    monkeypatch.setenv("TO_SPAWN_TICKET", "7")
    assert befugnis.einordnen(ziel, "pushen", False) == klasse


def test_klasse_3_weg_ist_vom_aufseher_gelesen(befugnis: ModuleType) -> None:
    text = befugnis.start_text("7")
    klasse_3 = text[text.index("Klasse 3") :]
    # Der Aufseher liest heute Bau-Log ``blockiert`` und antwortet per Issue-Kommentar.
    assert "eintrag --ticket 7 --typ blockiert" in klasse_3
    assert "gh issue view 7 --comments" in klasse_3
    assert "in den Briefkasten" not in klasse_3
    assert "in dieser Datei" not in klasse_3


def test_start_text_nennt_klasse_1_listen_aus_tabelle(befugnis: ModuleType) -> None:
    text = befugnis.start_text("7")
    klasse_1 = text[: text.index("Klasse 2")]
    for regel in befugnis._KLASSE_1:
        for wort in (regel.ziele or ()) | (regel.arten or ()):
            assert wort in klasse_1, wort
    assert "pushen" in klasse_1 and "hauptzweig" not in klasse_1


@pytest.mark.parametrize(
    "wort",
    ["zahlung", "zahlen", "auszahlung", "zahl", "abbuchung", "rechnung", "kredit",
     "payment", "billing", "passwort", "password", "login", "abo", "abo-kuendigen"],
)
def test_neue_klasse_2_woerter(befugnis: ModuleType, wort: str) -> None:
    assert befugnis.einordnen(wort, "ändern", True) == 2
    assert befugnis.einordnen("staging", wort, True) == 2


@pytest.mark.parametrize("ziel", ["labor", "anzahl", "staging"])
def test_abo_zahl_nur_ganzes_wort(befugnis: ModuleType, ziel: str) -> None:
    assert befugnis.einordnen(ziel, "ändern", True) != 2


def test_deploy_auf_staging_bleibt(befugnis: ModuleType) -> None:
    assert befugnis.einordnen("staging", "deploy", True) == 3
