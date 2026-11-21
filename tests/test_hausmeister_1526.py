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
