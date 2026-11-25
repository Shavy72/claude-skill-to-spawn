"""#279 P6a: letzte zweite Wege zum Ticket-Zustand laufen über :mod:`to_spawn.ticket_zustand`.

Je Punkt ein Test: Leiter und Handoff-Blick lösen fertig gebaute Tickets nicht ab (#288),
``gh`` trägt kein Label-Wissen mehr, Aufpasser und Befund lesen über den Baustein,
capo meldet beide Schließgrund-Formen und benennt kaputte Sub-Issue-Einträge.
"""

# ruff: noqa: F811 — Fixtures kommen aus den Nachbar-Tests und werden als Parameter genannt.
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

import test_aufseher_stand_430 as stand_test
from test_eigener_handoff_542 import JETZT, MINUTE, SPEC, TICKET, _handoff, welt  # noqa: F401
from test_handoff_blick_542 import STILL_BILD, Aussen, ProbeNaht, _manifest
from test_leiter_fix1_432 import _stand_repo

from to_spawn import (
    aufpasser,
    aufseher_stand,
    befund,
    gh,
    handoff_blick,
    leiter,
    ticket_zustand,
)

FERTIG = [{"name": ticket_zustand.STAGING_FERTIG_LABEL}]


# (1) Leiter + Handoff-Blick: fertig gebaut braucht keine Ablösung (#288) ----------------


