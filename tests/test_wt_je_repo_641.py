"""#641: Zwei Repos, gleiche Ticket-Nummer — kein Stillstand mehr durch die Weigerung aus #499.

Ohne ``worktree_basis`` und ohne ``BAU_WT_DIR`` teilten sich zwei Repos ``~/wt/wt-<N>``; das
zweite Repo wurde mit Exit 9 abgewiesen und das Ticket stand. Jetzt weicht
``config.worktree_pfad`` aus: gehört ``<basis>/wt-<N>`` einem anderen Repo, liegt der eigene
Worktree unter ``<basis>/<repo-name>/wt-<N>``. Alter Bestand ``<basis>/wt-<N>`` des eigenen Repos
bleibt gültig, und ein einmal angelegter Ausweich-Ordner wird immer wieder gefunden.

Echt: git (Wegwerf-Repos im Temp-Ordner), ``startstelle.betreten``. Keine Attrappe für git.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import config, startstelle

TICKET = "35"


def _git(ort: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(ort), *args], check=True, capture_output=True, text=True)


def _repo(tmp: Path, name: str) -> Path:
    """Bare-Remote + Klon mit einem Commit; ``origin`` zeigt auf das eigene Bare-Repo."""
    fern = tmp / "fern" / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", str(fern)], check=True)
    repo = tmp / "repos" / name
    subprocess.run(["git", "clone", "-q", str(fern), str(repo)], check=True, capture_output=True)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "start")
    _git(repo, "push", "-q", "origin", "HEAD")
    return repo


@pytest.fixture()
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Plattform-Vorgabe ``~/wt`` mit Home im Temp-Ordner — kein ``BAU_WT_DIR``, kein ``worktree_basis``."""
    heim = tmp_path / "heim"
    heim.mkdir()
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("USERPROFILE", str(heim))
    monkeypatch.delenv("BAU_WT_DIR", raising=False)
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    return {
        "heim": heim,
        "a": _repo(tmp_path, "probe-mit"),
        "b": _repo(tmp_path, "probe-ohne"),
    }


def _pfad(repo: Path | None, ticket: str = TICKET) -> Path:
    return Path(config.worktree_pfad(ticket, repo)).expanduser()


def _start(repo: Path) -> startstelle.Entscheidung:
    return startstelle.betreten(TICKET, f"test/{repo.name}-641", repo, pruefer=lambda _t, _c: True)


def test_gleiche_nummer_zwei_repos_eigene_ordner_ohne_weigerung(welt: dict[str, Path]) -> None:
    wt_a = _pfad(welt["a"])
    assert wt_a == welt["heim"] / "wt" / "probe-mit" / f"wt-{TICKET}", "erstes Repo: immer mit Repo-Namen (#951)"
    _git(welt["a"], "worktree", "add", "-q", "-b", f"ticket/{TICKET}-a", str(wt_a))

    wt_b = _pfad(welt["b"])
    assert wt_b != wt_a and wt_a not in wt_b.parents
    assert wt_b == welt["heim"] / "wt" / "probe-ohne" / f"wt-{TICKET}"
    assert config.worktree_fremd(TICKET, welt["b"]) is None
    entscheidung = _start(welt["b"])
    assert entscheidung.erlaubt, entscheidung.grund

    # Beide bauen gleichzeitig: zweiter Worktree entsteht, keiner teilt einen Ordner.
    _git(welt["b"], "worktree", "add", "-q", "-b", f"ticket/{TICKET}-b", str(wt_b))
    assert _pfad(welt["a"]) == wt_a
    assert _pfad(welt["b"]) == wt_b
    assert _start(welt["a"]).erlaubt


def test_ausweich_ordner_bleibt_wenn_der_fremde_verschwindet(welt: dict[str, Path]) -> None:
    wt_a = _pfad(welt["a"])
    _git(welt["a"], "worktree", "add", "-q", "-b", f"ticket/{TICKET}-a", str(wt_a))
    wt_b = _pfad(welt["b"])
    _git(welt["b"], "worktree", "add", "-q", "-b", f"ticket/{TICKET}-b", str(wt_b))
    _git(welt["a"], "worktree", "remove", "--force", str(wt_a))
    assert _pfad(welt["b"]) == wt_b, "Leser nach dem Aufräumen des anderen Repos finden den Bau weiter"


def test_alter_bestand_des_eigenen_repos_bleibt_gueltig(welt: dict[str, Path]) -> None:
    flach = welt["heim"] / "wt" / f"wt-{TICKET}"
    _git(welt["b"], "worktree", "add", "-q", "-b", f"ticket/{TICKET}-b", str(flach))
    assert _pfad(welt["b"]) == flach
    assert _start(welt["b"]).erlaubt


def test_aus_dem_worktree_heraus_gilt_der_name_des_hauptrepos(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_a = _pfad(welt["a"])
    _git(welt["a"], "worktree", "add", "-q", "-b", f"ticket/{TICKET}-a", str(wt_a))
    wt_b = _pfad(welt["b"])
    _git(welt["b"], "worktree", "add", "-q", "-b", f"ticket/{TICKET}-b", str(wt_b))
    monkeypatch.chdir(wt_b)
    assert _pfad(None) == wt_b, "Hooks/Leser ohne repo (cwd = Worktree) finden denselben Ordner"
