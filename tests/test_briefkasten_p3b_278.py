"""Briefkasten, Aufseher-Seite (#278 P3b).

Prüft ohne Claude-Session (tmp_path): Start-Text zeigt auf ``frage``/``antwort`` statt auf das
Bau-Log; Aufseher-Texte (Takt + Dauer-Aufseher) tragen die Klasse-2-Regel aus EINER Quelle in
``befugnis``, den Briefkasten-Weg und das Umgehungs-Verbot; unbekannte Frage-``id`` ergibt
„Frage neu stellen“; ``nutzer-antwort`` weist auf liegende ``.kaputt-*`` hin; die
Entscheidungs-Liste ohne Manifest kennt Tickets, die nur im Briefkasten stehen; die Antwort des
Nutzers per Ticket-Kommentar wird nur mit prüfbarem Kommentar des Repo-Besitzers übertragen
(gh = externer Dienst: Form aus einer echten Aufzeichnung, ``tests/hilfen/gh_kommentar_278.json``).
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
HILFEN = SKILL / "tests" / "hilfen"
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


# --- 1: Start-Text ----------------------------------------------------------------


def test_start_text_zeigt_auf_briefkasten_befehle(befugnis: ModuleType) -> None:
    text = befugnis.start_text("7")
    assert "keinen Leser" not in text
    klasse_3 = text[text.index("Klasse 3") :]
    assert "to_spawn.py frage 7 --frage" in klasse_3
    assert "--vorschlag" in klasse_3
    assert "to_spawn.py antwort 7 --id <id>" in klasse_3
    assert "neu stellen" in klasse_3  # Exit 4: unbekannte id
    klasse_2 = text[text.index("Klasse 2") : text.index("Klasse 3")]
    assert "--klasse 2" in klasse_2


# --- 4 + 5: Aufseher-Texte aus einer Quelle ----------------------------------------


def test_aufseher_text_klasse_2_und_umgehungs_verbot(befugnis: ModuleType) -> None:
    text = befugnis.aufseher_text("python X/to_spawn.py")
    assert "Klasse 2" in text and "nie selbst freigeben, geht an den Nutzer" in text
    assert befugnis.klasse_2_erkennung() in text
    assert "python X/to_spawn.py antworten <N> --id <id>" in text
    assert "python X/to_spawn.py offene" in text
    for verboten in ("script -qc", "nutzer_antworten", "Briefkasten-Datei"):
        assert verboten in text
    assert "{" not in text and "}" not in text  # wird später per .format() gefüllt


def test_takt_prompt_traegt_aufseher_text(befugnis: ModuleType) -> None:
    from to_spawn import waechter_takt

    prompt = waechter_takt.baue_prompt(293, Path("/r"), ["- x"], None)
    assert befugnis.aufseher_text(f"python {waechter_takt.SKILL.as_posix()}/to_spawn.py") in prompt
    assert "Jede Rückfrage (Bau-Log" not in prompt  # alte Formel ohne Klasse 2


def test_wache_prompt_traegt_aufseher_text_und_klasse_2_fenster(befugnis: ModuleType) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("wache_p3b_278", SKILL / "skripte" / "wache.py")
    assert spec and spec.loader
    wache = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wache)
    text = wache.prompt_bauen(293, "/r", 1800, {})
    assert "nie selbst freigeben, geht an den Nutzer" in text
    assert "antworten <N> --id <id>" in text
    assert "Spiegel-Kommentar" in text
    fenster = text[text.index("## Rückfragen-Fenster") : text.index("## Checkpoints")]
    assert befugnis.klasse_2_erkennung() in fenster
    assert "Live-Bezug (Live-URL" not in fenster  # eigene Liste weg, Quelle = befugnis


# --- 2: Takt sieht Briefkasten-Fragen (Text je Klasse) ---------------------------


def test_takt_eintraege_aus_briefkasten(briefkasten: ModuleType, tmp_path: Path) -> None:
    from to_spawn import waechter_takt

    k3 = briefkasten.einwerfen("297", "Ordner neu?", "neu", 3, [], hauptbaum=tmp_path)
    k2 = briefkasten.einwerfen("298", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    (tmp_path / ".to-spawn" / "entscheidung-299.json").write_text("{kaputt", encoding="utf-8")
    eintraege = waechter_takt.briefkasten_eintraege(tmp_path, [297, 298, 299, 300], set())
    texte = {k: t for t, feld, k in eintraege if feld == "fragen"}
    assert len(eintraege) == 3
    assert "du entscheidest" in texte[f"297:{k3}"] and "Vorschlag: neu" in texte[f"297:{k3}"]
    assert "wartet auf den Nutzer" in texte[f"298:{k2}"] and "nie selbst freigeben" in texte[f"298:{k2}"]
    (kaputt,) = [t for k, t in texte.items() if k.startswith("299:")]
    assert "wartet auf den Nutzer" in kaputt
    gesehen = set(texte)
    assert waechter_takt.briefkasten_eintraege(tmp_path, [297, 298, 299], gesehen) == []


# --- 7: unbekannte id --------------------------------------------------------------


def test_unbekannte_id_heisst_neu_stellen(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid = briefkasten.einwerfen("7", "Ordner?", "neu", 3, [], hauptbaum=tmp_path)
    assert briefkasten.lesen("7", "ffff", hauptbaum=tmp_path) == briefkasten.UNBEKANNT
    (tmp_path / ".to-spawn" / "entscheidung-7.json").unlink()  # Nutzer hat gelöscht
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.UNBEKANNT
    assert briefkasten.lesen("7", hauptbaum=tmp_path) == briefkasten.WARTET  # ohne id wie bisher


def test_cli_antwort_unbekannte_id_exit_4(tmp_path: Path) -> None:
    lauf = _cli(tmp_path, "antwort", "7", "--id", "ffff")
    assert lauf.returncode == 4, lauf.stdout + lauf.stderr
    assert "neu stellen" in lauf.stdout


# --- 8: Hinweis auf .kaputt-* -------------------------------------------------------


def test_nutzer_antwort_meldet_liegende_kaputt_datei(
    briefkasten: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from to_spawn import briefkasten_cli

    ordner = tmp_path / ".to-spawn"
    ordner.mkdir()
    (ordner / "entscheidung-7.json").write_text('{"fragen": [', encoding="utf-8")
    fid = briefkasten.einwerfen("7", "Ordner?", "neu", 3, [], hauptbaum=tmp_path)
    monkeypatch.setenv("TO_SPAWN_LOG_RUECKFALL", str(tmp_path))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    args = argparse.Namespace(befehl="nutzer-antwort", ticket="7", frage_id=fid, wahl="neu", grund="ok", beleg=[])
    assert briefkasten_cli.lauf(args) == 0
    aus = capsys.readouterr().out
    assert "Hinweis" in aus and ".kaputt-" in aus and str(ordner) in aus


# --- 9: Entscheidungs-Liste ohne Manifest --------------------------------------------


def test_entscheidungen_ohne_manifest_zeigt_briefkasten_tickets(briefkasten: ModuleType, tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    fid = briefkasten.einwerfen("41", "Ordner?", "neu", 3, [], hauptbaum=tmp_path)
    briefkasten.antworten("41", fid, "neu", ["Lastenheft"], "passt", hauptbaum=tmp_path)
    assert briefkasten.alle_tickets(hauptbaum=tmp_path) == ["41"]
    lauf = _cli(tmp_path, "entscheidungen", "999")
    assert lauf.returncode == 0, lauf.stderr
    assert "#41 · aufseher · Ordner? → neu" in lauf.stdout


# --- 6: Antwort des Nutzers per Ticket-Kommentar -------------------------------------

REPO_URL = "https://github.com/Shavy72/claude-skill-to-spawn"


def _welt_kommentar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **aenderung: object) -> str:
    """Git-Repo mit GitHub-origin + gh-Ersatz, der die aufgezeichnete Kommentar-Form liefert."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "remote", "add", "origin", f"{REPO_URL}.git"], cwd=tmp_path, check=True)
    kommentar = json.loads((HILFEN / "gh_kommentar_278.json").read_text(encoding="utf-8"))
    kommentar.update(aenderung)
    datei = tmp_path / "kommentar.json"
    datei.write_text(json.dumps(kommentar), encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(HILFEN / "gh_kommentar_278.py"))
    monkeypatch.setenv("GH_KOMMENTAR_DATEI", str(datei))
    return str(kommentar["html_url"])


