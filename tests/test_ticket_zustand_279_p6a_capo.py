"""#279 P6a: capo — Schließgrund in beiden Formen, kaputte Sub-Issue-Einträge benannt, Folge-Runde.

Eigene Datei, weil die capo-Welt (``test_waechter_213.welt``) und die Handoff-Welt denselben
Fixture-Namen tragen.
"""

# ruff: noqa: F811 — Fixtures ``welt``/``tmux`` kommen aus Nachbar-Tests und werden als Parameter genannt.
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from test_capo_folgerunde_284 import _starts, _wieder_oeffnen_lassen, tmux  # noqa: F401
from test_waechter_213 import _capo, _gh_setzen, _iso, _text, welt  # noqa: F401

from to_spawn import capo, gh, ticket_zustand


def test_capo_zu_als_nennt_schliessgrund(welt: dict[str, Path]) -> None:
    """Schließgrund kommt aus ``ticket_zustand.schliessgrund`` (beide Formen: Unit-Test im P6a-Haupttest)."""
    _gh_setzen(welt, "901", state="open", closed_at=None)
    assert _capo(welt).returncode == 0  # Ausgangsstand
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(), state_reason="not_planned")
    ergebnis = _capo(welt)
    assert "#901 zu als not_planned — keine Regeln" in ergebnis.stdout, _text(ergebnis)


def test_capo_folge_runde_fertig_gebaut_meldet_wortlaut(
    welt: dict[str, Path], tmux: dict[str, Path]
) -> None:
    """``jetzt_offen``: Stand nach dem Wieder-Öffnen mit Staging-Label → keine Folge-Runde, klare Zeile."""
    _wieder_oeffnen_lassen(welt)
    _gh_setzen(welt, "901", labels=[ticket_zustand.STAGING_FERTIG_LABEL])
    ergebnis = _capo(welt)
    assert "#901 wieder geöffnet" in ergebnis.stdout, _text(ergebnis)
    assert (
        "#901 Ticket fertig gebaut, wartet auf Live — keine Folge-Runde"
        in ergebnis.stdout
    ), _text(ergebnis)
    assert _starts(tmux) == []


def test_capo_folge_runde_offen_startet(
    welt: dict[str, Path], tmux: dict[str, Path]
) -> None:
    """Gegenprobe: ohne Staging-Label ist ``jetzt_offen`` offen → Folge-Runde startet."""
    _wieder_oeffnen_lassen(welt)
    ergebnis = _capo(welt)
    assert "#901 Folge-Runde gestartet" in ergebnis.stdout, _text(ergebnis)
    assert "keine Folge-Runde" not in ergebnis.stdout


def test_capo_kinder_benennt_kaputten_eintrag(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Fail-closed bleibt (halbe Liste ließe die Spec fertig aussehen), aber die Meldung nennt den Eintrag."""
    monkeypatch.setattr(
        gh,
        "json_lauf",
        lambda *a, **k: [{"number": 1, "state": "open"}, {"title": "kaputt"}, "Text"],
    )
    with caplog.at_level(logging.WARNING):
        assert capo.kinder("o/r", 5) is None
    meldung = " ".join(r.getMessage() for r in caplog.records)
    assert "Eintrag 2" in meldung and "Eintrag 3" in meldung, meldung
    assert "Eintrag 1" not in meldung
