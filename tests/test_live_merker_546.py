# ruff: noqa: F811
"""#546: Live-Schritt setzt den Merker ``live_gelaufen``; der Takt schließt Staging-Specs damit und räumt die Fenster."""

from __future__ import annotations

from pathlib import Path
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
from test_waechter_213 import _commit

from to_spawn import capo, komplett_lauf, live_tuer, tmux_aufruf

STAGING = {
    "staging": {"url": "https://staging.example.test"},
    "pruefung": {"komplett": "true"},
}
GH = "test/wegwerf"


def _fertig(welt: dict[str, Path]) -> None:
    """Spec fertig mit eingechecktem Rückblick/Thermo, damit ``komplett_lauf`` keinen schmutzigen Klon meldet (#451)."""
    _spec_fertig(welt, rueckblick=False)
    _commit(
        welt["repo"],
        "docs: Rückblick (#902)",
        {
            f"docs/agents/rueckblick_{SPEC}.md": "Rückblick: keine Vorschläge\n",
            f"docs/agents/thermo_{SPEC}.md": "# Thermo\n\n- nichts auffällig\n",
        },
    )


@pytest.fixture(autouse=True)
def fenster(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Echte tmux-Fenster nie anfassen: Aufruf an der Modulgrenze ersetzt."""
    aufrufe: list[int] = []
    monkeypatch.setattr(tmux_aufruf, "spec_session_schliessen", lambda repo, spec: aufrufe.append(spec))
    return aufrufe


def _merken(welt) -> None:
    assert capo.live_merken(welt["repo"], int(SPEC), GH)


def test_staging_mit_merker_schliesst_und_raeumt_fenster_einmal(welt, hm, monkeypatch, fenster) -> None:
    _fertig(welt)
    _merken(welt)
    assert _tick(welt, monkeypatch, STAGING) == [int(SPEC)]
    assert _zustand(welt).get("spec_geschlossen")
    assert "live_gelaufen" not in _zustand(welt)
    assert fenster == [int(SPEC)]
    assert _tick(welt, monkeypatch, STAGING) == []  # nächster Takt: nichts mehr, Fenster nicht erneut
    assert fenster == [int(SPEC)]


def test_staging_ohne_merker_schliesst_nie(welt, hm, monkeypatch, fenster) -> None:
    _fertig(welt)
    assert _tick(welt, monkeypatch, STAGING) == []
    assert not _zustand(welt).get("spec_geschlossen") and fenster == []


def test_kaputter_zustand_schliesst_nie(welt, hm, monkeypatch, fenster) -> None:
    _fertig(welt)
    datei = capo._zustand_datei(welt["repo"], GH, int(SPEC))
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("{kaputt", encoding="utf-8")
    assert _tick(welt, monkeypatch, STAGING) == []
    assert fenster == []


def test_merker_ohne_staging_eintrag_schliesst_nie(welt, hm, monkeypatch, fenster) -> None:
    _fertig(welt)
    _merken(welt)
    assert _tick(welt, monkeypatch, {}) == [] and fenster == []


def test_staging_ohne_weg_bleibt_und_raeumt_fenster(welt, hm, monkeypatch, fenster) -> None:
    _fertig(welt)
    assert _tick(welt, monkeypatch, {"staging": {"ohne": True}}) == [int(SPEC)]
    assert fenster == [int(SPEC)]


def test_fehler_beim_fenster_schliessen_laesst_takt_gruen(welt, hm, monkeypatch) -> None:
    def kaputt(repo: Path, spec: int) -> None:
        raise tmux_aufruf.TmuxFehler("kill-session", "boom")

    monkeypatch.setattr(tmux_aufruf, "spec_session_schliessen", kaputt)
    _fertig(welt)
    _merken(welt)
    assert _tick(welt, monkeypatch, STAGING) == [int(SPEC)]
    assert _zustand(welt).get("spec_geschlossen")


@pytest.mark.parametrize("befehl, merker", [("true", True), ("false", False)])
def test_live_schritt_setzt_merker_nur_bei_erfolg(
    welt, monkeypatch: pytest.MonkeyPatch, befehl: str, merker: bool
) -> None:
    monkeypatch.setattr(komplett_lauf, "darf_live", lambda *a: SimpleNamespace(frei=True, grund="frei"))
    monkeypatch.setattr(capo.gh, "repo_aus_origin", lambda *a, **k: GH)
    code, _ = live_tuer.starte(welt["repo"], int(SPEC), {"live": {"befehl": befehl}})
    assert (code == 0) is merker
    assert capo.live_gelaufen(welt["repo"], int(SPEC), GH) is merker


def test_merker_wird_bei_belegter_takt_sperre_gesetzt(welt, hm) -> None:
    datei = capo._zustand_datei(welt["repo"], GH, int(SPEC))
    with capo.sperre(datei) as frei:
        assert frei
        assert capo.live_merken(welt["repo"], int(SPEC), GH)
    assert capo.live_gelaufen(welt["repo"], int(SPEC), GH)


@pytest.mark.parametrize("inhalt", ["", "   ", "kein-zeitstempel", "{kaputt"])
def test_leere_oder_kaputte_merker_datei_schliesst_nie(welt, hm, monkeypatch, fenster, inhalt) -> None:
    _fertig(welt)
    merker = capo._live_merker_datei(welt["repo"], int(SPEC), GH)
    merker.parent.mkdir(parents=True, exist_ok=True)
    merker.write_text(inhalt, encoding="utf-8")
    assert not capo.live_gelaufen(welt["repo"], int(SPEC), GH)
    assert _tick(welt, monkeypatch, STAGING) == [] and fenster == []


def _tmux_ersatz(monkeypatch: pytest.MonkeyPatch, pfade: str | Exception) -> list[tuple[str, ...]]:
    monkeypatch.undo()
    echte: list[tuple[str, ...]] = []

    def ersatz(*a: str, **k: object) -> str:
        echte.append(a)
        if a[0] == "list-panes" and isinstance(pfade, Exception):
            raise pfade
        return pfade if a[0] == "list-panes" else ""  # type: ignore[return-value]

    monkeypatch.setattr(tmux_aufruf, "aufrufen", ersatz)
    return echte


def test_eigene_session_wird_geschlossen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "meins"
    echte = _tmux_ersatz(monkeypatch, f"{repo}\n{repo}/sub\n")
    tmux_aufruf.spec_session_schliessen(repo, 7)
    assert echte[-1] == ("kill-session", "-t", "=spec-7")


def test_fremde_session_gleicher_nummer_bleibt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    echte = _tmux_ersatz(monkeypatch, str(tmp_path / "fremd"))
    tmux_aufruf.spec_session_schliessen(tmp_path / "meins", 7)
    assert all(a[0] != "kill-session" for a in echte)


def test_abfrage_scheitert_nichts_geschlossen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    echte = _tmux_ersatz(monkeypatch, tmux_aufruf.TmuxFehler("list-panes", "boom"))
    tmux_aufruf.spec_session_schliessen(tmp_path, 7)
    assert all(a[0] != "kill-session" for a in echte)
