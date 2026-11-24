"""#554: „Ticket zu“ weckt den Aufseher, ein Aufseher-Kommentar die Bau-Session — sofort, nicht im Takt.

Gestellt sind nur ``tmux`` (Attrappe aus ``test_briefkasten_wecker_414``) und die GitHub-Abfrage
(:func:`gh.json_lauf`); die Antwort hat die Form einer echten ``gh api graphql``-Antwort
(aufgezeichnet am Skill-Repo, 24.11.2026). Wecker, Gedächtnis-Datei und tmux-Tür sind echt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import test_briefkasten_wecker_414 as basis
from test_briefkasten_wecker_414 import (
    ARBEITET,
    PANE,
    SPEC,
    STILL,
    TICKET,
    _bild,
    _getippt,
)

from to_spawn import briefkasten_wecker, capo, gh

welt = basis.welt  # Fixture aus dem Grund-Test (Hauptbaum + gestelltes tmux)
ZWEI = TICKET + 1
BAU = f"=spec-{SPEC}:@2"


class GitHub:
    """Gestellte GitHub-Lage: Ticket → (state, Kommentare); zählt die GraphQL-Abfragen."""

    def __init__(self) -> None:
        self.lage: dict[int, dict[str, Any]] = {}
        self.abfragen = 0
        self.kaputt = False

    def issue(self, ticket: int, state: str = "OPEN") -> None:
        self.lage[ticket] = {"state": state, "comments": {"nodes": []}}

    def kommentar(self, ticket: int, kid: int, body: str, rolle: str = "OWNER") -> None:
        self.lage[ticket]["comments"]["nodes"].append({"databaseId": kid, "body": body, "authorAssociation": rolle})

    def json_lauf(self, args: list[str], **_kw: Any) -> Any:
        if args[:2] != ["api", "graphql"]:
            return None  # REST-Abfragen des Abnahme-Blicks (#528) sind hier nicht Thema
        self.abfragen += 1
        if self.kaputt:
            return None
        abfrage = next(a for a in args if a.startswith("query="))
        return {"data": {"repository": {f"t{t}": daten for t, daten in self.lage.items() if f"t{t}:" in abfrage}}}


@pytest.fixture
def github(welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> GitHub:
    g = GitHub()
    g.issue(TICKET)
    monkeypatch.setattr(gh, "json_lauf", g.json_lauf)
    return g


def _wecker(welt: dict[str, Any]) -> briefkasten_wecker.Wecker:
    return briefkasten_wecker.Wecker(SPEC, welt["haupt"], PANE, gh_repo="o/r")


def _gedaechtnis(welt: dict[str, Any]) -> Path:
    return welt["haupt"] / ".to-spawn" / f"wecker-{SPEC}.json"


AUFSEHER = f"{capo.WAECHTER_KOPF} Bitte aufräumen."


def test_erster_durchgang_merkt_bestand_und_weckt_nicht(welt: dict[str, Any], github: GitHub) -> None:
    """Ohne Gedächtnis-Datei: Altes (zu, Aufseher-Kommentar) wird nur gemerkt, nicht geweckt."""
    github.kommentar(TICKET, 11, AUFSEHER)
    _wecker(welt).einmal()
    assert _getippt(welt) == []
    assert json.loads(_gedaechtnis(welt).read_text(encoding="utf-8"))["geweckt"] == ["kommentar:11"]


def test_ticket_zu_weckt_aufseher_einmal(welt: dict[str, Any], github: GitHub) -> None:
    w = _wecker(welt)
    w.einmal()
    github.issue(TICKET, "CLOSED")
    w.einmal()
    w.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [PANE], getippt
    assert f"#{TICKET}" in getippt[0]["text"] and "geschlossen" in getippt[0]["text"]


def test_aufseher_kommentar_weckt_bau_session_einmal(welt: dict[str, Any], github: GitHub) -> None:
    """Nur der Aufseher-Kommentar eines Beteiligten weckt ``bau <N>``; Mensch, Bau-Session, Fremde nicht."""
    w = _wecker(welt)
    w.einmal()
    github.kommentar(TICKET, 21, f"{capo.SESSION_KOPF} fertig")
    github.kommentar(TICKET, 22, "passt so")
    github.kommentar(TICKET, 23, AUFSEHER, rolle="NONE")
    w.einmal()
    assert _getippt(welt) == []
    github.kommentar(TICKET, 24, AUFSEHER)
    w.einmal()
    w.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [BAU], getippt
    assert f"gh issue view {TICKET} --comments" in getippt[0]["text"]


def test_unruhiges_fenster_weckt_spaeter_genau_einmal(welt: dict[str, Any], github: GitHub) -> None:
    """Fenster arbeitet → nichts tippen, nichts merken; danach still → ein Weckruf."""
    w = _wecker(welt)
    w.einmal()
    github.issue(TICKET, "CLOSED")
    _bild(welt, ARBEITET)
    w.einmal()
    assert _getippt(welt) == []
    assert json.loads(_gedaechtnis(welt).read_text(encoding="utf-8"))["geweckt"] == []
    _bild(welt, STILL)
    w.einmal()
    w.einmal()
    assert [g["ziel"] for g in _getippt(welt)] == [PANE]


def test_neustart_weckt_weder_doppelt_noch_gar_nicht(welt: dict[str, Any], github: GitHub) -> None:
    """Gedächtnis überlebt den Neustart: Geweckt bleibt still, das Ereignis in der Pause weckt."""
    w = _wecker(welt)
    w.einmal()
    github.kommentar(TICKET, 31, AUFSEHER)
    w.einmal()
    assert [g["ziel"] for g in _getippt(welt)] == [BAU]
    github.issue(ZWEI, "CLOSED")  # geschieht, während der Aufseher neu startet
    basis_manifest = welt["haupt"] / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    basis_manifest.write_text(json.dumps({"spec": SPEC, "tickets": {str(TICKET): {}, str(ZWEI): {}}}), encoding="utf-8")
    neu = _wecker(welt)
    neu.einmal()
    neu.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [BAU, PANE], getippt
    assert f"#{ZWEI}" in getippt[1]["text"]


def test_github_fehler_weckt_nichts_und_merkt_nichts(welt: dict[str, Any], github: GitHub) -> None:
    w = _wecker(welt)
    w.einmal()
    vorher = _gedaechtnis(welt).read_text(encoding="utf-8")
    github.issue(TICKET, "CLOSED")
    github.kaputt = True
    w.einmal()
    assert _getippt(welt) == []
    assert _gedaechtnis(welt).read_text(encoding="utf-8") == vorher
    github.kaputt = False
    w.einmal()
    assert [g["ziel"] for g in _getippt(welt)] == [PANE]


def test_eine_abfrage_je_durchgang_fuer_alle_tickets(welt: dict[str, Any], github: GitHub) -> None:
    manifest = welt["haupt"] / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    manifest.write_text(json.dumps({"spec": SPEC, "tickets": {str(TICKET): {}, str(ZWEI): {}}}), encoding="utf-8")
    github.issue(ZWEI)
    w = _wecker(welt)
    w.einmal()
    w.einmal()
    assert github.abfragen == 2


def test_ereignis_quelle_ist_eine_reine_funktion() -> None:
    """Schmale Schnittstelle: GitHub-Lage → (Schlüssel, wen, Text); neues Ereignis = ein Zweig dort."""
    lage = {
        "7": {"state": "CLOSED", "comments": {"nodes": []}},
        "8": {
            "state": "OPEN",
            "comments": {"nodes": [{"databaseId": 5, "body": AUFSEHER, "authorAssociation": "OWNER"}]},
        },
        "9": None,  # Issue gelöscht/unlesbar: kein Ereignis, kein Absturz
    }
    ereignisse = briefkasten_wecker.ereignisse(lage)
    assert [(e.schluessel, e.bau) for e in ereignisse] == [("zu:7", None), ("kommentar:5", "8")]
