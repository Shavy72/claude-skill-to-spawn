"""Briefkasten, Seite der Session (#278 P2, Tür B; Lastenheft L8 A45–A49).

Prüft ohne gh und ohne Claude-Session (tmp_path): nach dem Einwurf meldet ``antwort N``
Exit 3 „WARTET“, nach ``antworten`` Exit 0 mit der Wahl (Abnahme 3, 4, 5); Klasse 2 wird
nie beantwortet, auch eine von Hand eingetragene Antwort ergibt Exit 2 (Abnahme 2); eine halbe
Datei sperrt (NUTZER); ``entscheidungen S`` listet Klasse-1-Einträge und Briefkasten-Antworten
(wer, was, warum, Beleg; Abnahme 8). Dazu die Regel-Änderungen an ``befugnis.einordnen``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
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
    fid = briefkasten.einwerfen("7", "Blocker erledigt?", "weiterbauen", 3, ["#5 zu"], hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.WARTET
    assert [(o["ticket"], o["id"]) for o in briefkasten.offene(hauptbaum=tmp_path)] == [("7", fid)]
    briefkasten.antworten("7", fid, "weiterbauen", ["Lastenheft A46"], "Blocker sind zu", hauptbaum=tmp_path)
    antwort = briefkasten.lesen("7", fid, hauptbaum=tmp_path)
    assert antwort.wahl == "weiterbauen" and antwort.von == "aufseher"
    assert briefkasten.offene(hauptbaum=tmp_path) == []
    assert (tmp_path / ".to-spawn" / "entscheidung-7.json").is_file()


def test_zweite_frage_wartet_wieder_und_alte_antwort_bleibt(briefkasten: ModuleType, tmp_path: Path) -> None:
    fa = briefkasten.einwerfen("7", "A?", "a", 3, [], hauptbaum=tmp_path)
    briefkasten.antworten("7", fa, "a", [], "passt", hauptbaum=tmp_path)
    fb = briefkasten.einwerfen("7", "B?", "b", 3, [], hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fb, hauptbaum=tmp_path) == briefkasten.WARTET
    assert [e["was"] for e in briefkasten.liste(["7"], hauptbaum=tmp_path)] == ["A? → a"]


def test_klasse_2_verweigert_antworten(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.antworten("7", fid, "ja", [], "egal", hauptbaum=tmp_path)


def test_klasse_2_von_hand_beantwortet_bleibt_nutzer(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    datei = tmp_path / ".to-spawn" / "entscheidung-7.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["fragen"][-1]["antwort"] = {"wahl": "ja", "belege": [], "grund": "", "von": "aufseher"}
    datei.write_text(json.dumps(daten), encoding="utf-8")
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER
    assert briefkasten.lesen("7", hauptbaum=tmp_path) == briefkasten.NUTZER


def test_halbe_datei_sperrt(briefkasten: ModuleType, tmp_path: Path) -> None:
    # Seit PR 306 strenger: halbe Datei = NUTZER (fail-closed, sichtbar), nicht mehr WARTET.
    fid = briefkasten.einwerfen("7", "A?", "a", 3, [], hauptbaum=tmp_path)
    briefkasten.antworten("7", fid, "a", [], "passt", hauptbaum=tmp_path)
    datei = tmp_path / ".to-spawn" / "entscheidung-7.json"
    inhalt = datei.read_text(encoding="utf-8")
    datei.write_text(inhalt[: len(inhalt) // 2], encoding="utf-8")
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER


def test_kaputte_datei_wird_beim_einwurf_nicht_ueberschrieben(briefkasten: ModuleType, tmp_path: Path) -> None:
    ordner = tmp_path / ".to-spawn"
    ordner.mkdir()
    (ordner / "entscheidung-7.json").write_text('{"fragen": [', encoding="utf-8")
    briefkasten.einwerfen("7", "A?", "a", 3, [], hauptbaum=tmp_path)
    (kaputt,) = ordner.glob("entscheidung-7.json.kaputt-*")
    assert kaputt.read_text(encoding="utf-8") == '{"fragen": ['


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
        briefkasten.antworten("7", "abcd", "a", [], "passt", hauptbaum=tmp_path)


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
    fid = _id(eingeworfen.stdout)
    wartet = _cli(tmp_path, "antwort", "7", "--id", fid)
    assert wartet.returncode == 3 and "WARTET" in wartet.stdout
    beantwortet = _cli(
        tmp_path,
        "antworten",
        "7",
        "--id",
        fid,
        "--wahl",
        "weiterbauen",
        "--grund",
        "Blocker zu",
        "--beleg",
        "gh issue 5",
    )
    assert beantwortet.returncode == 0, beantwortet.stderr
    fertig = _cli(tmp_path, "antwort", "7", "--id", fid)
    assert fertig.returncode == 0 and "weiterbauen" in fertig.stdout


def test_cli_klasse_2(tmp_path: Path) -> None:
    eingeworfen = _cli(tmp_path, "frage", "7", "--frage", "Live?", "--vorschlag", "nein", "--klasse", "2")
    assert eingeworfen.returncode == 0
    fid = _id(eingeworfen.stdout)
    assert _cli(tmp_path, "antworten", "7", "--id", fid, "--wahl", "ja", "--grund", "x").returncode == 2
    nutzer = _cli(tmp_path, "antwort", "7", "--id", fid)
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
    eingeworfen = _cli(tmp_path, "frage", "7", "--frage", "Blocker?", "--vorschlag", "bauen")
    assert eingeworfen.returncode == 0
    fid = _id(eingeworfen.stdout)
    assert _cli(tmp_path, "antworten", "7", "--id", fid, "--wahl", "bauen", "--grund", "Blocker zu", "--beleg", "#5").returncode == 0
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


# --- Fix-Runde PR 303: H1 Klasse/Selbstantwort/fremdes Ticket, H2 Bindung/Sperre ----------


def _id(ausgabe: str) -> str:
    treffer = re.search(r"id ([0-9a-f]+)", ausgabe)
    assert treffer, ausgabe
    return treffer.group(1)


@pytest.mark.parametrize(
    ("frage", "vorschlag", "ziel", "art"),
    [
        ("Live schalten?", "ja", "", ""),
        ("Gebühr für das Werkzeug zahlen?", "ja", "", ""),
        ("Ausrollen?", "ja", "", "deploy --live"),
        ("Ausrollen?", "ja", "staging;live", "deploy"),
    ],
)
def test_einwurf_ordnet_klasse_2_selbst_ein(
    briefkasten: ModuleType, tmp_path: Path, frage: str, vorschlag: str, ziel: str, art: str
) -> None:
    fid = briefkasten.einwerfen("7", frage, vorschlag, 3, [], ziel=ziel, art=art, hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.antworten("7", fid, "ja", [], "passt", hauptbaum=tmp_path)
    daten = json.loads((tmp_path / ".to-spawn" / "entscheidung-7.json").read_text(encoding="utf-8"))
    assert daten["fragen"][-1]["klasse"] == 2


def test_klasse_vorgabe_macht_nur_strenger(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid = briefkasten.einwerfen("7", "Ordner umbenennen?", "ja", 2, [], hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER


def test_cli_frage_ohne_klasse_live_ist_nutzer(tmp_path: Path) -> None:
    eingeworfen = _cli(tmp_path, "frage", "7", "--frage", "Live schalten?", "--vorschlag", "ja")
    assert eingeworfen.returncode == 0, eingeworfen.stderr
    fid = _id(eingeworfen.stdout)
    assert _cli(tmp_path, "antworten", "7", "--id", fid, "--wahl", "ja", "--grund", "x").returncode == 2
    assert _cli(tmp_path, "antwort", "7", "--id", fid).returncode == 2


def test_bau_session_darf_nicht_antworten(
    briefkasten: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = briefkasten.einwerfen("7", "Blocker zu?", "weiterbauen", 3, [], hauptbaum=tmp_path)
    monkeypatch.setenv("TO_SPAWN_TICKET", "7")
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.antworten("7", fid, "weiterbauen", [], "selbst", von="aufseher", hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.WARTET


def test_cli_bau_session_antwortet_nicht(tmp_path: Path) -> None:
    fid = _id(_cli(tmp_path, "frage", "7", "--frage", "Blocker zu?", "--vorschlag", "bauen").stdout)
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env.update(TO_SPAWN_REPO=str(tmp_path), TO_SPAWN_TICKET="7")
    lauf = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "antworten", "7", "--id", fid, "--wahl", "bauen", "--grund", "x"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    assert lauf.returncode == 2, lauf.stderr
    assert _cli(tmp_path, "antwort", "7", "--id", fid).returncode == 3


def test_fremdes_ticket_einwerfen_verweigert(
    briefkasten: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TO_SPAWN_TICKET", "12")
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.einwerfen("13", "A?", "a", 3, [], hauptbaum=tmp_path)
    assert not (tmp_path / ".to-spawn" / "entscheidung-13.json").exists()
    assert briefkasten.einwerfen("012", "A?", "a", 3, [], hauptbaum=tmp_path)


def test_antwort_fuer_q1_landet_nicht_an_q2(briefkasten: ModuleType, tmp_path: Path) -> None:
    q1 = briefkasten.einwerfen("7", "Q1?", "a", 3, [], hauptbaum=tmp_path)
    q2 = briefkasten.einwerfen("7", "Q2?", "b", 3, [], hauptbaum=tmp_path)
    assert q1 != q2
    briefkasten.antworten("7", q1, "a", [], "für Q1", hauptbaum=tmp_path)  # P3a: jede offene per id
    assert briefkasten.lesen("7", q2, hauptbaum=tmp_path) == briefkasten.WARTET
    briefkasten.antworten("7", q2, "b", [], "für Q2", hauptbaum=tmp_path)
    assert briefkasten.lesen("7", q2, hauptbaum=tmp_path).wahl == "b"
    assert briefkasten.lesen("7", q1, hauptbaum=tmp_path).wahl == "a"
    assert briefkasten.lesen("7", "ffff", hauptbaum=tmp_path) == briefkasten.UNBEKANNT  # P3b: unbekannte id


def test_zwei_schreiber_verlieren_nichts(
    briefkasten: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A liest und hängt beim Schreiben; B wirft dazwischen ein — ohne Sperre geht B verloren."""
    echt = briefkasten.manifest.schreibe_atomar
    a_schreibt, b_fertig = threading.Event(), threading.Event()

    def langsam(datei: Path, daten: dict) -> None:
        if threading.current_thread().name == "A":
            a_schreibt.set()
            b_fertig.wait(1.0)
        echt(datei, daten)

    monkeypatch.setattr(briefkasten.manifest, "schreibe_atomar", langsam)

    def b() -> None:
        a_schreibt.wait(5)
        briefkasten.einwerfen("7", "B?", "b", 3, [], hauptbaum=tmp_path)
        b_fertig.set()

    faden_a = threading.Thread(
        name="A", target=briefkasten.einwerfen, args=("7", "A?", "a", 3, []), kwargs={"hauptbaum": tmp_path}
    )
    faden_b = threading.Thread(name="B", target=b)
    faden_a.start()
    faden_b.start()
    faden_a.join(10)
    faden_b.join(10)
    daten = json.loads((tmp_path / ".to-spawn" / "entscheidung-7.json").read_text(encoding="utf-8"))
    assert sorted(f["frage"] for f in daten["fragen"]) == ["A?", "B?"]