def test_uebertragen_nimmt_wahl_aus_besitzer_kommentar(
    briefkasten: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = briefkasten.einwerfen("278", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    url = _welt_kommentar(tmp_path, monkeypatch, body=f"Antwort {fid}: ja, aber erst Montag")
    briefkasten.nutzer_antwort_uebertragen("278", fid, url, hauptbaum=tmp_path)
    antwort = briefkasten.lesen("278", fid, hauptbaum=tmp_path)
    assert isinstance(antwort, briefkasten.Antwort)
    assert (antwort.von, antwort.wahl, antwort.belege) == ("nutzer", "ja, aber erst Montag", [url])


@pytest.mark.parametrize(
    "aenderung",
    [
        {"user": {"login": "fremd"}},
        {"author_association": "CONTRIBUTOR"},
        {"issue_url": "https://api.github.com/repos/Shavy72/claude-skill-to-spawn/issues/1"},
        {"body": "Antwort ffffffff: ja"},
        {"body": "ja"},
    ],
)
def test_uebertragen_ohne_pruefbaren_kommentar_verweigert(
    briefkasten: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, aenderung: dict[str, object]
) -> None:
    fid = briefkasten.einwerfen("278", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    aenderung.setdefault("body", f"Antwort {fid}: ja")
    url = _welt_kommentar(tmp_path, monkeypatch, **aenderung)
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.nutzer_antwort_uebertragen("278", fid, url, hauptbaum=tmp_path)
    assert briefkasten.lesen("278", fid, hauptbaum=tmp_path) == briefkasten.NUTZER


def test_uebertragen_fremdes_repo_oder_kaputte_url_verweigert(
    briefkasten: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = briefkasten.einwerfen("278", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    url = _welt_kommentar(tmp_path, monkeypatch, body=f"Antwort {fid}: ja")
    for falsch in (url.replace("claude-skill-to-spawn", "anderes"), url.replace("/278#", "/277#"), "kein-link"):
        with pytest.raises((briefkasten.Verweigert, ValueError)):
            briefkasten.nutzer_antwort_uebertragen("278", fid, falsch, hauptbaum=tmp_path)
    assert briefkasten.lesen("278", fid, hauptbaum=tmp_path) == briefkasten.NUTZER


def test_uebertragen_nie_aus_bau_session(
    briefkasten: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = briefkasten.einwerfen("278", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    url = _welt_kommentar(tmp_path, monkeypatch, body=f"Antwort {fid}: ja")
    monkeypatch.setenv("TO_SPAWN_TICKET", "278")
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.nutzer_antwort_uebertragen("278", fid, url, hauptbaum=tmp_path)
