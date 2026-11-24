"""#464: ``gh pr merge --match-head-commit`` bekommt immer die volle SHA (40 Zeichen)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from to_spawn import einreichen


def _repo(tmp_path: Path) -> tuple[Path, str]:
    def git(*a: str) -> str:
        return subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    return tmp_path, git("rev-parse", "HEAD")


def test_kurze_sha_wird_voll(tmp_path: Path) -> None:
    ordner, voll = _repo(tmp_path)
    assert einreichen._volle_sha(ordner, voll[:7]) == voll
    assert einreichen._volle_sha(ordner, voll) == voll


def test_unbekannte_sha_wird_abgelehnt(tmp_path: Path) -> None:
    ordner, _ = _repo(tmp_path)
    with pytest.raises(einreichen._Rot, match="volle SHA"):
        einreichen._volle_sha(ordner, "deadbee")
