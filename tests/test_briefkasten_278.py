"""Briefkasten, Seite der Session (#278 P2, Tür B; Lastenheft L8 A45–A49).

Prüft ohne gh und ohne Claude-Session (tmp_path): nach dem Einwurf meldet ``antwort N``
Exit 3 „WARTET“, nach ``antworten`` Exit 0 mit der Wahl (Abnahme 3, 4, 5); Klasse 2 wird
nie beantwortet, auch eine von Hand eingetragene Antwort ergibt Exit 2 (Abnahme 2); eine halbe
Datei zählt als WARTET; ``entscheidungen S`` listet Klasse-1-Einträge und Briefkasten-Antworten
(wer, was, warum, Beleg; Abnahme 8). Dazu die Regel-Änderungen an ``befugnis.einordnen``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))


@pytest.fixture(scope="module")
def briefkasten() -> ModuleType:
    from to_spawn import briefkasten as modul

    return modul


@pytest.fixture(scope="module")
def befugnis() -> ModuleType:
    from to_spawn import befugnis as modul

    return modul


def _cli(repo: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env["TO_SPAWN_REPO"] = str(repo)
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), *argv],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# --- Modul -------------------------------------------------------------------


def test_ohne_frage_wartet(briefkasten: ModuleType, tmp_path: Path) -> None:
    assert briefkasten.lesen("7", hauptbaum=tmp_path) == briefkasten.WARTET


def test_einwurf_dann_antwort(briefkasten: ModuleType, tmp_path: Path) -> None:
    briefkasten.einwerfen("7", "Blocker erledigt?", "weiterbauen", 3, ["#5 zu"], hauptbaum=tmp_path)
    assert briefkasten.lesen("7", hauptbaum=tmp_path) == briefkasten.WARTET
    assert briefkasten.offene(hauptbaum=tmp_path) == ["7"]
    briefkasten.antworten("7", "weiterbauen", ["Lastenheft A46"], "Blocker sind zu", hauptbaum=tmp_path)
    antwort = briefkasten.lesen("7", hauptbaum=tmp_path)
    assert antwort.wahl == "weiterbauen" and antwort.von == "aufseher"
    assert briefkasten.offene(hauptbaum=tmp_path) == []
    assert (tmp_path / ".to-spawn" / "entscheidung-7.json").is_file()


def test_zweite_frage_wartet_wieder_und_alte_antwort_bleibt(briefkasten: ModuleType, tmp_path: Path) -> None:
    briefkasten.einwerfen("7", "A?", "a", 3, [], hauptbaum=tmp_path)
    briefkasten.antworten("7", "a", [], "passt", hauptbaum=tmp_path)
    briefkasten.einwerfen("7", "B?", "b", 3, [], hauptbaum=tmp_path)
    assert briefkasten.lesen("7", hauptbaum=tmp_path) == briefkasten.WARTET
    assert [e["was"] for e in briefkasten.liste(["7"], hauptbaum=tmp_path)] == ["A? → a"]


def test_klasse_2_verweigert_antworten(briefkasten: ModuleType, tmp_path: Path) -> None:
    briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    assert briefkasten.lesen("7", hauptbaum=tmp_path) == briefkasten.NUTZER
    with pytest.raises(PermissionError):
        briefkasten.antworten("7", "ja", [], "egal", hauptbaum=tmp_path)


def test_klasse_2_von_hand_beantwortet_bleibt_nutzer(briefkasten: ModuleType, tmp_path: Path) -> None:
    briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    datei = tmp_path / ".to-spawn" / "entscheidung-7.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["fragen"][-1]["antwort"] = {"wahl": "ja", "belege": [], "grund": "", "von": "aufseher"}
    datei.write_text(json.dumps(daten), encoding="utf-8")
    assert briefkasten.lesen("7", hauptbaum=tmp_path) == briefkasten.NUTZER


def test_halbe_datei_wartet(briefkasten: ModuleType, tmp_path: Path) -> None:
    briefkasten.einwerfen("7", "A?", "a", 3, [], hauptbaum=tmp_path)
    briefkasten.antworten("7", "a", [], "passt", hauptbaum=tmp_path)
    datei = tmp_path / ".to-spawn" / "entscheidung-7.json"
    inhalt = datei.read_text(encoding="utf-8")
    datei.write_text(inhalt[: len(inhalt) // 2], encoding="utf-8")
    assert briefkasten.lesen("7", hauptbaum=tmp_path) == briefkasten.WARTET


def test_kaputte_datei_wird_beim_einwurf_nicht_ueberschrieben(briefkasten: ModuleType, tmp_path: Path) -> None:
    ordner = tmp_path / ".to-spawn"
    ordner.mkdir()
    (ordner / "entscheidung-7.json").write_text('{"fragen": [', encoding="utf-8")
    briefkasten.einwerfen("7", "A?", "a", 3, [], hauptbaum=tmp_path)
    assert (ordner / "entscheidung-7.json.kaputt").read_text(encoding="utf-8") == '{"fragen": ['


@pytest.mark.parametrize("n", ["../7", "7/x", "", "٧", "-1"])
def test_ticket_nummer_nur_ziffern(briefkasten: ModuleType, tmp_path: Path, n: str) -> None:
    with pytest.raises(ValueError):
        briefkasten.einwerfen(n, "A?", "a", 3, [], hauptbaum=tmp_path)


@pytest.mark.parametrize("klasse", [1, 4, 0])
def test_einwurf_nur_klasse_2_oder_3(briefkasten: ModuleType, tmp_path: Path, klasse: int) -> None:
    with pytest.raises(ValueError):
        briefkasten.einwerfen("7", "A?", "a", klasse, [], hauptbaum=tmp_path)


def test_antworten_ohne_offene_frage(briefkasten: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        briefkasten.antworten("7", "a", [], "passt", hauptbaum=tmp_path)


# --- CLI ---------------------------------------------------------------------


def test_cli_frage_antwort_antworten(tmp_path: Path) -> None:
    eingeworfen = _cli(
        tmp_path,
        "frage",
        "7",
        "--frage",
        "Blocker erledigt?",
        "--vorschlag",
        "weiterbauen",
        "--beleg",
        "#5 zu",
    )
    assert eingeworfen.returncode == 0, eingeworfen.stderr
    wartet = _cli(tmp_path, "antwort", "7")
    assert wartet.returncode == 3 and "WARTET" in wartet.stdout
    beantwortet = _cli(
        tmp_path,
        "antworten",
        "7",
        "--wahl",
        "weiterbauen",
        "--grund",
        "Blocker zu",
        "--beleg",
        "gh issue 5",
    )
    assert beantwortet.returncode == 0, beantwortet.stderr
    fertig = _cli(tmp_path, "antwort", "7")
    assert fertig.returncode == 0 and "weiterbauen" in fertig.stdout


def test_cli_klasse_2(tmp_path: Path) -> None:
    assert _cli(tmp_path, "frage", "7", "--frage", "Live?", "--vorschlag", "nein", "--klasse", "2").returncode == 0
    assert _cli(tmp_path, "antworten", "7", "--wahl", "ja", "--grund", "x").returncode == 2
    nutzer = _cli(tmp_path, "antwort", "7")
    assert nutzer.returncode == 2 and "NUTZER" in nutzer.stdout


@pytest.mark.parametrize(
    ("ziel", "art", "code"),
    [("staging", "ändern", 3), ("live", "ändern", 2), ("arbeitsordner", "anlegen", 0)],
)
def test_cli_befugnis_exit(tmp_path: Path, ziel: str, art: str, code: int) -> None:
    assert _cli(tmp_path, "befugnis", "7", "--ziel", ziel, "--art", art).returncode == code


def test_cli_befugnis_gesichert(tmp_path: Path) -> None:
    assert _cli(tmp_path, "befugnis", "7", "--ziel", "staging", "--art", "ändern", "--gesichert").returncode == 0


def test_cli_eintrag_typ_frage(tmp_path: Path) -> None:
    fertig = _cli(
        tmp_path,
        "eintrag",
        "--ticket",
        "7",
        "--typ",
        "frage",
        "--frage",
        "A?",
        "--wahl",
        "a",
        "--repo",
        str(tmp_path),
    )
    assert fertig.returncode == 0, fertig.stderr


def test_cli_entscheidungen_listet_beide_quellen(tmp_path: Path) -> None:
    assert (
        _cli(
            tmp_path,
            "eintrag",
            "--ticket",
            "7",
            "--typ",
            "entscheidung",
            "--frage",
            "Ordner?",
            "--wahl",
            "neu anlegen",
            "--grund",
            "Klasse 1, eigener Arbeitsordner",
            "--repo",
            str(tmp_path),
        ).returncode
        == 0
    )
    assert _cli(tmp_path, "frage", "7", "--frage", "Blocker?", "--vorschlag", "bauen").returncode == 0
    assert _cli(tmp_path, "antworten", "7", "--wahl", "bauen", "--grund", "Blocker zu", "--beleg", "#5").returncode == 0
    liste = _cli(tmp_path, "entscheidungen", "99")
    assert liste.returncode == 0, liste.stderr
    zeilen = [z for z in liste.stdout.splitlines() if z.startswith("#7")]
    assert len(zeilen) == 2, liste.stdout
    assert "Bau-Session" in zeilen[0] and "Ordner? → neu anlegen" in zeilen[0]
    assert "Klasse 1, eigener Arbeitsordner" in zeilen[0]
    assert "aufseher" in zeilen[1] and "Blocker? → bauen" in zeilen[1]
    assert "Blocker zu" in zeilen[1] and "#5" in zeilen[1]


# --- befugnis: Regel-Änderungen in P2 ------------------------------------------


@pytest.mark.parametrize("gesichert", [False, True])
@pytest.mark.parametrize("art", ["deploy", "deployen"])
def test_deploy_auf_staging_ist_klasse_1(befugnis: ModuleType, art: str, gesichert: bool) -> None:
    """Lastenheft A47: alles auf Staging — Deploy dorthin ist Bau-Alltag, fasst keine Daten an."""
    assert befugnis.einordnen("staging", art, gesichert) == 1


@pytest.mark.parametrize("ziel", ["staging-live", "live", "produktion", "prod-staging", "master", "main", "vps"])
def test_deploy_ausser_staging_nie_klasse_1(befugnis: ModuleType, ziel: str) -> None:
    assert befugnis.einordnen(ziel, "deploy", True) != 1


@pytest.mark.parametrize("ziel", ["staging-live", "live", "produktion", "vps"])
def test_deploy_live_bleibt_klasse_2(befugnis: ModuleType, ziel: str) -> None:
    assert befugnis.einordnen(ziel, "deploy", True) == 2


@pytest.mark.parametrize(
    "wort",
    ["gebühr", "euro", "lizenz", "bestellung", "preis", "tarif", "vertrag", "bitwarden", ".env", "oauth", "2fa", "dns"],
)
def test_geld_und_zugang_woerter_sind_klasse_2(befugnis: ModuleType, wort: str) -> None:
    assert befugnis.einordnen("staging", wort, True) == 2
    assert befugnis.einordnen(f"{wort}-einstellung", "ändern", True) == 2


@pytest.mark.parametrize(
    "ziel", ["ticket/12-lizenz-check", "ticket/12-dns-fix", "ticket/12-preis-anzeige", "wip/12-oauth"]
)
def test_eigener_zweig_mit_neuen_woertern_bleibt_1(
    befugnis: ModuleType, monkeypatch: pytest.MonkeyPatch, ziel: str
) -> None:
    monkeypatch.setenv("TO_SPAWN_TICKET", "12")
    assert befugnis.einordnen(ziel, "pushen", True) == 1


def test_zweig_nummer_nur_ascii_ziffern(befugnis: ModuleType) -> None:
    assert befugnis._EIGENER_ZWEIG.fullmatch("ticket/١٢-x") is None
    assert befugnis._EIGENER_ZWEIG.fullmatch("ticket/12-x") is not None


def test_start_text_staging_deploy(befugnis: ModuleType) -> None:
    text = befugnis.start_text("7")
    assert "deploy" in text and "Staging" in text
