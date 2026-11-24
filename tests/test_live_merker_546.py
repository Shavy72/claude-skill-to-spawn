# ruff: noqa: F811
"""#546: Live-Schritt setzt den Merker ``live_gelaufen``; der Takt schließt Staging-Specs damit und räumt die Fenster."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_spec_schliessen_nach_fertig_543 import (  # noqa: F401
    SPEC,
    _spec_fertig,
    _tick,
    _zustand,
    config,
    hm,
    welt,
)

from to_spawn import capo, komplett_lauf, live_tuer, tmux_aufruf

STAGING = {"staging": {"url": "https://staging.example.test"}}
GH = "test/wegwerf"


@pytest.fixture(autouse=True)
def fenster(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Echte tmux-Fenster nie anfassen: Aufruf an der Modulgrenze ersetzt."""
    aufrufe: list[int] = []
    monkeypatch.setattr(tmux_aufruf, "spec_session_schliessen", aufrufe.append)
    return aufrufe


def _merken(welt) -> None:
    assert capo.live_merken(welt["repo"], int(SPEC), GH)


def test_staging_mit_merker_schliesst_und_raeumt_fenster_einmal(
    welt, hm, monkeypatch, fenster
) -> None:
    _spec_fertig(welt)
    _merken(welt)
    assert _tick(welt, monkeypatch, STAGING) == [int(SPEC)]
    assert _zustand(welt).get("spec_geschlossen") and _zustand(welt).get(
        "live_gelaufen"
    )
    assert fenster == [int(SPEC)]
    assert (
        _tick(welt, monkeypatch, STAGING) == []
    )  # nächster Takt: nichts mehr, Fenster nicht erneut
    assert fenster == [int(SPEC)]


def test_staging_ohne_merker_schliesst_nie(welt, hm, monkeypatch, fenster) -> None:
    _spec_fertig(welt)
    assert _tick(welt, monkeypatch, STAGING) == []
    assert not _zustand(welt).get("spec_geschlossen") and fenster == []


def test_kaputter_zustand_schliesst_nie(welt, hm, monkeypatch, fenster) -> None:
    _spec_fertig(welt)
    datei = capo._zustand_datei(welt["repo"], GH, int(SPEC))
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("{kaputt", encoding="utf-8")
    assert _tick(welt, monkeypatch, STAGING) == []
    assert fenster == []


def test_merker_ohne_staging_eintrag_schliesst_nie(
    welt, hm, monkeypatch, fenster
) -> None:
    _spec_fertig(welt)
    _merken(welt)
    assert _tick(welt, monkeypatch, {}) == [] and fenster == []


def test_staging_ohne_weg_bleibt_und_raeumt_fenster(
    welt, hm, monkeypatch, fenster
) -> None:
    _spec_fertig(welt)
    assert _tick(welt, monkeypatch, {"staging": {"ohne": True}}) == [int(SPEC)]
    assert fenster == [int(SPEC)]


def test_fehler_beim_fenster_schliessen_laesst_takt_gruen(
    welt, hm, monkeypatch
) -> None:
    def kaputt(spec: int) -> None:
        raise tmux_aufruf.TmuxFehler("kill-session", "boom")

    monkeypatch.setattr(tmux_aufruf, "spec_session_schliessen", kaputt)
    _spec_fertig(welt)
    _merken(welt)
    assert _tick(welt, monkeypatch, STAGING) == [int(SPEC)]
    assert _zustand(welt).get("spec_geschlossen")


@pytest.mark.parametrize("befehl, merker", [("true", True), ("false", False)])
def test_live_schritt_setzt_merker_nur_bei_erfolg(
    welt, monkeypatch: pytest.MonkeyPatch, befehl: str, merker: bool
) -> None:
    monkeypatch.setattr(
        komplett_lauf, "darf_live", lambda *a: SimpleNamespace(frei=True, grund="frei")
    )
    monkeypatch.setattr(capo.gh, "repo_aus_origin", lambda *a, **k: GH)
    code, _ = live_tuer.starte(welt["repo"], int(SPEC), {"live": {"befehl": befehl}})
    assert (code == 0) is merker
    datei = capo._zustand_datei(welt["repo"], GH, int(SPEC))
    stand = json.loads(datei.read_text(encoding="utf-8")) if datei.is_file() else {}
    assert capo.live_gelaufen(stand) is merker


def test_session_schliessen_ruft_kill_session(monkeypatch: pytest.MonkeyPatch) -> None:
    echte: list[tuple[str, ...]] = []
    monkeypatch.undo()
    monkeypatch.setattr(tmux_aufruf, "aufrufen", lambda *a, **k: echte.append(a) or "")
    tmux_aufruf.spec_session_schliessen(7)
    assert echte == [("kill-session", "-t", "=spec-7")]
