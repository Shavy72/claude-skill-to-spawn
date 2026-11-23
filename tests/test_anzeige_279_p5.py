"""#279 Paket 5: Anzeige-Stellen zeigen „fertig gebaut“ und „geschlossen“ unterscheidbar.

Quelle der Wahrheit ist ``to_spawn/ticket_zustand.py``; jede Anzeige fragt dort.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
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


def test_leitstand_fertig_gebaut_kein_ende(ls: ModuleType) -> None:
    """Offen + ``staging:fertig``: kein „auf GitHub geschlossen“, kein ``ende`` — auch kein alter Merkwert."""
    gebaut = {"state": "OPEN", "labels": {"nodes": [{"name": LABEL}]}}
    zustand = ls.leerer_zustand()
    zustand["ende"] = {"1": "2026-11-23T10:00:00+01:00"}  # aus der Zeit vor dem Fix
    live, ereignisse, merk = ls.berechne("9", {"1": "Titel"}, {}, {"1": gebaut}, {}, zustand)
    assert live["tickets"]["1"]["zustand"] == "fertig"
    assert "ende" not in live["tickets"]["1"]
    assert "1" not in merk["ende"]
    assert "#1 fertig" not in [e.titel for e in ereignisse]
    zu = {"state": "CLOSED", "closedAt": "2026-11-23T09:00:00Z"}
    live, ereignisse, merk = ls.berechne("9", {"1": "Titel"}, {}, {"1": zu}, {}, ls.leerer_zustand())
    assert "ende" in live["tickets"]["1"] and "1" in merk["ende"]
    assert "#1 fertig" in [e.titel for e in ereignisse]


def _seiten_js(name: str) -> str:
    seite = (SKILL / "leitstand" / "seite.html").read_text(encoding="utf-8")
    anfang = seite.index(f"  function {name}(")
    return seite[anfang : seite.index("\n  }\n", anfang) + 4]


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_leitstand_seite_alles_fertig_nur_wenn_zu() -> None:
    """Phasen-Gesamtzeile: „Alles fertig.“ nur, wenn jedes Ticket wirklich zu ist (``ende`` gesetzt)."""
    js = (
        "let tickets = [];\nconst model = { tiefe: new Map() };\n"
        "const phaseKey = (t) => 1, offeneBlocker = () => [], nrListe = (l) => l.join(', ');\n"
        + _seiten_js("phasenStand")
        + "\nconst t = (nr, ende) => ({ nr, zustand: 'fertig', blocked_by: [], ende });\n"
        "tickets = [t(1, '2026-11-23T10:00:00'), t(2, '2026-11-23T10:05:00')];\n"
        "console.log(phasenStand({ key: 1 }).ueber);\n"
        "tickets = [t(1, '2026-11-23T10:00:00'), t(2, null)];\n"
        "console.log(phasenStand({ key: 1 }).ueber);\n"
    )
    aus = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True, encoding="utf-8")
    assert aus.stdout.splitlines() == ["Alles fertig.", "Fertig gebaut, wartet auf Live."]


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_leitstand_seite_zeigt_phase_auch_bei_fertig() -> None:
    """Knoten im Gleisplan: bei „fertig“ steht die Phase ohne „Gerade:“, sonst mit."""
    js = (
        "const W = 200, FONT_TITEL = 0, FONT_PHASE = 0, umbrechen = (s) => [s];\n"
        + _seiten_js("knotenText")
        + "\nconst ph = (zustand) => knotenText({ titel: 'T', art: 'bau', zustand, phase: 'Ticket fertig gebaut, wartet auf Live' }).ph[0].txt;\n"
        "console.log(ph('fertig'));\nconsole.log(ph('läuft'));\n"
    )
    aus = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True, encoding="utf-8")
    assert aus.stdout.splitlines() == [
        "Ticket fertig gebaut, wartet auf Live",
        "Gerade: Ticket fertig gebaut, wartet auf Live",
    ]


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


def test_belege_fertig_gebaut_nicht_gruen_wie_zu(bu: ModuleType) -> None:
    gebaut = bu.baue_html(9, [bu.Eintrag("7", "Titel", "fertig gebaut", "ABNAHME ok")], "23.11.2026")
    zu = bu.baue_html(9, [bu.Eintrag("7", "Titel", "zu", "ABNAHME ok")], "23.11.2026")
    assert '<span class="marke gruen">Status: zu' in zu
    assert '<span class="marke gruen">Status: fertig gebaut' not in gebaut
