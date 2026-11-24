"""#596: Falsches Warten auf einen fertigen Blocker endet nach höchstens einem Wecker-Takt.

Die Bau-Session meldet ``WARTET: #<B> offen`` und schläft (ScheduleWakeup). Wird #<B> fertig,
stößt der Briefkasten-Wecker (60 s) sie an — dieselbe Regel wie der Aufpasser (#269), genau ein
Vorfall ``blocker_falsch_gewartet``. GitHub wird nur für Fenster gefragt, die ``WARTET`` zeigen;
scheitert die Abfrage, kein Anstoß (fail-closed).

Gestellt sind nur ``tmux`` (Attrappe aus ``test_briefkasten_wecker_414``) und ``gh`` (externer
Dienst; Form der REST-Antwort ``issues/<N>/dependencies/blocked_by``). git, Wecker, Regel und
Bau-Log sind echt.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import test_briefkasten_wecker_414 as basis
from test_briefkasten_wecker_414 import LINIE, PANE, SPEC, TICKET, _bild, _getippt

from to_spawn import aufpasser, bau_log, briefkasten_wecker, gh

welt = basis.welt  # Fixture aus dem Grund-Test (Hauptbaum + gestelltes tmux)
BLOCKER = TICKET + 10
BAU = f"=spec-{SPEC}:@2"


def _wartet(zeile: str) -> str:
    """Claude-Pane nach ``to_spawn.py blocker <N>``: letzte Ausgabe, dann leeres Eingabefeld."""
    return f"● Bash(to_spawn.py blocker {TICKET})\n  ⎿  {zeile}\n\n{LINIE}\n❯ \n{LINIE}\n  ⏵⏵ auto mode\n"


class GitHub:
    """Gestellte Blocker-Kanten; zählt die Abfragen. Alles andere scheitert (hier nicht Thema)."""

    def __init__(self) -> None:
        self.zustand = "open"
        self.kaputt = False
        self.abfragen = 0

    def lauf(self, args: list[str], *_a: Any, **_kw: Any) -> tuple[int, str]:
        if args[:2] != ["api", f"repos/o/r/issues/{TICKET}/dependencies/blocked_by"]:
            return 1, ""
        self.abfragen += 1
        if self.kaputt:
            return 1, ""
        return 0, json.dumps([{"number": BLOCKER, "state": self.zustand, "labels": []}])


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True, capture_output=True
    )


@pytest.fixture
def github(welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> GitHub:
    """Hauptbaum als git-Repo mit origin; Vorfälle landen im Hauptbaum."""
    haupt: Path = welt["haupt"]
    origin = welt["bin"] / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    _git(haupt, "init", "-q", "-b", "main")
    _git(haupt, "remote", "add", "origin", str(origin))
    _git(haupt, "commit", "-q", "--allow-empty", "-m", "chore: Start")
    _git(haupt, "push", "-q", "origin", "main")
    for name in bau_log.RUECKFALL_VARIABLEN:
        monkeypatch.delenv(name, raising=False)
    g = GitHub()
    monkeypatch.setattr(gh, "lauf", g.lauf)
    monkeypatch.setenv("TO_SPAWN_WAECHTER_ORDNER", str(welt["bin"] / "waechter"))
    return g


def _blocker_fertig(welt: dict[str, Any], github: GitHub) -> None:
    _git(welt["haupt"], "commit", "-q", "--allow-empty", "-m", f"feat: Vorgänger (#{BLOCKER})")
    _git(welt["haupt"], "push", "-q", "origin", "main")
    github.zustand = "closed"


def _vorfaelle(welt: dict[str, Any]) -> list[dict]:
    pfad = bau_log.lauf_pfad(welt["haupt"], TICKET)
    if not pfad.is_file():
        return []
    zeilen = [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines() if z.strip()]
    return [z for z in zeilen if z.get("typ") == "vorfall" and z.get("regel") == "blocker_falsch_gewartet"]


def _wecker(welt: dict[str, Any]) -> briefkasten_wecker.Wecker:
    return briefkasten_wecker.Wecker(SPEC, welt["haupt"], PANE, gh_repo="o/r")


def test_blocker_fertig_im_schlaf_anstoss_nach_einem_takt_genau_ein_vorfall(
    welt: dict[str, Any], github: GitHub
) -> None:
    _bild(welt, _wartet(f"WARTET: #{BLOCKER} offen"))
    w = _wecker(welt)
    w.einmal()  # Blocker offen: sie wartet zu Recht
    assert _getippt(welt) == []
    assert _vorfaelle(welt) == []
    _blocker_fertig(welt, github)
    w.einmal()  # ein Wecker-Takt
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [BAU], getippt
    assert f"to_spawn.py blocker {TICKET}" in getippt[0]["text"]
    assert f"#{BLOCKER}" in getippt[0]["text"]
    w.einmal()  # dieselbe Zeile weckt nicht noch einmal
    assert len(_getippt(welt)) == 1
    vorfaelle = _vorfaelle(welt)
    assert len(vorfaelle) == 1, vorfaelle
    assert f"#{BLOCKER}" in vorfaelle[0]["beispiel"] and f"#{TICKET}" in vorfaelle[0]["beispiel"]
    # Der Aufpasser (Takt) schreibt über dieselbe Stelle — kein zweiter Vorfall.
    aufpasser.notiere_blocker_falsch(welt["haupt"], TICKET, f"#{BLOCKER}", "2026-11-24")
    assert len(_vorfaelle(welt)) == 1


def test_ohne_wartet_zeile_keine_github_abfrage(welt: dict[str, Any], github: GitHub) -> None:
    _blocker_fertig(welt, github)
    w = _wecker(welt)
    w.einmal()
    _bild(welt, _wartet("FREI"))
    w.einmal()
    assert github.abfragen == 0
    assert _getippt(welt) == []


def test_abfrage_scheitert_kein_anstoss(welt: dict[str, Any], github: GitHub) -> None:
    _blocker_fertig(welt, github)
    github.kaputt = True
    _bild(welt, _wartet(f"WARTET: #{BLOCKER} offen"))
    _wecker(welt).einmal()
    assert github.abfragen == 1
    assert _getippt(welt) == []
    assert _vorfaelle(welt) == []
