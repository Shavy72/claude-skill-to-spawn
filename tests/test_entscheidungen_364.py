"""Eine Entscheidungs-Liste je Spec (#364, Lastenheft L9 A52).

``capo.uebersicht()`` schreibt ``entscheidungen_<S>.md`` aus ``briefkasten.liste()``:
Bau-Log-Entscheidungen + Briefkasten-Antworten des Aufsehers, Spalten Ticket · Was ·
Warum · Quelle. ``entscheidung_anhaengen()`` schreibt dasselbe Zeilenformat.
Ohne gh, ohne git-Ref (tmp_path).
"""

from __future__ import annotations

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
    datei = capo.uebersicht(tmp_path, None, 3, [7])
    assert _zeilen(datei) == ["| #7 | Blocker erledigt? → weiterbauen | Blocker sind zu | Lastenheft A46 |"]


def test_spalten_und_bau_log_quelle(tmp_path: Path) -> None:
    bau_log.schreibe(tmp_path, 7, "entscheidung", frage="Welche Tabelle", wahl="SQLite", grund="schon da")
    text = capo.uebersicht(tmp_path, None, 3, [7]).read_text(encoding="utf-8")
    assert KOPF in text
    (zeile,) = [z for z in text.splitlines() if z.startswith("| #7")]
    assert zeile.startswith("| #7 | Welche Tabelle → SQLite | schon da | Bau-Log #7 ")


def test_leere_quelle_sichtbar(tmp_path: Path) -> None:
    _antwort(tmp_path, [])
    (zeile,) = _zeilen(capo.uebersicht(tmp_path, None, 3, [7]))
    assert zeile.endswith("| keine Quelle |")


def test_umbruch_und_strich_entschaerft(tmp_path: Path) -> None:
    _antwort(tmp_path, ["a|b"], grund="Zeile 1\nZeile 2 | Rest")
    (zeile,) = _zeilen(capo.uebersicht(tmp_path, None, 3, [7]))
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

    abschluss = capo.uebersicht(tmp_path, None, 3, [7])
    (spalten,) = {z.count("|") for z in _zeilen(abschluss) + zwei}
    assert spalten == KOPF.count("|")
