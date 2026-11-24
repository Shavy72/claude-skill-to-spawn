"""#459 Soll 2: Still am Prompt, Zweig gemergt, Ticket offen -> Anstoß im nächsten Wecker-Takt.

Gestellt sind nur tmux und die externen Abfragen (GitHub-Ticket, origin-Commits); Wecker,
Regel (:func:`aufpasser.steht_umsonst`) und Briefkasten sind echt.
"""

from __future__ import annotations

from typing import Any

import pytest
import test_briefkasten_wecker_414 as basis
from test_briefkasten_wecker_414 import SPEC, TICKET, _bild, _getippt
from test_frage_am_prompt_wecker_485 import ANSTOSS, BAU, _sagt, _wecker

from to_spawn import aufpasser, briefkasten, ticket_zustand

welt = basis.welt


@pytest.fixture
def gemergt(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    stand: dict[str, Any] = {"labels": [], "state": "OPEN"}
    monkeypatch.setattr(aufpasser.gh, "commit_betreffe", lambda *a, **k: [f"feat: Probe (#{TICKET})"])
    monkeypatch.setattr(aufpasser.gh, "hauptzweig", lambda *a, **k: "main")
    monkeypatch.setattr(ticket_zustand, "lesen", lambda *a, **k: dict(stand))
    return stand


AUSSAGE = "Pull-Request ist gemergt. Ich bin fertig."


def test_gemergt_still_offen_anstoss_im_naechsten_takt_genau_einmal(welt: dict[str, Any], gemergt: dict[str, Any]) -> None:
    _bild(welt, _sagt(AUSSAGE))
    w = _wecker(welt)
    w.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [BAU], getippt
    assert getippt[0]["text"].startswith(ANSTOSS)
    w.einmal()
    assert len(_getippt(welt)) == 1


def test_gemergt_aber_nicht_auf_origin_kein_anstoss(
    welt: dict[str, Any], gemergt: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(aufpasser.gh, "commit_betreffe", lambda *a, **k: [])
    _bild(welt, _sagt(AUSSAGE))
    _wecker(welt).einmal()
    assert _getippt(welt) == []


@pytest.mark.parametrize("stand", [{"state": "CLOSED"}, {"labels": [{"name": "checkpoint:human"}]}, {"labels": [{"name": "staging:fertig"}]}])
def test_gemergt_geschlossen_checkpoint_oder_staging_kein_anstoss(
    welt: dict[str, Any], gemergt: dict[str, Any], stand: dict[str, Any]
) -> None:
    gemergt.update(stand)
    _bild(welt, _sagt(AUSSAGE))
    _wecker(welt).einmal()
    assert _getippt(welt) == []


def test_gemergt_offene_briefkasten_frage_kein_anstoss(welt: dict[str, Any], gemergt: dict[str, Any]) -> None:
    briefkasten.einwerfen(TICKET, "Zweig A oder B?", "A", None, ["Lastenheft"], hauptbaum=welt["haupt"])
    _bild(welt, _sagt(AUSSAGE))
    _wecker(welt).einmal()
    assert _getippt(welt) == []


def test_gemergt_live_ja_aufforderung_kein_anstoss(welt: dict[str, Any], gemergt: dict[str, Any]) -> None:
    _bild(welt, _sagt(f"Gemergt. Live braucht dein Ja: `{briefkasten.JA_BEFEHL} {TICKET}`"))
    _wecker(welt).einmal()
    assert _getippt(welt) == []


def test_ticket_stand_unlesbar_kein_anstoss(welt: dict[str, Any], gemergt: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ticket_zustand, "lesen", lambda *a, **k: None)
    _bild(welt, _sagt(AUSSAGE))
    _wecker(welt).einmal()
    assert _getippt(welt) == []
