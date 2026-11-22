"""#169 Fix-Runde 1: gesperrte Pfade auch bei Nicht-ASCII, Großschreibung, Umbenennung, Merge.

Echte Git-Repos unter ``tmp_path`` mit bare-„origin“ (Fixtures aus den #169- und
#1439-Tests); git ist nie attrappiert. Zusätzlich der zweite Push-Weg ``sicherung/<N>``
über den Hausmeister.
"""

# ruff: noqa: F811 — Fixtures kommen aus anderen Test-Modulen und werden als Parameter genannt.
from __future__ import annotations

from pathlib import Path

from test_hausmeister_1368 import Welt, git, welt  # noqa: F401 — ``welt`` ist ein Fixture
from test_hausmeister_1439 import _heads, _lokale_zweige, _zeilen_mit, zw  # noqa: F401 — ``zw`` ist ein Fixture
from test_uebergabe_sichern_169 import _entfernte_zweige, _git, _zweig, skill  # noqa: F401

from to_spawn import hausmeister
from to_spawn import uebergabe_sichern as us


def _verweigert(erg: us.Sicherung, zweig: str) -> list[str]:
    return [f for f in erg.fehler if "verweigert" in f and zweig in f]


def test_nicht_ascii_pfad_wird_verweigert(skill: dict[str, Path]) -> None:
    _zweig(skill, "l1/umlaut", [("Ünter/.claude-handoff.md", "roh\n")])
    erg = us.sichern()
    assert _entfernte_zweige(skill["origin"]) == ["main"]
    gesperrt = _verweigert(erg, "l1/umlaut")
    assert gesperrt and "Ünter/.claude-handoff.md" in gesperrt[0], erg.fehler


def test_grossschreibung_wird_verweigert(skill: dict[str, Path]) -> None:
    assert us.nie_oeffentlich(".CLAUDE-HANDOFF.md")
    assert us.nie_oeffentlich("docs/Entscheidungen.md")
    _zweig(skill, "l1/gross", [(".CLAUDE-HANDOFF.md", "roh\n")])
    _zweig(skill, "l1/log", [("docs/Entscheidungen.md", "E1\n")])
    erg = us.sichern()
    assert _entfernte_zweige(skill["origin"]) == ["main"]
    assert _verweigert(erg, "l1/gross"), erg.fehler
    assert _verweigert(erg, "l1/log"), erg.fehler


def test_umbenennung_zur_uebergabe_wird_verweigert(skill: dict[str, Path]) -> None:
    wt = _zweig(skill, "l1/umbenannt", [("notiz.md", "roh\n")])
    _git(wt, "mv", "notiz.md", ".claude-handoff.md")
    _git(wt, "commit", "-q", "-m", "umbenannt")
    erg = us.sichern()
    assert _entfernte_zweige(skill["origin"]) == ["main"]
    assert _verweigert(erg, "l1/umbenannt"), erg.fehler


def test_uebergabe_nur_in_merge_aufloesung_wird_verweigert(skill: dict[str, Path]) -> None:
    _zweig(skill, "l1/neben", [("b.py", "b = 1\n")])
    wt = _zweig(skill, "l1/merge", [("a.py", "a = 1\n")])
    _git(wt, "merge", "-q", "--no-ff", "--no-commit", "l1/neben")
    (wt / ".claude-handoff.md").write_text("roh\n", encoding="utf-8")
    _git(wt, "add", "-f", ".claude-handoff.md")
    _git(wt, "commit", "-q", "-m", "merge mit Übergabe")
    # Nur der Merge-Commit enthält die Übergabe, kein Eltern-Commit.
    assert ".claude-handoff.md" not in _git(wt, "log", "--no-merges", "--name-only", "--format=")
    erg = us.sichern()
    assert "l1/merge" not in _entfernte_zweige(skill["origin"])
    assert _verweigert(erg, "l1/merge"), erg.fehler


def test_sicherungs_push_mit_uebergabe_wird_verweigert(zw: Welt) -> None:
    """Zweiter Push-Weg: lokaler Zweig mit Übergabe geht nie auf ``sicherung/<N>``."""
    wt = zw.heim / "wt" / "wt-49"
    git(zw.haupt, "worktree", "add", "-q", "-b", "ticket/49-test", str(wt))
    (wt / ".claude-handoff.md").write_text("roh\n", encoding="utf-8")
    git(wt, "add", "-f", ".claude-handoff.md")
    git(wt, "commit", "-q", "-m", "c handoff")
    zw.ticket(49, "CLOSED", vor_stunden=48)
    hausmeister._loeschen(wt, True)
    assert "sicherung/49" not in _heads(zw)
    gruende = [str(z.get("grund", "")) for z in _zeilen_mit(zw, "ticket/49-test")]
    assert any("verweigert" in g and ".claude-handoff.md" in g for g in gruende), gruende
    assert "ticket/49-test" in _lokale_zweige(zw)