def test_offene_klasse_2_sperrt_trotz_klasse_3_danach(briefkasten: ModuleType, tmp_path: Path) -> None:
    briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    q3 = briefkasten.einwerfen("7", "Ordner?", "neu", 3, [], hauptbaum=tmp_path)
    assert briefkasten.lesen("7", q3, hauptbaum=tmp_path) == briefkasten.NUTZER
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.antworten("7", q3, "neu", [], "passt", hauptbaum=tmp_path)


def test_klasse_2_nach_nutzer_antwort_gibt_klasse_3_frei(briefkasten: ModuleType, tmp_path: Path) -> None:
    q2 = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    datei = tmp_path / ".to-spawn" / "entscheidung-7.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["fragen"][-1]["antwort"] = {"wahl": "nein", "von": "nutzer"}
    datei.write_text(json.dumps(daten), encoding="utf-8")
    assert briefkasten.lesen("7", q2, hauptbaum=tmp_path).von == "nutzer"  # P3a: Session sieht sie
    q3 = briefkasten.einwerfen("7", "Ordner?", "neu", 3, [], hauptbaum=tmp_path)
    briefkasten.antworten("7", q3, "neu", [], "passt", hauptbaum=tmp_path)
    assert briefkasten.lesen("7", q3, hauptbaum=tmp_path).wahl == "neu"


