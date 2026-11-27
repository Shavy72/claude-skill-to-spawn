"""Eine Entscheidungs-Liste je Spec (#364, Lastenheft L9 A52).

``capo.uebersicht()`` schreibt ``entscheidungen_<S>.md`` aus ``briefkasten.liste()``:
Bau-Log-Entscheidungen + Briefkasten-Antworten des Aufsehers, Spalten Ticket · Was ·
Warum · Quelle. ``entscheidung_anhaengen()`` schreibt dasselbe Zeilenformat.
Ohne gh, ohne git-Ref (tmp_path).
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from to_spawn import bau_log, briefkasten, capo

KOPF = "| Ticket | Was | Warum | Quelle |"


def _zeilen(datei: Path) -> list[str]:
    return [z for z in datei.read_text(encoding="utf-8").splitlines() if z.startswith("| #")]


def _antwort(repo: Path, belege: list[str], grund: str = "Blocker sind zu") -> None:
    fid = briefkasten.einwerfen("7", "Blocker erledigt?", "weiterbauen", 3, [], hauptbaum=repo)
    briefkasten.antworten("7", fid, "weiterbauen", belege, grund, hauptbaum=repo)


def test_briefkasten_antwort_steht_in_der_datei(tmp_path: Path) -> None:
    _antwort(tmp_path, ["Lastenheft A46"])
    datei = capo.uebersicht(tmp_path, 3, [7])
    assert _zeilen(datei) == ["| #7 | Blocker erledigt? → weiterbauen | Blocker sind zu | Lastenheft A46 |"]


def test_spalten_und_bau_log_quelle(tmp_path: Path) -> None:
    bau_log.schreibe(
        "entscheidung",
        repo=tmp_path,
        ticket=7,
        wer="bau",
        was="SQLite",
        session="s-7",
        commit="-",
        grund="schon da",
        kontextstand=0,
        frage="Welche Tabelle",
        wahl="SQLite",
    )
    text = capo.uebersicht(tmp_path, 3, [7]).read_text(encoding="utf-8")
    assert KOPF in text
    (zeile,) = [z for z in text.splitlines() if z.startswith("| #7")]
    assert zeile.startswith("| #7 | Welche Tabelle → SQLite | schon da | Bau-Log #7 ")


def test_leere_quelle_sichtbar(tmp_path: Path) -> None:
    fid = briefkasten.einwerfen("7", "Blocker erledigt?", "weiterbauen", 3, [], hauptbaum=tmp_path)
    briefkasten.nutzer_antworten("7", fid, "weiterbauen", [], "Blocker sind zu", hauptbaum=tmp_path)
    (zeile,) = _zeilen(capo.uebersicht(tmp_path, 3, [7]))
    assert zeile.endswith("| keine Quelle |")


def test_umbruch_und_strich_entschaerft(tmp_path: Path) -> None:
    _antwort(tmp_path, ["a|b"], grund="Zeile 1\nZeile 2 | Rest")
    (zeile,) = _zeilen(capo.uebersicht(tmp_path, 3, [7]))
    assert zeile == "| #7 | Blocker erledigt? → weiterbauen | Zeile 1 Zeile 2 / Rest | a/b |"


def test_anhaengen_und_abschluss_gleiches_format(tmp_path: Path) -> None:
    wann = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
    lauf = capo.entscheidung_anhaengen(tmp_path, 3, 7, "Frage?", "ja", "Doktrin", wann)
    angehaengt = lauf.read_text(encoding="utf-8")
    assert KOPF in angehaengt
    (zwischen,) = _zeilen(lauf)
    assert zwischen.startswith("| #7 | Frage? → ja | Doktrin | ")

    _antwort(tmp_path, ["Lastenheft A46"])
    capo.entscheidung_anhaengen(tmp_path, 3, 7, "Zweite?", "nein", "", wann)
    zwei = _zeilen(lauf)
    assert len(zwei) == 2 and zwei[1].startswith("| #7 | Zweite? → nein | ")

    abschluss = capo.uebersicht(tmp_path, 3, [7])
    (spalten,) = {z.count("|") for z in _zeilen(abschluss) + zwei}
    assert spalten == KOPF.count("|")


def test_lange_frage_behaelt_die_wahl(tmp_path: Path) -> None:
    frage = "Welche Tabelle nehmen wir " + "sehr " * 40 + "lange?"
    bau_log.schreibe(
        "entscheidung",
        repo=tmp_path,
        ticket=7,
        wer="bau",
        was="SQLite",
        session="s-7",
        commit="-",
        grund="schon da",
        kontextstand=0,
        frage=frage,
        wahl="SQLite",
    )
    (zeile,) = _zeilen(capo.uebersicht(tmp_path, 3, [7]))
    was = zeile.split(" | ")[1]
    assert was.endswith("… → SQLite"), was


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True, capture_output=True
    )


def test_liste_liest_auch_den_hauptzweig_auf_origin(tmp_path: Path) -> None:
    """Hauptbaum hinter ``origin/main``: die Entscheidung von dort fehlt nicht (#364)."""
    _git(tmp_path, "init", "-q", "-b", "main")
    log_datei = tmp_path / bau_log.log_im_repo(tmp_path, 7)
    log_datei.parent.mkdir(parents=True, exist_ok=True)
    zeile = {
        "typ": "entscheidung",
        "ts": "2026-11-23T10:00:00+00:00",
        "frage": "Ordner?",
        "wahl": "neu",
        "grund": "frei",
    }
    log_datei.write_text(json.dumps(zeile, ensure_ascii=False) + "\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "log")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(tmp_path, "rm", "-q", str(log_datei))
    _git(tmp_path, "commit", "-q", "-m", "Hauptbaum ohne Log")

    erwartet = ["Ordner? → neu"]
    assert [e["was"] for e in briefkasten.liste(["7"], hauptbaum=tmp_path)] == erwartet
    (zeile_md,) = _zeilen(capo.uebersicht(tmp_path, 3, [7]))
    assert zeile_md.startswith("| #7 | Ordner? → neu | frei | Bau-Log #7 ")


def test_kopfzeile_nennt_den_gelesenen_stand(tmp_path: Path) -> None:
    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)

    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    git("update-ref", "refs/remotes/origin/master", "HEAD")
    text = capo.uebersicht(tmp_path, 3, [7]).read_text(encoding="utf-8")
    assert "Bau-Logs auf origin/master + Briefkasten" in text
