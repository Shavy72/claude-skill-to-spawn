"""Blocker gilt als erledigt bei grünem Staging (#1527).

David (21.11.2026): „Kein Ticket darf darauf warten, dass ein anderes live ist.“
Ein Ticket mit Nutzer-Ablauf bleibt bis zum Live-Klickweg offen. Die Deploy-Schlange
setzt nach grünem Staging-Deploy das Label ``staging:fertig``; ab dann zählt es für
Folge-Tickets wie ein geschlossener Blocker. Die Regel lebt nur in
``gh.blocker_erledigt`` — bau.py, spec_stand, capo und ticketstand fragen nur dort.

Echt sind ``gh.blocker_erledigt``, ``gh.offene_blocker``, ``bau.blocker_offen`` und
``ticketstand.zustand``; ersetzt wird nur ``gh`` (externer Dienst, Stub im PATH).
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import gh, ticketstand  # noqa: E402

_ZAEHLER = itertools.count()


def _dep(nr: int, state: str, *labels: str) -> dict[str, Any]:
    return {"number": nr, "state": state, "labels": [{"name": x} for x in labels]}


# --- Die eine Regel ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("dep", "erledigt"),
    [
        (_dep(1, "closed"), True),
        (_dep(1, "CLOSED"), True),
        (_dep(1, "open", "staging:fertig"), True),
        (_dep(1, "OPEN", "ready-for-agent", "staging:fertig"), True),
        (_dep(1, "open"), False),
        (_dep(1, "open", "ready-for-agent"), False),
        ({"number": 1, "state": "open", "labels": ["staging:fertig"]}, True),
        ({"number": 1}, False),
    ],
)
def test_blocker_erledigt(dep: dict[str, Any], erledigt: bool) -> None:
    assert gh.blocker_erledigt(dep) is erledigt


def test_offene_blocker_ueberspringt_staging_fertig() -> None:
    deps = [_dep(3, "open"), _dep(1, "open", "staging:fertig"), _dep(2, "closed")]
    assert gh.offene_blocker(deps) == [3]


def test_label_name_eine_konstante() -> None:
    assert gh.STAGING_FERTIG_LABEL == "staging:fertig"


# --- bau.py wartet nicht auf einen Blocker, der grün auf Staging liegt -------------


def _lade_bau(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    monkeypatch.setenv("TO_SPAWN_GH_REPO", "test/spiegel")
    modulname = f"bau_staging_1527_{next(_ZAEHLER)}"
    spec = importlib.util.spec_from_file_location(modulname, SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    bau = importlib.util.module_from_spec(spec)
    sys.modules[modulname] = bau
    try:
        spec.loader.exec_module(bau)
    finally:
        sys.modules.pop(modulname, None)
    bau.GH_REPO = "test/spiegel"
    return bau


def _fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, deps: object) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    daten = tmp_path / "deps.json"
    daten.write_text(json.dumps(deps), encoding="utf-8")
    stub = bin_dir / "gh_stub.py"
    stub.write_text(f"import sys\nsys.stdout.write(open(r'{daten}', encoding='utf-8').read())\n", encoding="utf-8")
    if os.name == "nt":
        (bin_dir / "gh.cmd").write_text(f'@"{sys.executable}" "{stub}" %*\n', encoding="utf-8")
    else:
        skript = bin_dir / "gh"
        skript.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{stub}' \"$@\"\n", encoding="utf-8")
        skript.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def _repo_mit_commit(tmp_path: Path, betreff: str) -> None:
    """Echtes git-Repo mit ``origin/master``, das den Blocker-Commit trägt."""

    def git(*args: str, cwd: Path = tmp_path) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    ursprung = tmp_path / "ursprung.git"
    git("init", "-q", "--bare", "-b", "master", str(ursprung))
    git("init", "-q", "-b", "master")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", betreff)
    git("remote", "add", "origin", str(ursprung))
    git("push", "-q", "origin", "master")
    git("fetch", "-q", "origin")


def test_bau_staging_fertig_mit_commit_ist_frei(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    _repo_mit_commit(tmp_path, "feat(x): Freischaltung (#5) [skip ci]")
    _fake_gh(tmp_path, monkeypatch, [_dep(5, "open", "staging:fertig")])
    monkeypatch.setattr(bau.gh, "hauptzweig", lambda _repo: "master")
    assert bau.blocker_offen("906") == []


def test_bau_staging_fertig_ohne_commit_wartet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    _repo_mit_commit(tmp_path, "chore: anderes (#9)")
    _fake_gh(tmp_path, monkeypatch, [_dep(5, "open", "staging:fertig")])
    monkeypatch.setattr(bau.gh, "hauptzweig", lambda _repo: "master")
    gruende = bau.blocker_offen("906")
    assert len(gruende) == 1 and "kein Commit" in gruende[0]


def test_bau_sofort_staging_fertig_ist_frei(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    _fake_gh(tmp_path, monkeypatch, [_dep(5, "open", "staging:fertig"), _dep(6, "open")])
    assert bau.blocker_offen("906", nur_offene=True) == ["#6 offen"]


# --- ticketstand: GitHub-Zähler kennt das Label nicht, die Blocker-Liste schon -----


def test_ticketstand_nicht_blockiert_wenn_blocker_nur_staging_fertig() -> None:
    issue = {
        "number": 2,
        "state": "open",
        "title": "t",
        "labels": [],
        "issue_dependencies_summary": {"blocked_by": 1},
    }
    z, _ = ticketstand.zustand(issue, None, [])
    assert z != ticketstand.BLOCKIERT


def test_ticketstand_ohne_liste_bleibt_zaehler_massgeblich() -> None:
    issue = {
        "number": 2,
        "state": "open",
        "title": "t",
        "labels": [],
        "issue_dependencies_summary": {"blocked_by": 1},
    }
    z, _ = ticketstand.zustand(issue, None, None)
    assert z == ticketstand.BLOCKIERT
