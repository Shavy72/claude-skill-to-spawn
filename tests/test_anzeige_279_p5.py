"""#279 Paket 5: Anzeige-Stellen zeigen „fertig gebaut“ und „geschlossen“ unterscheidbar.

Quelle der Wahrheit ist ``to_spawn/ticket_zustand.py``; jede Anzeige fragt dort.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[1]
SKRIPTE = SKILL / "skripte"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import aufseher_stand, ticket_zustand, ticketstand  # noqa: E402

LABEL = ticket_zustand.STAGING_FERTIG_LABEL


def _lade(name: str, datei: Path) -> ModuleType:
    if str(SKRIPTE) not in sys.path:
        sys.path.insert(0, str(SKRIPTE))
    spec = importlib.util.spec_from_file_location(name, datei)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture(scope="module")
def ls() -> ModuleType:
    return _lade("leitstand_279_p5", SKRIPTE / "leitstand.py")


@pytest.fixture(scope="module")
def ss() -> ModuleType:
    return _lade("spec_stand_279_p5", SKRIPTE / "spec_stand.py")


@pytest.fixture(scope="module")
def bu() -> ModuleType:
    return _lade("belege_uebersicht_279_p5", SKRIPTE / "belege_uebersicht.py")


# REST-Form (sub_issues, issue view): klein geschrieben, Labels als Objekte.
OFFEN: dict[str, Any] = {"number": 1, "state": "open", "labels": []}
FERTIG_GEBAUT: dict[str, Any] = {
    "number": 2,
    "state": "open",
    "labels": [{"name": LABEL}],
}
ZU: dict[str, Any] = {
    "number": 3,
    "state": "closed",
    "state_reason": "completed",
    "labels": [{"name": LABEL}],
}
VERWORFEN: dict[str, Any] = {
    "number": 4,
    "state": "closed",
    "state_reason": "not_planned",
    "labels": [],
}


# --- ticket_zustand: Anzeige-Wort je Stufe + GraphQL-Form ---------------------


def test_anzeige_wort_je_stufe() -> None:
    assert [ticket_zustand.ANZEIGE[ticket_zustand.stufe(t)] for t in (OFFEN, FERTIG_GEBAUT, ZU, VERWORFEN)] == [
        "offen",
        "fertig gebaut",
        "zu",
        "verworfen",
    ]


def test_graphql_labels_mit_nodes() -> None:
    issue = {"state": "OPEN", "labels": {"nodes": [{"name": LABEL}]}}
    assert ticket_zustand.stufe(issue) == "fertig_gebaut"


# --- Leitstand ----------------------------------------------------------------


def test_leitstand_fertig_gebaut_und_geschlossen_unterscheidbar(ls: ModuleType) -> None:
    leer = ls.leerer_zustand()
    gebaut = {"state": "OPEN", "labels": {"nodes": [{"name": LABEL}]}}
    assert ls.ticket_stand("1", "aus", gebaut, [], [], leer) == (
        "fertig",
        "Ticket fertig gebaut, wartet auf Live",
    )
    assert ls.ticket_stand("1", "läuft", {"state": "CLOSED"}, [], [], leer) == (
        "fertig",
        None,
    )
    verworfen = {"state": "CLOSED", "stateReason": "NOT_PLANNED"}
    assert ls.ticket_stand("1", "aus", verworfen, [], [], leer) == (
        "fertig",
        "Ticket verworfen",
    )
    assert ls.ticket_stand("1", "läuft", {"state": "OPEN", "labels": {"nodes": []}}, [], [], leer)[0] == "läuft"


def test_leitstand_graphql_holt_labels(ls: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gesehen: list[str] = []

    def lauf(argv: list[str], repo: Path) -> tuple[int, str]:
        gesehen.append(argv[-1])
        return 0, '{"data": {"repository": {"i5": {"state": "OPEN"}}}}'

    monkeypatch.setattr(ls, "repo_slug", lambda repo: "o/n")
    monkeypatch.setattr(ls, "gh_lauf", lauf)
    ls.gh_issues(tmp_path, ["5"])
    assert "labels(" in gesehen[0] and "stateReason" in gesehen[0]


def test_leitstand_seite_zeigt_phase_auch_bei_fertig() -> None:
    seite = (SKILL / "leitstand" / "seite.html").read_text(encoding="utf-8")
    assert 'if (t.zustand !== "fertig" && t.phase)' not in seite


# --- Spec-Stand ---------------------------------------------------------------


def test_spec_stand_drei_werte(ss: ModuleType) -> None:
    assert [ss.zustand_text(t) for t in (OFFEN, FERTIG_GEBAUT, ZU, VERWORFEN)] == [
        "offen        ",
        "fertig gebaut",
        "zu           ",
        "verworfen    ",
    ]


# --- Aufseher-Stand -----------------------------------------------------------


def test_aufseher_stand_stufe_statt_bool() -> None:
    assert [aufseher_stand._stufe(t) for t in (OFFEN, FERTIG_GEBAUT, ZU)] == [
        "offen",
        "fertig_gebaut",
        "zu",
    ]
    assert aufseher_stand._stufe({}) is None


def test_aufseher_kurz_und_kopf_zeile() -> None:
    lagen = [
        aufseher_stand.TicketLage(1, "offen", aufseher_stand.ARBEITET),
        aufseher_stand.TicketLage(2, "fertig_gebaut", aufseher_stand.KEIN_FENSTER),
        aufseher_stand.TicketLage(3, "zu", aufseher_stand.KEIN_FENSTER),
        aufseher_stand.TicketLage(4, None, aufseher_stand.KEIN_FENSTER),
    ]
    zeilen = [aufseher_stand.kurz_zeile(x) for x in lagen]
    assert zeilen[0].startswith("#1 offen · ")
    assert zeilen[1].startswith("#2 fertig gebaut · ")
    assert zeilen[2].startswith("#3 zu · ")
    assert zeilen[3].startswith("#4 — · ")
    kopf = aufseher_stand.kopf_zeile(9, lagen, 0.0, False)
    assert "1 offen, 1 fertig gebaut, 1 zu" in kopf


# --- Ticketstand --------------------------------------------------------------


def test_ticketstand_eigene_stufe_fertig_gebaut() -> None:
    lage = aufseher_stand.TicketLage(2, "fertig_gebaut", aufseher_stand.KEIN_FENSTER)
    assert ticketstand.zustand(FERTIG_GEBAUT, lage) == (
        "fertig gebaut",
        "wartet auf Live",
    )
    assert ticketstand.zustand(ZU, None) == ("fertig", "")
    assert ticketstand.zustand(VERWORFEN, None) == ("fertig", "verworfen")
    assert ticketstand.zustand(OFFEN, lage)[0] == "wartet"


def test_ticketstand_kopf_zaehlt_fertig_gebaut() -> None:
    zeilen = [
        ticketstand.Zeile(1, "a", ticketstand.FERTIG),
        ticketstand.Zeile(2, "b", ticketstand.FERTIG_GEBAUT, "wartet auf Live"),
    ]
    kopf = ticketstand.tabelle(9, zeilen, "12:00").splitlines()[0]
    assert "1 von 2 fertig" in kopf and "1 fertig gebaut" in kopf


# --- Belege-Übersicht ---------------------------------------------------------


@pytest.mark.parametrize(
    ("daten", "erwartet"),
    [
        (OFFEN, "offen"),
        (FERTIG_GEBAUT, "fertig gebaut"),
        (ZU, "zu"),
        (VERWORFEN, "verworfen"),
        (None, "unbekannt"),
    ],
)
def test_belege_status_von(
    bu: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daten: dict[str, Any] | None,
    erwartet: str,
) -> None:
    monkeypatch.setattr(ticket_zustand, "lesen", lambda nr, cwd=None: daten)
    assert bu.status_von("7", tmp_path, ohne_github=False) == erwartet
    assert bu.status_von("7", tmp_path, ohne_github=True) == "unbekannt"


def test_belege_html_kennt_fertig_gebaut(bu: ModuleType) -> None:
    eintrag = bu.Eintrag("7", "Titel", "fertig gebaut", "ABNAHME ok")
    seite = bu.baue_html(9, [eintrag], "23.11.2026")
    assert "Status: fertig gebaut" in seite
