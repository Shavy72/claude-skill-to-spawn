"""``gh.letzter_commit`` gegen ein echtes temporäres git-Repo (#320) — keine Attrappen."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from to_spawn import gh


def _git(repo: Path, *args: str) -> str:
    fertig = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return fertig.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    for betreff in ("feat: eins (#7)", "chore: ohne Nummer", "fix: zwei (#7)", "docs: andere (#8)"):
        _git(tmp_path, "commit", "-q", "--allow-empty", "-m", betreff)
    return tmp_path


def test_treffer_gibt_kurzhash_des_juengsten_passenden_commits(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    erwartet = _git(repo, "rev-parse", "--short", "HEAD~1")  # "fix: zwei (#7)"
    assert gh.letzter_commit(repo, 7, "main") == erwartet


def test_kein_treffer_gibt_none(tmp_path: Path) -> None:
    assert gh.letzter_commit(_repo(tmp_path), 99, "main") is None


def test_fehlender_ref_gibt_none_und_warnt(tmp_path: Path, caplog) -> None:
    repo = _repo(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert gh.letzter_commit(repo, 7, "gibt-es-nicht") is None
    assert "Commit-Suche (#7) gescheitert" in caplog.text
