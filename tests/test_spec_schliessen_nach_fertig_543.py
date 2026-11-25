# ruff: noqa: F811
"""#543: Nach SPEC FERTIG schließt capo das Spec-Issue über den vorhandenen Weg ``befund.spec_schliessen``."""

from __future__ import annotations

import inspect
import json
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
    aufrufe, zustand, zeilen = _lauf(monkeypatch, befund.Ergebnis(ok=True, zeilen=["Spec #7 geschlossen"]))
    assert aufrufe == [7] and "spec_geschlossen" in zustand and zeilen == ["Spec #7 geschlossen"]


def test_abbruch_ist_kein_fehler_und_wird_wiederholt(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        _, zustand, zeilen = _lauf(monkeypatch, befund.Ergebnis(ok=False, zeilen=["Spec #7 bleibt offen"]))
    assert "spec_geschlossen" not in zustand and zeilen == []
    assert "bleibt offen" in caplog.text


def test_ausnahme_stuerzt_nicht_ab(monkeypatch: pytest.MonkeyPatch) -> None:
    _, zustand, zeilen = _lauf(monkeypatch, RuntimeError("gh weg"))
    assert "spec_geschlossen" not in zustand and zeilen == []


def test_probelauf_schliesst_nichts(monkeypatch: pytest.MonkeyPatch) -> None:
    aufrufe, zustand, zeilen = _lauf(monkeypatch, befund.Ergebnis(ok=True, zeilen=[]), dry_run=True)
    assert aufrufe == [] and not zustand and "[Probe]" in zeilen[0]


def test_tick_ruft_schritt_nach_hausmeister() -> None:
    quelle = inspect.getsource(capo)
    assert quelle.index("_hausmeister_schluss(spec, zustand") < quelle.index("_spec_schliessen_schritt(repo, spec")


# --- echter Tick (Git-Repo, Zustandsdatei wie #1370); nur ``spec_schliessen`` ist Attrappe -----------
from test_aufraeumen_schluss_1370 import (  # noqa: F401
    SPEC,
    _spec_fertig,
    _zustand,
    config,
    hm,
    welt,
)


def _tick(welt, monkeypatch: pytest.MonkeyPatch, konfig_zusatz: dict | str) -> list[int]:
    """Tick mit echter ``config.lade`` über die geschriebene ``.to-spawn/config.json``."""
    aufrufe: list[int] = []

    def attrappe(repo: Path, gh_repo: str, spec: int, abruf=None):
        aufrufe.append(spec)
        return befund.Ergebnis(ok=True, zeilen=[f"Spec #{spec} geschlossen"])

    monkeypatch.setattr(befund, "spec_schliessen", attrappe)
    monkeypatch.setenv("TO_SPAWN_REPO", str(welt["repo"]))
    monkeypatch.chdir(welt["repo"])
    datei = welt["repo"] / config.KONFIG_PFAD
    if isinstance(konfig_zusatz, str):
        datei.write_text(konfig_zusatz, encoding="utf-8")
    else:
        alt = json.loads(datei.read_text(encoding="utf-8")) if datei.is_file() else {}
        for gruppe, werte in konfig_zusatz.items():
            alt.setdefault(gruppe, {}).update(werte)
        datei.write_text(json.dumps(alt), encoding="utf-8")
    konfig = config.lade(welt["repo"])
    capo.tick(welt["repo"], int(SPEC), "test/wegwerf", konfig, wt_basis=str(welt["wt"]))
    return aufrufe


def test_tick_bewusst_ohne_staging_schliesst_nach_aufraeumen(welt, hm, monkeypatch) -> None:
    _spec_fertig(welt)
    assert _tick(welt, monkeypatch, {"staging": {"ohne": True}}) == [int(SPEC)]
    assert _zustand(welt).get("spec_geschlossen")


@pytest.mark.parametrize(
    "zusatz",
    [
        {"staging": {"url": "https://staging.example.test"}},
        {"vps": {"staging_ssh": "staging-vps"}},
        "{kaputtes json",
        {},
    ],
    ids=["url", "nur_vps_staging_ssh", "kaputte_datei", "ohne_eintrag"],
)
def test_tick_ohne_positiven_beleg_schliesst_nie(welt, hm, monkeypatch, zusatz) -> None:
    _spec_fertig(welt)
    assert _tick(welt, monkeypatch, zusatz) == []
    assert not _zustand(welt).get("spec_geschlossen")
