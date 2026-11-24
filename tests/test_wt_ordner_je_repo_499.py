"""#499: Zwei Repos ohne eigenes ``worktree_basis`` teilen sich ``~/wt/wt-<N>``.

Jedes frische Repo beginnt bei Ticket #1; ohne ``worktree_basis`` in der Repo-Konfig zeigt
``config.worktree_pfad`` für beide Repos auf denselben Ordner. Sicherer Kern (Muster wie
``spec-<S>`` in #457): die Start-Stelle verweigert mit Exit 9, wenn der Ordner schon ein
Worktree eines anderen Repos ist (Repo-Kennung = ``origin``-Adresse, ohne ``origin`` der
gemeinsame Git-Ordner). Eigenes Repo, ein anderer Klon desselben Repos und ein noch
nicht vorhandener Ordner starten wie bisher — laufende ``wt-<N>`` werden weiter gefunden.

Echt: git (Wegwerf-Repos im Temp-Ordner), ``startstelle.betreten``. Keine Attrappe für git.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from to_spawn import config, startstelle

TICKET = "5"


def _git(ort: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(ort), *args], check=True, capture_output=True, text=True
    )


def _repo(tmp: Path, name: str) -> Path:
    """Bare-Remote + Klon mit einem Commit; ``origin`` zeigt auf das eigene Bare-Repo."""
    fern = tmp / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", str(fern)], check=True)
    repo = tmp / name
    subprocess.run(
        ["git", "clone", "-q", str(fern), str(repo)], check=True, capture_output=True
    )
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "start",
    )
    _git(repo, "push", "-q", "origin", "HEAD")
    return repo


@pytest.fixture()
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path / "wt"))
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    a, b = _repo(tmp_path, "repo-a"), _repo(tmp_path, "repo-b")
    wt = Path(config.worktree_pfad(TICKET, a)).expanduser()
    assert wt == Path(config.worktree_pfad(TICKET, b)).expanduser(), (
        "Ausgangslage: beide Repos teilen wt-5"
    )
    _git(a, "worktree", "add", "-q", "-b", f"ticket/{TICKET}-a", str(wt))
    return {"tmp": tmp_path, "a": a, "b": b, "wt": wt}


def _start(repo: Path, slug: str, ticket: str = TICKET) -> startstelle.Entscheidung:
    return startstelle.betreten(ticket, slug, repo, pruefer=lambda _t, _c: True)


def test_fremdes_repo_wird_beim_start_verweigert(welt: dict[str, Path]) -> None:
    entscheidung = _start(welt["b"], "test/repo-b-499")
    assert not entscheidung.erlaubt, entscheidung.grund
    assert entscheidung.exit_code == 9
    assert str(welt["wt"]) in entscheidung.grund and "repo-a" in entscheidung.grund


def test_eigenes_repo_und_zweiter_klon_starten_weiter(welt: dict[str, Path]) -> None:
    assert config.worktree_fremd(TICKET, welt["a"]) is None
    assert _start(welt["a"], "test/repo-a-499").erlaubt
    klon = welt["tmp"] / "repo-a-klon"
    subprocess.run(
        ["git", "clone", "-q", str(welt["tmp"] / "repo-a.git"), str(klon)], check=True
    )
    assert config.worktree_fremd(TICKET, klon) is None, (
        "anderer Klon desselben Repos ist dasselbe Repo"
    )


def test_ohne_origin_entscheidet_der_git_ordner(welt: dict[str, Path]) -> None:
    for repo in (welt["a"], welt["b"]):
        _git(repo, "remote", "remove", "origin")
    assert config.worktree_fremd(TICKET, welt["a"]) is None
    assert config.worktree_fremd(TICKET, welt["b"]) is not None


def test_fehlender_ordner_ist_frei(welt: dict[str, Path]) -> None:
    assert config.worktree_fremd("6", welt["b"]) is None
    assert _start(welt["b"], "test/repo-b-499-frei", "6").erlaubt is True
