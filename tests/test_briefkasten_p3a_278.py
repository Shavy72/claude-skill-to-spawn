"""Briefkasten, Lücken der Session-Seite (#278 P3a).

Prüft ohne gh und ohne Claude-Session (tmp_path): Klasse-2-Wörter auch hinter Satzzeichen;
eine beiseitegelegte ``.kaputt-*``-Datei sperrt das Ticket (fail-closed); der Nutzer antwortet
nur über ``nutzer-antwort`` (nie aus einer Bau-Session, nie ohne Terminal), ``--von`` ist feste
Auswahl; die Session liest die Antwort des Nutzers; jede offene Frage ist über ihre ``id``
beantwortbar; ``TO_SPAWN_TICKET=`` (leer) zählt als Bau-Session.
"""

from __future__ import annotations

import argparse
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


@pytest.fixture(autouse=True)
def _ohne_bau_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)


def _cli(repo: Path, *argv: str, **zusatz: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env["TO_SPAWN_REPO"] = str(repo)
    env.update(zusatz)
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), *argv],
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# --- 1: Klasse-2-Wörter an allen Nicht-Wortzeichen ---------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Darf ich per ssh, dann",
        "Zugang (ssh) nötig?",
        "„ssh“ auf den Server",
        '"SSH"',
        "erst prüfen\nssh danach",
        "Per SSH!",
        "ssh;ls",
        "Abo: verlängern?",
        "kostet das [cost]?",
    ],
)
def test_ganzwort_hinter_satzzeichen_ist_klasse_2(befugnis: ModuleType, text: str) -> None:
    assert befugnis.ist_klasse_2(text) is True


@pytest.mark.parametrize("text", ["sshd-Ordner aufräumen", "Größe prüfen"])
def test_ganzwort_nicht_als_wortteil(befugnis: ModuleType, text: str) -> None:
    assert befugnis.ist_klasse_2(text) is False


def test_einordnen_art_mit_satzzeichen(befugnis: ModuleType) -> None:
    assert befugnis.einordnen("arbeitsordner", "per ssh, dann ändern") == 2


# --- 2: kaputte Datei sperrt fail-closed -------------------------------------


def _kaputt_beiseite(briefkasten: ModuleType, tmp_path: Path) -> str:
    ordner = tmp_path / ".to-spawn"
    ordner.mkdir()
    (ordner / "entscheidung-7.json").write_text('{"fragen": [{"klasse": 2', encoding="utf-8")
    return briefkasten.einwerfen("7", "Ordner?", "neu", 3, [], hauptbaum=tmp_path)


