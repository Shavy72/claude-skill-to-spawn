"""#508: Ein normaler Probesitz löscht seinen EIGENEN Worktree weiterhin."""

from __future__ import annotations

import subprocess
from pathlib import Path

from to_spawn import probesitz


def _git(ort: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(ort), *args], check=True, capture_output=True, text=True)


def _lauf(argv: list[str], cwd: Path | None = None, timeout: float = 0) -> subprocess.CompletedProcess[str]:
    if Path(argv[0]).name == "gh":
        return subprocess.CompletedProcess(argv, 0, "", "")
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)


def _welt(tmp: Path) -> tuple[Path, Path]:
    fern = tmp / "r.git"
    subprocess.run(["git", "init", "-q", "--bare", str(fern)], check=True)
    repo = tmp / "r"
    subprocess.run(["git", "clone", "-q", str(fern), str(repo)], check=True, capture_output=True)
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
        "s",
    )
    _git(repo, "push", "-q", "origin", "HEAD")
    wt = tmp / "wt" / "wt-7"
    _git(repo, "worktree", "add", "-q", "-b", "ticket/7-x", str(wt))
    return repo, wt


def test_aufraeumen_loescht_eigenen_worktree(tmp_path: Path) -> None:
    repo, wt = _welt(tmp_path)
    assert wt.exists()
    probesitz._aufraeumen(
        repo,
        "7",
        wt,
        "ticket/7-x",
        _lauf,
        lambda n: f"/usr/bin/{n}",
        "ok",
        worktree_loeschen=True,
    )
    assert not wt.exists()


def test_aufraeumen_laesst_fremden_worktree_stehen(tmp_path: Path) -> None:
    repo, wt = _welt(tmp_path)
    probesitz._aufraeumen(
        repo,
        "7",
        wt,
        "ticket/7-x",
        _lauf,
        lambda n: f"/usr/bin/{n}",
        "ok",
        worktree_loeschen=False,
    )
    assert wt.exists()
