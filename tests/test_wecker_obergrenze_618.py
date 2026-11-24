"""#618: Wecker-Obergrenze, Zähler-Rücksetzen, Aufpasser-Briefkasten-Weg, Commit-Suche-Warnung."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import pytest
import test_briefkasten_wecker_414 as basis
from test_aufpasser_236_fix import _fenster, _still_seit
from test_aufpasser_236 import Welt, pane_text
from test_aufpasser_236 import welt as aufpasser_welt  # noqa: F401 — Fixture
from test_briefkasten_wecker_414 import TICKET, _bild, _getippt
from test_frage_am_prompt_wecker_485 import _sagt, _wecker

from to_spawn import aufpasser, briefkasten, gh

welt = basis.welt


def _frage(n: int) -> str:
    return f"Variante {n} gebaut. Soll ich Zweig {n} mergen? Ja/Nein."


def test_obergrenze_vier_wechselnde_fragebilder_hoechstens_stupser_max(welt: dict[str, Any]) -> None:
    w = _wecker(welt)
    for n in range(aufpasser.STUPSER_MAX + 1):
        _bild(welt, _sagt(_frage(n)))
        w.einmal()
        _bild(welt, _sagt("Ticket geschlossen, Fenster wird beendet."))
        w.einmal()
    assert len(_getippt(welt)) == aufpasser.STUPSER_MAX


def test_zaehler_zurueck_nach_arbeit_wieder_anstoss(welt: dict[str, Any]) -> None:
    w = _wecker(welt)
    w._prompt_stupser[TICKET] = aufpasser.STUPSER_MAX
    _bild(welt, _sagt(_frage(1)))
    w.einmal()
    assert _getippt(welt) == []  # Obergrenze hält
    _bild(welt, "● Arbeite...\n\n  esc to interrupt\n")
    w.einmal()  # Session hat gearbeitet: Zähler neu
    _bild(welt, _sagt(_frage(2)))
    w.einmal()
    assert len(_getippt(welt)) == 1


def test_aufpasser_offene_briefkasten_frage_kein_fuenf_minuten_anstoss(aufpasser_welt: Welt) -> None:
    w = aufpasser_welt
    briefkasten.einwerfen(9002, "Zweig A oder B?", "A", None, ["Lastenheft"], hauptbaum=w.repo)
    _fenster(w, "bau 9002", "printf 'Vorschlag: Review starten? Ja/Nein.\\n'; sleep 3600")
    _still_seit(w, "bau 9002", 10, stufe=0)
    assert w.lauf() == 0
    time.sleep(1)
    assert "Aufpasser: Du stehst" not in pane_text("bau 9002")
    assert w.eintrag("bau 9002").get("stufe", 0) == 0


def test_commit_suche_warnung_je_ticket_nur_einmal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    monkeypatch.setattr(gh, "_git_ausgabe", lambda *a, **k: (128, ""))
    gh._COMMIT_SUCHE_GEMELDET.clear()
    with caplog.at_level(logging.WARNING, logger="to_spawn.gh"):
        for _ in range(3):
            assert gh.commit_betreffe(tmp_path, 618, "origin/main") is None
        assert gh.commit_betreffe(tmp_path, 619, "origin/main") is None
    warnungen = [r for r in caplog.records if "Commit-Suche" in r.getMessage()]
    assert len(warnungen) == 2  # je Ticket eine