def test_kaputt_datei_sperrt_ticket(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid = _kaputt_beiseite(briefkasten, tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.antworten("7", fid, "neu", [], "passt", hauptbaum=tmp_path)
    offen = briefkasten.offene(hauptbaum=tmp_path)
    assert any(o["ticket"] == "7" and o["klasse"] == "2" and "kaputt" in o["frage"] for o in offen)


def test_kaputt_geklaert_gibt_wieder_frei(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid = _kaputt_beiseite(briefkasten, tmp_path)
    for kaputt in (tmp_path / ".to-spawn").glob("entscheidung-7.json.kaputt-*"):
        kaputt.unlink()
    briefkasten.antworten("7", fid, "neu", [], "passt", hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path).wahl == "neu"


# --- 3: Nutzer-Weg ------------------------------------------------------------


def test_nutzer_antwortet_klasse_2(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    briefkasten.nutzer_antworten("7", fid, "nein", [], "noch nicht", hauptbaum=tmp_path)
    antwort = briefkasten.lesen("7", fid, hauptbaum=tmp_path)
    assert isinstance(antwort, briefkasten.Antwort)
    assert (antwort.wahl, antwort.von) == ("nein", briefkasten.NUTZER_VON)


def test_nutzer_weg_verweigert_in_bau_session(
    briefkasten: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    monkeypatch.setenv("TO_SPAWN_TICKET", "7")
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.nutzer_antworten("7", fid, "ja", [], "selbst", hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER


def test_antworten_von_nutzer_unmoeglich(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid = briefkasten.einwerfen("7", "Ordner?", "neu", 3, [], hauptbaum=tmp_path)
    with pytest.raises(ValueError):
        briefkasten.antworten("7", fid, "neu", [], "x", von="nutzer", hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.WARTET


def test_cli_von_nutzer_abgelehnt(tmp_path: Path) -> None:
    fid = _id(_cli(tmp_path, "frage", "7", "--frage", "Ordner?", "--vorschlag", "neu").stdout)
    lauf = _cli(tmp_path, "antworten", "7", "--id", fid, "--wahl", "neu", "--grund", "x", "--von", "nutzer")
    assert lauf.returncode == 2  # argparse: ungültige Auswahl
    assert _cli(tmp_path, "antwort", "7", "--id", fid).returncode == 3


def test_cli_nutzer_antwort_ohne_terminal_verweigert(tmp_path: Path) -> None:
    fid = _id(_cli(tmp_path, "frage", "7", "--frage", "Live?", "--vorschlag", "nein").stdout)
    # #531: tmp-HOME ohne eingetragene Ja-Sperre → ohne Terminal fail-closed, egal was auf dieser Maschine installiert ist
    lauf = _cli(
        tmp_path, "nutzer-antwort", "7", "--id", fid, "--wahl", "ja", "--grund", "x", HOME=str(tmp_path), USERPROFILE=str(tmp_path)
    )
    assert lauf.returncode == 2, lauf.stderr
    assert _cli(tmp_path, "antwort", "7", "--id", fid).returncode == 2


def test_cli_nutzer_antwort_im_terminal(
    briefkasten: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from to_spawn import briefkasten_cli

    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    monkeypatch.setenv("TO_SPAWN_LOG_RUECKFALL", str(tmp_path))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    # Unter Windows zählt nur eine echte Konsole; der Test täuscht das Terminal an der Naht vor.
    monkeypatch.setattr(briefkasten_cli.konsole, "stdin_ist_terminal", lambda: True)
    args = argparse.Namespace(
        befehl="nutzer-antwort", ticket="7", frage_id=fid, wahl="nein", grund="noch nicht", beleg=[]
    )
    assert briefkasten_cli.lauf(args) == 0
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path).von == "nutzer"
    args = argparse.Namespace(befehl="antwort", ticket="7", frage_id=fid)
    assert briefkasten_cli.lauf(args) == 0
    assert "nein" in capsys.readouterr().out


# --- 4: Session sieht die Antwort des Nutzers ---------------------------------


def test_liste_zeigt_nutzer_antwort(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    briefkasten.nutzer_antworten("7", fid, "nein", ["Chat"], "noch nicht", hauptbaum=tmp_path)
    zeilen = briefkasten.liste(["7"], hauptbaum=tmp_path)
    assert [(z["wer"], z["warum"]) for z in zeilen] == [("nutzer", "noch nicht")]


def test_von_hand_aufseher_bei_klasse_2_bleibt_nutzer(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    datei = tmp_path / ".to-spawn" / "entscheidung-7.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["fragen"][-1]["antwort"] = {"wahl": "ja", "von": "aufseher"}
    datei.write_text(json.dumps(daten), encoding="utf-8")
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER
    assert briefkasten.liste(["7"], hauptbaum=tmp_path) == []


# --- 5: jede offene Frage über ihre id --------------------------------------


def test_aeltere_offene_frage_beantwortbar(briefkasten: ModuleType, tmp_path: Path) -> None:
    q1 = briefkasten.einwerfen("7", "Q1?", "a", 3, [], hauptbaum=tmp_path)
    q2 = briefkasten.einwerfen("7", "Q2?", "b", 3, [], hauptbaum=tmp_path)
    assert {o["id"] for o in briefkasten.offene(hauptbaum=tmp_path)} == {q1, q2}
    briefkasten.antworten("7", q1, "a", [], "für Q1", hauptbaum=tmp_path)
    assert briefkasten.lesen("7", q1, hauptbaum=tmp_path).wahl == "a"
    assert briefkasten.lesen("7", q2, hauptbaum=tmp_path) == briefkasten.WARTET
    assert [o["id"] for o in briefkasten.offene(hauptbaum=tmp_path)] == [q2]
    with pytest.raises(ValueError):
        briefkasten.antworten("7", q1, "c", [], "zweimal", hauptbaum=tmp_path)


def test_offene_meldet_klasse(briefkasten: ModuleType, tmp_path: Path) -> None:
    q2 = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    (offen,) = briefkasten.offene(["7"], hauptbaum=tmp_path)
    assert (offen["ticket"], offen["id"], offen["klasse"], offen["vorschlag"]) == ("7", q2, "2", "nein")


# --- 6: leeres TO_SPAWN_TICKET zählt als Bau-Session -------------------------


def test_leeres_ticket_ist_bau_session(
    briefkasten: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = briefkasten.einwerfen("7", "Ordner?", "neu", 3, [], hauptbaum=tmp_path)
    monkeypatch.setenv("TO_SPAWN_TICKET", "")
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.antworten("7", fid, "neu", [], "selbst", hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.WARTET


def test_cli_leeres_ticket_antwortet_nicht(tmp_path: Path) -> None:
    fid = _id(_cli(tmp_path, "frage", "7", "--frage", "Ordner?", "--vorschlag", "neu").stdout)
    lauf = _cli(tmp_path, "antworten", "7", "--id", fid, "--wahl", "neu", "--grund", "x", TO_SPAWN_TICKET="")
    assert lauf.returncode == 2, lauf.stderr


def _id(ausgabe: str) -> str:
    return ausgabe.strip().rsplit(" ", 1)[-1]


# --- 7: Fix-Runde PR 306 — unlesbare Datei, ungültige Antwort, Lesefehler -----


def _datei_7(tmp_path: Path, inhalt: str) -> Path:
    ordner = tmp_path / ".to-spawn"
    ordner.mkdir(exist_ok=True)
    datei = ordner / "entscheidung-7.json"
    datei.write_text(inhalt, encoding="utf-8")
    return datei


@pytest.mark.parametrize("inhalt", ['{"fragen": [{"klasse": 2', "[]", '{"fragen": "x"}'])
def test_unlesbare_datei_sperrt_ohne_einwurf(briefkasten: ModuleType, tmp_path: Path, inhalt: str) -> None:
    datei = _datei_7(tmp_path, inhalt)
    assert briefkasten.lesen("7", "ab", hauptbaum=tmp_path) == briefkasten.NUTZER
    (offen,) = briefkasten.offene(hauptbaum=tmp_path)
    assert (offen["ticket"], offen["id"], offen["klasse"]) == ("7", "", "2")
    assert "kaputt" in offen["frage"]
    assert briefkasten.offene(["7"], hauptbaum=tmp_path) == [offen]
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.antworten("7", "ab", "a", [], "passt", hauptbaum=tmp_path)
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.nutzer_antworten("7", "ab", "a", [], "passt", hauptbaum=tmp_path)
    assert datei.read_text(encoding="utf-8") == inhalt
    assert not list(datei.parent.glob("entscheidung-7.json.kaputt-*"))


def _klasse_2_mit_aufseher_von_hand(briefkasten: ModuleType, tmp_path: Path) -> tuple[str, Path]:
    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    datei = tmp_path / ".to-spawn" / "entscheidung-7.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["fragen"][-1]["antwort"] = {"wahl": "ja", "von": "aufseher"}
    datei.write_text(json.dumps(daten), encoding="utf-8")
    return fid, datei


def test_offene_zeigt_frage_mit_ungueltiger_antwort(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid, _ = _klasse_2_mit_aufseher_von_hand(briefkasten, tmp_path)
    (offen,) = briefkasten.offene(hauptbaum=tmp_path)
    assert (offen["id"], offen["klasse"]) == (fid, "2")


def test_nutzer_gibt_frei_trotz_ungueltiger_antwort(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid, datei = _klasse_2_mit_aufseher_von_hand(briefkasten, tmp_path)
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.antworten("7", fid, "ja", [], "selbst", hauptbaum=tmp_path)
    briefkasten.nutzer_antworten("7", fid, "nein", [], "noch nicht", hauptbaum=tmp_path)
    antwort = briefkasten.lesen("7", fid, hauptbaum=tmp_path)
    assert isinstance(antwort, briefkasten.Antwort)
    assert (antwort.wahl, antwort.von) == ("nein", briefkasten.NUTZER_VON)
    assert briefkasten.offene(hauptbaum=tmp_path) == []
    frage = json.loads(datei.read_text(encoding="utf-8"))["fragen"][-1]
    assert frage["verworfen"] == [{"wahl": "ja", "von": "aufseher"}]  # nachvollziehbar erhalten
    with pytest.raises(ValueError):
        briefkasten.nutzer_antworten("7", fid, "ja", [], "zweimal", hauptbaum=tmp_path)


def test_lesefehler_sperrt_ohne_beiseitelegen(
    briefkasten: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    briefkasten.einwerfen("7", "Ordner?", "neu", 3, [], hauptbaum=tmp_path)
    datei = tmp_path / ".to-spawn" / "entscheidung-7.json"
    inhalt = datei.read_text(encoding="utf-8")
    echt = Path.read_text

    def lesefehler(self: Path, *args: object, **kwargs: object) -> str:
        if self == datei:
            raise PermissionError(13, "Zugriff verweigert")
        return echt(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", lesefehler)
    assert briefkasten.lesen("7", "ab", hauptbaum=tmp_path) == briefkasten.NUTZER
    (offen,) = briefkasten.offene(hauptbaum=tmp_path)
    assert (offen["id"], offen["klasse"]) == ("", "2")
    with pytest.raises(OSError):
        briefkasten.einwerfen("7", "Noch was?", "x", 3, [], hauptbaum=tmp_path)
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.antworten("7", "ab", "a", [], "x", hauptbaum=tmp_path)
    monkeypatch.undo()
    assert not list(datei.parent.glob("entscheidung-7.json.kaputt-*"))
    assert datei.read_text(encoding="utf-8") == inhalt