@pytest.mark.parametrize("wahl", ["", "   "])
def test_leere_wahl_verweigert(briefkasten: ModuleType, tmp_path: Path, wahl: str) -> None:
    fid = briefkasten.einwerfen("7", "A?", "a", 3, [], hauptbaum=tmp_path)
    with pytest.raises(ValueError):
        briefkasten.antworten("7", fid, wahl, [], "passt", hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.WARTET


def test_liste_zeigt_klasse_2_nie_als_aufseher_antwort(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid = briefkasten.einwerfen("7", "Live schalten?", "ja", 3, [], hauptbaum=tmp_path)
    datei = tmp_path / ".to-spawn" / "entscheidung-7.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["fragen"][-1]["klasse"] = 3  # von Hand zurückgestuft
    daten["fragen"][-1]["antwort"] = {"wahl": "ja", "von": "aufseher", "grund": "x", "belege": []}
    datei.write_text(json.dumps(daten), encoding="utf-8")
    assert briefkasten.liste(["7"], hauptbaum=tmp_path) == []
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER


def test_antworten_auf_beantwortete_frage(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid = briefkasten.einwerfen("7", "A?", "a", 3, [], hauptbaum=tmp_path)
    briefkasten.antworten("7", fid, "a", [], "passt", hauptbaum=tmp_path)
    with pytest.raises(ValueError):
        briefkasten.antworten("7", fid, "b", [], "doch nicht", hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path).wahl == "a"


def test_antworten_bei_kaputter_datei(briefkasten: ModuleType, tmp_path: Path) -> None:
    ordner = tmp_path / ".to-spawn"
    ordner.mkdir()
    (ordner / "entscheidung-7.json").write_text('{"fragen": [', encoding="utf-8")
    with pytest.raises(briefkasten.Verweigert):  # seit PR 306: unlesbar sperrt (vorher ValueError)
        briefkasten.antworten("7", "abcd", "a", [], "passt", hauptbaum=tmp_path)
    assert (ordner / "entscheidung-7.json").read_text(encoding="utf-8") == '{"fragen": ['


@pytest.mark.parametrize(
    "inhalt",
    [
        "[]",
        '{"fragen": "x"}',
        '{"fragen": [1]}',
        '{"fragen": [{"id": "ab", "klasse": 3, "antwort": "ja"}]}',
        '{"fragen": [{"id": "ab", "klasse": 3, "antwort": {"wahl": ""}}]}',
        '{"fragen": [{"id": "ab", "antwort": {"wahl": "ja"}}]}',
    ],
)
def test_lesen_faellt_sicher_zurueck(briefkasten: ModuleType, tmp_path: Path, inhalt: str) -> None:
    ordner = tmp_path / ".to-spawn"
    ordner.mkdir()
    (ordner / "entscheidung-7.json").write_text(inhalt, encoding="utf-8")
    assert not isinstance(briefkasten.lesen("7", "ab", hauptbaum=tmp_path), briefkasten.Antwort)


def test_kaputt_datei_mit_zeitstempel(briefkasten: ModuleType, tmp_path: Path) -> None:
    ordner = tmp_path / ".to-spawn"
    ordner.mkdir()
    for inhalt in ("{", "["):
        (ordner / "entscheidung-7.json").write_text(inhalt, encoding="utf-8")
        briefkasten.einwerfen("7", "A?", "a", 3, [], hauptbaum=tmp_path)
        (ordner / "entscheidung-7.json").write_text(inhalt, encoding="utf-8")
        briefkasten.einwerfen("7", "A?", "a", 3, [], hauptbaum=tmp_path)
    assert len(list(ordner.glob("entscheidung-7.json.kaputt-*"))) == 4


_UNGUELTIGE_BYTES = b"\xff\xfe\x00kaputt"


def test_ungueltige_bytes_sperren_statt_abzustuerzen(briefkasten: ModuleType, tmp_path: Path) -> None:
    ordner = tmp_path / ".to-spawn"
    ordner.mkdir()
    (ordner / "entscheidung-7.json").write_bytes(_UNGUELTIGE_BYTES)
    assert briefkasten.lesen("7", "ab", hauptbaum=tmp_path) == briefkasten.NUTZER
    (eintrag,) = briefkasten.offene(["7"], hauptbaum=tmp_path)
    assert eintrag["klasse"] == "2" and eintrag["id"] == ""
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.antworten("7", "ab", "a", [], "passt", hauptbaum=tmp_path)
    assert (ordner / "entscheidung-7.json").read_bytes() == _UNGUELTIGE_BYTES


def test_ungueltige_bytes_beim_einwurf_beiseitegelegt(briefkasten: ModuleType, tmp_path: Path) -> None:
    ordner = tmp_path / ".to-spawn"
    ordner.mkdir()
    (ordner / "entscheidung-7.json").write_bytes(_UNGUELTIGE_BYTES)
    briefkasten.einwerfen("7", "A?", "a", 3, [], hauptbaum=tmp_path)
    (kaputt,) = ordner.glob("entscheidung-7.json.kaputt-*")
    assert kaputt.read_bytes() == _UNGUELTIGE_BYTES
    assert briefkasten.lesen("7", "ab", hauptbaum=tmp_path) == briefkasten.NUTZER


def test_liste_meldet_kaputte_datei(
    briefkasten: ModuleType, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ordner = tmp_path / ".to-spawn"
    ordner.mkdir()
    (ordner / "entscheidung-7.json").write_text("[]", encoding="utf-8")
    with caplog.at_level("WARNING", logger="to_spawn.briefkasten"):
        assert briefkasten.liste(["7"], hauptbaum=tmp_path) == []
    assert any("entscheidung-7.json" in r.getMessage() for r in caplog.records)
