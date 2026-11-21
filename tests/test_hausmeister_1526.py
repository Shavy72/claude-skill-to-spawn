"""Hausmeister #1526: Ticket-Nummer in Ordnernamen wie ``wt-1292f``, ``t1490a``, ``1331-linux``.

Die Nummer zählt nur am Namensanfang, direkt nach ``wt-``/``skill-``/``duoplus-`` oder nach
einem einzelnen Buchstaben — nie als Zahl hinter einem Wort (``schlange-einspielen-1121`` ist
ein Datum, #1121 ist zu: der aktive Ordner würde sonst gesichert und gelöscht).
Weg-Tests mit echtem Wegwerf-Git (Fixture ``welt`` aus #1368).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import pytest
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    Welt,
    git,
    welt,
)

from to_spawn import hausmeister


@pytest.mark.parametrize(
    ("name", "nr"),
    [
        ("wt-1292f", 1292),
        ("t1490a", 1490),
        ("1331-linux", 1331),
        ("1489", 1489),
        ("a1279", 1279),
        ("skill-1371-basis", 1371),
        ("wt-1302-a", 1302),
        ("skill-993675b", None),
        ("skill-install", None),
        ("schlange-einspielen-1121", None),
        ("schlange-einspielen", None),
        ("wt-aufraeumen-2026", None),
        ("sperre-9221", None),
        ("wt-abschluss-1248", None),
        ("skill-hm-sammel", None),
    ],
)
def test_nummer_aus_ordnername(name: str, nr: int | None) -> None:
    assert hausmeister._nummer(name, hausmeister._NAME_MUSTER) == nr


@pytest.mark.parametrize(
    ("zweig", "nr"),
    [
        ("fix-1292", 1292),
        ("aufseher/1279-abschluss", 1279),
        ("abschluss-1248", 1248),
        ("ticket/1525-hausmeister", 1525),
        ("master", None),
        ("main", None),
    ],
)
def test_nummer_aus_zweig(zweig: str, nr: int | None) -> None:
    assert hausmeister._nummer(zweig, (hausmeister._ZWEIG_MUSTER,)) == nr


def _worktree(welt: Welt, name: str, zweig: str) -> object:
    wt = welt.heim / "wt" / name
    git(welt.haupt, "worktree", "add", "-q", "-b", zweig, str(wt))
    git(wt, "push", "-q", "-u", "origin", zweig)
    return wt


def test_name_mit_buchstaben_und_fix_zweig_wird_geloescht(welt: Welt) -> None:
    wt = _worktree(welt, "wt-1292f", "fix-1292")
    welt.ticket(1292, "CLOSED", 48)
    r = welt.aufraeumen(wt)
    assert r.art == "gelöscht" and r.ticket == 1292, r.grund


def test_name_und_zweig_nennen_verschiedene_nummern_bleibt(welt: Welt) -> None:
    wt = _worktree(welt, "t1490a", "aufseher/1491-abschluss")
    welt.ticket(1490, "CLOSED", 48)
    welt.ticket(1491, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "widersprechen" in r.grund, r.grund
    assert wt.is_dir()


def test_datum_hinter_wort_ist_kein_ticket(welt: Welt) -> None:
    wt = _worktree(welt, "schlange-einspielen-1121", "einspielen")
    welt.ticket(1121, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "keine Ticket-Nummer" in r.grund, r.grund
    assert wt.is_dir()


@pytest.mark.parametrize(
    "name",
    [
        "skill-4821f3c",  # Commit-Hash-Rest
        "skill-123abcd",
        "2026-11-21-sicherung",  # Datum
        "v2026-backup",
        "8080-notizen",  # freie Zahl
        "x500",
        "805_fremd_backup",  # echter Ordner auf dem Bau-Server
    ],
)
def test_keine_nummer_aus_hash_datum_freier_zahl(name: str) -> None:
    assert hausmeister._nummer(name, hausmeister._NAME_MUSTER) is None


@pytest.mark.parametrize(
    ("name", "nr"),
    [
        ("skill-1426a", 1426),
        ("1342-lauf", 1342),
        ("t1490b", 1490),
        ("wt-1293-push", 1293),
    ],
)
def test_echte_ordnernamen_bleiben_lesbar(name: str, nr: int) -> None:
    assert hausmeister._nummer(name, hausmeister._NAME_MUSTER) == nr


@pytest.mark.parametrize(
    "name",
    [
        "wt-2026-11-21",  # Datum hinter wt- (Review 5957c7a)
        "wt-2026-11-21-sicherung",
        "wt-8080-notizen",  # freie Zahl hinter wt-
        "skill-20261121",  # Datum ohne Trenner
    ],
)
def test_keine_nummer_aus_datum_oder_freier_zahl_hinter_praefix(name: str) -> None:
    assert hausmeister._nummer(name, hausmeister._NAME_MUSTER) is None


@pytest.mark.parametrize(
    ("name", "nr"),
    [
        ("wt-1375", 1375),
        ("wt-1375-7c", 1375),
        ("wt-1313-3", 1313),
        ("wt-627-waechter", 627),
        ("duoplus-1307", 1307),
    ],
)
def test_echte_wt_ordner_mit_kurzem_zusatz_bleiben_lesbar(name: str, nr: int) -> None:
    assert hausmeister._nummer(name, hausmeister._NAME_MUSTER) == nr


@pytest.mark.parametrize(
    "pfad", ["scripts/x.pyc", "foo__pycache__/x.pyc", "sub/data/.secret_key"]
)
def test_grenzfaelle_gelten_nicht_als_neu_erzeugbar(pfad: str) -> None:
    assert not hausmeister._neu_erzeugbar(pfad)


@pytest.mark.parametrize(
    "name",
    [
        "1",  # nackt nur 3–4 Ziffern (Fix-Runde 3)
        "12",
        "99",
        "1-linux",
        "a7",  # t/a nur mit 3–4 Ziffern
        "wt-2026-11",  # Jahr-Monat
        "wt-2025-12",
        "wt-5000",  # über der Obergrenze
        "wt-0",
        "wt-01",
    ],
)
def test_keine_nummer_aus_kurzer_zahl_oder_jahr_monat(name: str) -> None:
    assert hausmeister._nummer(name, hausmeister._NAME_MUSTER) is None


@pytest.mark.parametrize(
    ("name", "nr"),
    [
        ("duoplus-39", 39),
        ("wt-81", 81),
        ("wt-4999", 4999),
        ("wt-1313-3", 1313),
        ("wt-1375-7c", 1375),
    ],
)
def test_kurze_und_grenz_nummern_hinter_praefix_bleiben_lesbar(name: str, nr: int) -> None:
    assert hausmeister._nummer(name, hausmeister._NAME_MUSTER) == nr


@pytest.mark.parametrize("pfad", ["__pycache__/x.pyc", "data/.secret_key"])
def test_kompilat_und_sitzungsschluessel_gelten_als_neu_erzeugbar(pfad: str) -> None:
    assert hausmeister._neu_erzeugbar(pfad)
