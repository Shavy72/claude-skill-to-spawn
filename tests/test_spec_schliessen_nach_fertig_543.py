"""#543: Nach SPEC FERTIG schließt capo das Spec-Issue über den vorhandenen Weg ``befund.spec_schliessen``."""

from __future__ import annotations

import inspect
import logging
from datetime import datetime
from pathlib import Path

import pytest

from to_spawn import befund, capo

JETZT = datetime(2026, 11, 24, 12, 0)


def _lauf(
    monkeypatch: pytest.MonkeyPatch,
    ergebnis: befund.Ergebnis | Exception,
    dry_run: bool = False,
):
    aufrufe: list[int] = []

    def falsch(repo: Path, gh_repo: str, spec: int, abruf=None):
        aufrufe.append(spec)
        if isinstance(ergebnis, Exception):
            raise ergebnis
        return ergebnis

    monkeypatch.setattr(befund, "spec_schliessen", falsch)
    zustand: dict = {}
    zeilen = capo._spec_schliessen_schritt(Path("."), 7, "o/r", zustand, JETZT, dry_run)
    return aufrufe, zustand, zeilen


def test_erfolg_merkt_geschlossen(monkeypatch: pytest.MonkeyPatch) -> None:
    aufrufe, zustand, zeilen = _lauf(
        monkeypatch, befund.Ergebnis(ok=True, zeilen=["Spec #7 geschlossen"])
    )
    assert (
        aufrufe == [7]
        and "spec_geschlossen" in zustand
        and zeilen == ["Spec #7 geschlossen"]
    )


def test_abbruch_ist_kein_fehler_und_wird_wiederholt(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        _, zustand, zeilen = _lauf(
            monkeypatch, befund.Ergebnis(ok=False, zeilen=["Spec #7 bleibt offen"])
        )
    assert "spec_geschlossen" not in zustand and zeilen == []
    assert "bleibt offen" in caplog.text


def test_ausnahme_stuerzt_nicht_ab(monkeypatch: pytest.MonkeyPatch) -> None:
    _, zustand, zeilen = _lauf(monkeypatch, RuntimeError("gh weg"))
    assert "spec_geschlossen" not in zustand and zeilen == []


def test_probelauf_schliesst_nichts(monkeypatch: pytest.MonkeyPatch) -> None:
    aufrufe, zustand, zeilen = _lauf(
        monkeypatch, befund.Ergebnis(ok=True, zeilen=[]), dry_run=True
    )
    assert aufrufe == [] and not zustand and "[Probe]" in zeilen[0]


def test_tick_ruft_schritt_nach_hausmeister() -> None:
    quelle = inspect.getsource(capo)
    assert quelle.index("_hausmeister_schluss(spec, zustand") < quelle.index(
        "_spec_schliessen_schritt(repo, spec"
    )
