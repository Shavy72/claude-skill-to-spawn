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


# --- echter Tick (Git-Repo, Zustandsdatei wie #1370); nur ``spec_schliessen`` ist Attrappe -----------
from test_aufraeumen_schluss_1370 import (  # noqa: F401
    SPEC,
    _spec_fertig,
    _zustand,
    config,
    hm,
    welt,
)


def _tick(welt, monkeypatch: pytest.MonkeyPatch, staging: bool) -> list[int]:  # noqa: F811
    aufrufe: list[int] = []

    def attrappe(repo: Path, gh_repo: str, spec: int, abruf=None):
        aufrufe.append(spec)
        return befund.Ergebnis(ok=True, zeilen=[f"Spec #{spec} geschlossen"])

    monkeypatch.setattr(befund, "spec_schliessen", attrappe)
    monkeypatch.setenv("TO_SPAWN_REPO", str(welt["repo"]))
    monkeypatch.chdir(welt["repo"])
    konfig = config.lade(welt["repo"])
    if staging:
        konfig["staging"] = {"url": "https://staging.example.test"}
    capo.tick(welt["repo"], int(SPEC), "test/wegwerf", konfig, wt_basis=str(welt["wt"]))
    return aufrufe


def test_tick_ohne_staging_schliesst_nach_aufraeumen(welt, hm, monkeypatch) -> None:  # noqa: F811
    _spec_fertig(welt)
    assert _tick(welt, monkeypatch, staging=False) == [int(SPEC)]
    assert _zustand(welt).get("spec_geschlossen")


def test_tick_mit_staging_schliesst_nie(welt, hm, monkeypatch) -> None:  # noqa: F811
    _spec_fertig(welt)
    assert _tick(welt, monkeypatch, staging=True) == []
    assert not _zustand(welt).get("spec_geschlossen")
