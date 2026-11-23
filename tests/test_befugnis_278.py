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
        ("wip-zweig", 3),  # Runde 5: symbolisch wie ticket-zweig
        ("wip/7-sicherung", 1),
        ("wip/278-sicherung", 3),  # Runde 4: wip nur mit eigener Nummer
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
            wort = befugnis._ziel_text(wort, "7")  # Runde 5: Marke → lesbare Regel
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


# --- Prüfer-Runde 4 (#278 P1): Push nur auf gültigen eigenen Zweignamen, mehr Klasse-2-Wörter ---


@pytest.mark.parametrize(
    "ziel",
    [
        "ticket/12-x:master",
        "ticket/12-x:refs/heads/master",
        "wip/x:master",
        "ticket/12-x master",
        "ticket/12-x;git push origin master",
        "ticket/12-+master",
        "ticket/12-x~1",
        "ticket/12-x@{0}",
        "ticket/12-x\tmaster",
        "ticket/12-x\nmaster",
        "wip/12-x:master",
        "wip/12-a..b",
        "wip/x",
        "wip/13-fremd",
        "ticket/13-fremd",
    ],
)
def test_push_fremdes_ziel_ist_klasse_3(
    befugnis: ModuleType, monkeypatch: pytest.MonkeyPatch, ziel: str
) -> None:
    monkeypatch.setenv("TO_SPAWN_TICKET", "12")
    assert befugnis.einordnen(ziel, "pushen", False) == 3


@pytest.mark.parametrize("ziel", ["ticket/12-x", "ticket/12-befugnis.p1_neu", "wip/12-sicherung"])
def test_push_gueltiger_eigener_zweig_bleibt_klasse_1(
    befugnis: ModuleType, monkeypatch: pytest.MonkeyPatch, ziel: str
) -> None:
    monkeypatch.setenv("TO_SPAWN_TICKET", "12")
    assert befugnis.einordnen(ziel, "pushen", False) == 1


@pytest.mark.parametrize(
    "wort",
    ["zugang", "secret", "credential", "api-key", "API_KEY", "apikey", "api-token",
     "access-token", "abonnement", "abos", "subscription", "Überweisung",
     "Überweisung", "ueberweisen", "invoice", "paypal", "stripe", "purchase",
     "kündigen", "kuendigen", "bezahlt", "pay", "buy", "cost", "charge", "ssh",
     "ssh-key", "server ssh"],
)
def test_runde_4_klasse_2_woerter(befugnis: ModuleType, wort: str) -> None:
    assert befugnis.einordnen(wort, "ändern", True) == 2
    assert befugnis.einordnen("staging", wort, True) == 2


@pytest.mark.parametrize(
    ("ziel", "art"),
    [("staging", "deploy"), ("token-zaehler", "ändern"), ("arbeitsordner", "token"),
     ("display", "ändern"), ("discharge", "ändern"), ("costume", "ändern")],
)
def test_runde_4_bewusst_nicht_klasse_2(befugnis: ModuleType, ziel: str, art: str) -> None:
    assert befugnis.einordnen(ziel, art, True) != 2


def test_start_text_trennt_wortteil_und_ganzes_wort(befugnis: ModuleType) -> None:
    text = befugnis.start_text("7")
    klasse_2 = text[text.index("Klasse 2") : text.index("Klasse 3")]
    teil, rest = klasse_2.split("auch als Wortteil;")
    ganz = rest.split("nur als ganzes Wort")[0]
    for wort in befugnis._KLASSE_2_WOERTER:
        assert wort in teil, wort
    assert sorted(w.strip() for w in ganz.split(",")) == sorted(befugnis._KLASSE_2_GANZWORT)


def test_start_text_zeigt_eigene_nummer(befugnis: ModuleType) -> None:
    klasse_1 = befugnis.start_text("7").split("Klasse 2")[0]
    assert "`ticket/7-<kurzname>`" in klasse_1 and "`wip/7-<kurzname>`" in klasse_1


# --- Runde 5 -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ziel",
    ["ticket/12-login-seite", "ticket/12-account-status", "ticket/12-live-ampel",
     "ticket/12-produkt-bild", "ticket/12-deliver", "ticket/12-olive",
     "ticket/12-kosten-anzeige", "wip/12-verkauf", "ticket/12-vpsfix",
     "ticket/12-zugangs-check", "ticket/12-secrets-scan", "ticket/12-pay",
     "ticket/12-a.b_c-1", "ticket/12-x"],
)
def test_r5_eigener_zweig_mit_woertern_im_kurznamen_ist_1(
    befugnis: ModuleType, monkeypatch: pytest.MonkeyPatch, ziel: str
) -> None:
    monkeypatch.setenv("TO_SPAWN_TICKET", "12")
    assert befugnis.einordnen(ziel, "pushen", True) == 1


@pytest.mark.parametrize(
    ("ziel", "art"),
    [("ticket/13-login-seite", "pushen"), ("ticket/13-x", "pushen"),
     ("ticket/12-x", "live schalten"), ("ticket/12-x", "kosten")],
)
def test_r5_gegenproben(
    befugnis: ModuleType, monkeypatch: pytest.MonkeyPatch, ziel: str, art: str
) -> None:
    monkeypatch.setenv("TO_SPAWN_TICKET", "12")
    assert befugnis.einordnen(ziel, art, True) != 1
    if art != "pushen":
        assert befugnis.einordnen(ziel, art, True) == 2


def test_r5_fremde_nummer_mit_klasse_2_wort_ist_2(
    befugnis: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TO_SPAWN_TICKET", "12")
    assert befugnis.einordnen("ticket/13-login-seite", "pushen", True) == 2


@pytest.mark.parametrize("ziel", ["wip-zweig", "WIP zweig", "wip_zweig"])
def test_r5_symbolisches_wip_ziel_ist_3(
    befugnis: ModuleType, monkeypatch: pytest.MonkeyPatch, ziel: str
) -> None:
    monkeypatch.setenv("TO_SPAWN_TICKET", "12")
    assert befugnis.einordnen(ziel, "pushen", True) == 3
    monkeypatch.delenv("TO_SPAWN_TICKET")
    assert befugnis.einordnen(ziel, "pushen", True) == 3


@pytest.mark.parametrize(
    "ziel", ["ticket/12--force", "ticket/12-x.lock", "ticket/12-x.", "ticket/12-."]
)
def test_r5_zweigname_endet_auf_buchstabe_oder_ziffer(
    befugnis: ModuleType, monkeypatch: pytest.MonkeyPatch, ziel: str
) -> None:
    monkeypatch.setenv("TO_SPAWN_TICKET", "12")
    assert befugnis.einordnen(ziel, "pushen", True) == 3


def test_r5_start_text_nennt_regel_statt_marke(befugnis: ModuleType) -> None:
    text = befugnis.start_text("12")
    assert "ticket/12-<kurzname>" in text and "ticket/<n>-…" not in text
    assert "Kleinbuchstaben, Ziffern" in text
    assert "ticket/<eigene Nummer>-<kurzname>" in befugnis.start_text("")
