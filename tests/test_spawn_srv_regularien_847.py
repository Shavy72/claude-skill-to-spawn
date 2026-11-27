"""Regressionstests für #847: ``spawn_srv.sh`` ruft ``regularien <S>`` statt veraltet ``pruefen <S>``."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SPAWN_SRV = SKILL / "skripte" / "spawn_srv.sh"
BEFUND = SKILL / "to_spawn" / "befund.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_spawn_srv_ruft_regularien_statt_pruefen_auf() -> None:
    """``skripte/spawn_srv.sh`` nutzt den aktuellen Unterbefehl ``regularien``."""
    text = SPAWN_SRV.read_text(encoding="utf-8")
    assert 'PRUEF_ARGS=(regularien "$SPEC")' in text
    assert 'PRUEF_ARGS=(pruefen ' not in text


def test_befund_nennt_regularien_statt_pruefen() -> None:
    """``to_spawn/befund.py`` verweist bei Manifest-Mängeln auf ``regularien``."""
    text = BEFUND.read_text(encoding="utf-8")
    assert "to_spawn.py pruefen" not in text
    assert "to_spawn.py regularien" in text


@pytest.mark.braucht("tmux")  # spawn_srv.sh prüft tmux (Bau-Server), Windows ohne tmux
def test_spawn_srv_probelauf_ohne_pruefen_warnung(tmp_path: Path) -> None:
    """Echter Aufruf von ``spawn_srv.sh --dry-run`` löst keine ``pruefen``-Warnung auf stderr aus."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", str(origin))

    manifest_ordner = repo / "docs" / "agents" / "manifests"
    manifest_ordner.mkdir(parents=True)
    (manifest_ordner / "spec-847.json").write_text(
        json.dumps({"spec": 847, "tickets": {"848": {"titel": "Test"}}}),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    _git(repo, "push", "-u", "origin", "main")

    env = {**os.environ, "TO_SPAWN_REPO": str(repo)}
    lauf = subprocess.run(
        ["bash", str(SPAWN_SRV), "847", "--dry-run"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert "WARNUNG: „to_spawn.py pruefen" not in lauf.stderr, lauf.stderr
    assert "== Regularien ==" in lauf.stdout, lauf.stdout + lauf.stderr
