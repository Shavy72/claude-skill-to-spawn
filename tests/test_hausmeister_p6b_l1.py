"""L1 Etappe 1 P6b: Hausmeister-Schutzliste aus der Maschinen-Liste, nur addierend.

``hausmeister.geschuetzte_ordner`` in ``maschine.json`` kann weitere Ordnernamen schützen,
nie eingebaute freigeben. Wurzeln mit verstecktem Ordner im festen Teil (``~/.ssh/*``)
fallen weg — dort liegen Schlüssel und Einstellungen, keine Worktrees.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_hausmeister_l1_konfig import _home, _maschine, ohne_naehte, welt_liste  # noqa: F401 — Fixtures
from test_hausmeister_1368 import welt  # noqa: F401 — Fixture (#1368)

from to_spawn import hausmeister

EINGEBAUT = ("duoplus-management", "claude-skill-to-spawn", "duoplus-leit", "deploy-schlange", "obsidian")


def test_ohne_schluessel_eingebaute_liste() -> None:
    assert hausmeister._geschuetzte_ordner() == EINGEBAUT
    assert hausmeister.GESCHUETZTE_ORDNER == EINGEBAUT


def test_konfig_schuetzt_zusaetzlich() -> None:
    _maschine({"hausmeister": {"geschuetzte_ordner": ["mein-archiv", " notizen ", "", 7]}})
    assert hausmeister._geschuetzte_ordner() == (*EINGEBAUT, "mein-archiv", "notizen")


def test_konfig_gibt_nie_frei() -> None:
    # Leere Liste oder kaputter Wert: eingebaute Namen bleiben geschützt
    for wert in ([], "obsidian", None, {"x": 1}):
        _maschine({"hausmeister": {"geschuetzte_ordner": wert}})
        assert set(EINGEBAUT) <= set(hausmeister._geschuetzte_ordner()), wert


def test_weg_geschuetzter_ordner_aus_konfig_bleibt(welt_liste) -> None:  # noqa: F811
    """Weg durch aufraeumen: löschbarer Worktree bleibt, sobald die Konfig seinen Namen schützt."""
    w = welt_liste
    wt = w.worktree(12)
    w.ticket(12, "CLOSED", 48)
    eintrag = {"slug": "Shavy72/duoplus-management", "pfad": str(w.haupt), "ticket_repo": "Shavy72/duoplus-management"}
    _maschine({"repos": [eintrag], "hausmeister": {"wurzeln": [f"{w.heim}/wt/*"]}})
    assert w.aufraeumen(wt).art == "gelöscht"  # Gegenprobe (trocken)
    _maschine({"repos": [eintrag], "hausmeister": {"wurzeln": [f"{w.heim}/wt/*"], "geschuetzte_ordner": [wt.name]}})
    r = w.aufraeumen(wt)
    assert (r.art, r.grund) == ("bleibt", "geschützter Ordner")
    assert wt.is_dir()


@pytest.mark.parametrize("wurzel", ["~/.ssh/*", "~/.config/x/*", "~/a/.cache/wt-*"])
def test_versteckte_wurzel_abgelehnt(
    ohne_naehte: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wurzel: str
) -> None:
    heim = _home(monkeypatch, tmp_path)
    _maschine({"repos": [], "hausmeister": {"wurzeln": [wurzel, "~/bau/*"]}})
    assert hausmeister._wurzeln() == [str(heim / "bau" / "*")]
