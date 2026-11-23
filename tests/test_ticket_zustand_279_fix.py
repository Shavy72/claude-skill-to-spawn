"""#279 P1, Fix-Runde nach Review.

(1) Der Aufpasser-Takt fragt keinen ``gh issue view`` je offenem Ticket mehr: Zustand und
    Labels kommen aus dem einen ``sub_issues``-Listen-Aufruf (ein hängendes ``gh`` je
    Ticket blockierte sonst den ganzen Takt).
(2) Die Meldung beim Schließen nennt die Stufe — „fertig gebaut“ ist nicht „zu“.

Welt wie in ``test_ticket_zustand_279`` (echtes tmux/git, gestellt nur ``gh``).
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from test_aufpasser_236 import (  # noqa: F401 — ``welt`` ist ein Fixture
    SPEC,
    Welt,
    fenster_namen,
    welt,
)
from test_ticket_zustand_279 import _labels

from to_spawn import ticket_zustand


def test_takt_ohne_issue_view_je_offenem_ticket(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Befund 1: Start-Entscheidung aus der Liste — kein ``issue view`` je offenem Ticket."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002|9003")
    _labels(welt, monkeypatch, {"9002": ["staging:fertig"]})
    assert welt.lauf(hang_min=90) == 0
    time.sleep(1)
    assert sorted(fenster_namen()) == ["bau 9003", f"wache {SPEC}"]
    aufrufe = welt.protokoll_gh.read_text(encoding="utf-8").splitlines()
    # Einzig übrig: die Stopp-Label-Probe in ``start_erlaubt`` direkt vor dem einen Start.
    ansichten = [z.split()[2] for z in aufrufe if z.startswith("issue view")]
    assert ansichten == ["9003"], aufrufe


def test_schliessen_meldet_fertig_gebaut_statt_zu(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Befund 2: offen + ``staging:fertig`` → Meldung „fertig gebaut“, nicht „Ticket zu“."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002")
    _labels(welt, monkeypatch, {"9002": ["staging:fertig"]})
    welt.fenster_still(9002, "sleep 3600")
    welt.stand_setzen("bau 9002", seit=time.time() - 20 * 60, hash=welt.hash_von("bau 9002"))
    assert welt.lauf(hang_min=90) == 0
    time.sleep(0.5)
    kommentare = welt.kommentare()
    assert any("„bau 9002“ geschlossen (Ticket fertig gebaut" in k for k in kommentare), kommentare
    assert not any("(Ticket zu)" in k for k in kommentare), kommentare


@pytest.mark.parametrize(
    ("ticket", "erwartet"),
    [
        ({"state": "OPEN", "labels": ["staging:fertig"]}, "Ticket fertig gebaut, wartet auf Live"),
        ({"state": "CLOSED"}, "Ticket zu"),
        ({"state": "closed", "state_reason": "not_planned"}, "Ticket verworfen"),
        ({"state": "OPEN"}, "Ticket offen"),
    ],
)
def test_wortlaut_je_stufe(ticket: dict[str, Any], erwartet: str) -> None:
    assert ticket_zustand.wortlaut(ticket) == erwartet