def test_leiter_fertig_gebautes_stilles_fenster_wird_beendet_nicht_abgeloest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Echte Lage aus den Sub-Issues: grün auf Staging + still → ``/exit``, kein Respawn."""
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    repo = _stand_repo(tmp_path, monkeypatch)
    w = stand_test.FakeWelt()
    w.ticket(TICKET)
    w.tickets[TICKET]["labels"] = FERTIG
    w.fenster[TICKET] = (
        stand_test.JETZT - 60 * MINUTE,
        stand_test.bildschirm("✻ Baked for 40s"),
    )
    u = leiter._EchteUmwelt(repo, w.quellen(repo))
    lage = u.lage(stand_test.SPEC, TICKET, None)
    assert lage.stufe == "fertig_gebaut"
    schritt = leiter.entscheide(lage, leiter.Gemerkt(0, None), stand_test.JETZT, 250.0)
    assert schritt.aktion == "exit", schritt
    assert "fertig gebaut" in schritt.grund


@pytest.mark.parametrize("stufe", ["fertig_gebaut", "zu", "verworfen"])
def test_leiter_nicht_offen_heisst_exit(stufe: ticket_zustand.Stufe) -> None:
    lage = leiter.Lage(
        stufe=stufe,
        fenster=aufseher_stand.STILL,
        still_min=60,
        kontext_k=400.0,
        prompt_datei=False,
    )
    assert leiter.entscheide(lage, leiter.Gemerkt(0, None), 0.0, 250.0).aktion == "exit"


def test_leiter_unbekannter_zustand_beendet_nichts() -> None:
    """GitHub unlesbar: nie ``/exit`` auf Verdacht."""
    lage = leiter.Lage(
        stufe=None,
        fenster=aufseher_stand.STILL,
        still_min=60,
        kontext_k=None,
        prompt_datei=False,
    )
    assert leiter.entscheide(lage, leiter.Gemerkt(0, None), 0.0, 250.0).aktion != "exit"


class _FertigAussen(Aussen):
    def kinder(self, spec: int) -> list[dict[str, Any]]:
        self.gh_aufrufe += 1
        return [{"number": TICKET, "state": "open", "comments": 0, "labels": FERTIG}]


def test_handoff_blick_fertig_gebaut_startet_keine_leiter(
    welt: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(aufseher_stand.STAND_ENV, str(tmp_path / "stand"))
    _manifest(welt["haupt"])
    aussen = _FertigAussen()
    aussen.bild = STILL_BILD
    naht = ProbeNaht(welt["haupt"], aussen)
    _handoff(welt["wt"], JETZT - 5 * MINUTE)
    blick = handoff_blick.Blick(SPEC, naht)
    assert blick.einmal(JETZT) == handoff_blick.STANDARD_S
    assert aussen.gh_aufrufe == 1  # fällig → Lage gefragt
    assert naht.leiter_aufrufe == []


# (2) gh trägt kein Label-Wissen mehr ---------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["staging_fertig", "STAGING_FERTIG_LABEL", "wartet_auf_live", "ticket_offen"],
)
def test_gh_hat_keinen_zweiten_weg_zum_ticket_zustand(name: str) -> None:
    assert not hasattr(gh, name)


# (3) Aufpasser liest über ticket_zustand.lesen -----------------------------------------


def _wache(tmp_path: Path) -> tuple[aufpasser.Aufpasser, list[tuple]]:
    wache = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "zustand"))
    aufrufe: list[tuple] = []
    wache.sh = lambda *a, **k: aufrufe.append(a) or ""  # type: ignore[method-assign]
    return wache, aufrufe


def test_aufpasser_ticket_lesen_geht_ueber_den_baustein(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gefragt: list[tuple[int | str, Path | None]] = []

    def lesen(ticket: int | str, cwd: Path | None = None) -> dict[str, Any]:
        gefragt.append((ticket, cwd))
        return {
            "state": "OPEN",
            "stateReason": None,
            "labels": [{"name": "stop"}, *FERTIG],
        }

    monkeypatch.setattr(ticket_zustand, "lesen", lesen)
    wache, aufrufe = _wache(tmp_path)
    daten = wache.ticket_lesen(7, tmp_path)
    assert gefragt == [(7, tmp_path)]
    assert aufrufe == []  # kein eigener gh-Aufruf mehr
    assert daten["labels"] == {"stop", ticket_zustand.STAGING_FERTIG_LABEL}
    assert ticket_zustand.stufe(daten) == "fertig_gebaut"


def test_aufpasser_ticket_lesen_unlesbar_ist_fehler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ticket_zustand, "lesen", lambda *a, **k: None)
    wache, _ = _wache(tmp_path)
    with pytest.raises(RuntimeError, match="#7"):
        wache.ticket_lesen(7, tmp_path)


# (4) capo: siehe ``test_ticket_zustand_279_p6a_capo.py`` (eigene capo-Welt).


# (5) stufe: Schließgrund mit Leerzeichen, ``None`` fail-closed --------------------------


@pytest.mark.parametrize(
    "ticket",
    [
        {"state": "closed", "state_reason": " not_planned "},
        {"state": "CLOSED", "stateReason": "DUPLICATE\n"},
    ],
)
def test_stufe_schliessgrund_mit_leerzeichen(ticket: dict[str, Any]) -> None:
    assert ticket_zustand.stufe(ticket) == "verworfen"


def test_stufe_none_ist_offen_ohne_absturz() -> None:
    assert ticket_zustand.stufe(None) == "offen"
    assert ticket_zustand.fertig_gebaut(None) is False
    assert ticket_zustand.endgueltig_zu(None) is False


@pytest.mark.parametrize(
    ("ticket", "grund"),
    [
        ({"state": "closed", "state_reason": "COMPLETED "}, "completed"),
        ({"state": "CLOSED", "stateReason": "NOT_PLANNED"}, "not_planned"),
        ({"state": "closed"}, ""),
        (None, ""),
    ],
)
def test_schliessgrund_beide_formen(ticket: dict[str, Any] | None, grund: str) -> None:
    assert ticket_zustand.schliessgrund(ticket) == grund


# (6) Befund: „verschoben“ auch bei Label als Text ---------------------------------------


@pytest.mark.parametrize(
    "labels",
    [
        [{"name": befund.LABEL_VERSCHOBEN}],
        [befund.LABEL_VERSCHOBEN],
        {"nodes": [{"name": befund.LABEL_VERSCHOBEN}]},
    ],
)
def test_befund_verschoben_in_jeder_label_form(labels: object) -> None:
    assert befund._status({"state": "OPEN", "labels": labels}) == "verschoben"
