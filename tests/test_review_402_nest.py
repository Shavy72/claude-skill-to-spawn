"""Weg-Tests für Review-Befund A3 (#402): ``git clone``/``git init`` in die Skills.

Echt laufen: das Hook-Skript ``nest/skill_schutz.py`` als Subprozess mit echtem
Hook-JSON auf stdin (wie ``test_skill_schutz_325.py``). ``SKILL_SCHUTZ_WURZEL``
setzt das Home auf einen Wegwerf-Ordner, damit ``~`` dort landet (keine Attrappen).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "nest" / "skill_schutz.py"
URL = "https://github.com/probe-org/probe-skill.git"


def _hook(home: Path, befehl: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    eingabe = json.dumps({"tool_name": "Bash", "tool_input": {"command": befehl}, "cwd": str(cwd)})
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=eingabe,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "SKILL_SCHUTZ_WURZEL": str(home), "HOME": str(home)},
        check=False,
    )


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    (tmp_path / ".claude" / "skills" / "to-spawn").mkdir(parents=True)
    (tmp_path / "repo").mkdir()
    return tmp_path


@pytest.mark.parametrize(
    "befehl",
    [
        f"git clone {URL} ~/.claude/skills/x",
        f"git clone --depth 1 {URL} ~/.claude/skills/x",
        f"git clone -b main --depth=1 {URL} ~/.claude/skills/x",
        f"git clone --branch main --single-branch {URL} ~/.claude/skills/x",
        f"git -C /tmp clone {URL} ~/.claude/skills/x",
        f"git -c core.autocrlf=false clone {URL} ~/.claude/skills/x",
        f"cd ~/.claude/skills && git clone {URL}",
        f"cd ~/.claude/skills && git clone {URL} neu",
        "git init ~/.claude/skills/x",
        "git init -b main ~/.claude/skills/x",
        "git init --initial-branch=main ~/.claude/skills/x",
        "git -C ~/.claude/skills/to-spawn init",
        "cd ~/.claude/skills/to-spawn && git init",
        "git init --separate-git-dir ~/.claude/skills/x.git /tmp/arbeit",
    ],
)
def test_git_clone_init_in_skills_wird_verweigert(home: Path, befehl: str) -> None:
    ergebnis = _hook(home, befehl, home / "repo")
    assert ergebnis.returncode == 2, (befehl, ergebnis.stderr)
    assert "Skill-Dateien sind auf dem Bau-Server schreibgeschützt" in ergebnis.stderr


@pytest.mark.parametrize(
    "befehl",
    [
        f"git clone {URL} /tmp/x",
        f"git clone --depth 1 {URL} /tmp/x",
        f"git clone {URL}",
        "git clone ~/.claude/skills/to-spawn /tmp/kopie",
        f"git -C /tmp clone {URL}",
        "git init /tmp/x",
        "git init",
        "git -C ~/.claude/skills/to-spawn status",
        "git -C ~/.claude/skills/to-spawn log --oneline -3",
    ],
)
def test_git_clone_init_ausserhalb_ist_erlaubt(home: Path, befehl: str) -> None:
    ergebnis = _hook(home, befehl, home / "repo")
    assert ergebnis.returncode == 0, (befehl, ergebnis.stderr)


@pytest.mark.parametrize(
    "befehl",
    [
        # R5: --git-dir/--work-tree bzw. GIT_DIR/GIT_WORK_TREE zeigen in die Skills.
        "git --git-dir ~/.claude/skills/x init",
        "git --git-dir=~/.claude/skills/x/.git --work-tree=~/.claude/skills/x init",
        "GIT_DIR=~/.claude/skills/x git init",
        "GIT_WORK_TREE=~/.claude/skills/to-spawn GIT_DIR=/tmp/g.git git commit -am x",
        "GIT_DIR=~/.claude/skills/to-spawn/.git git checkout -- .",
        # R6: worktree add / submodule add mit Ziel in den Skills.
        "git worktree add ~/.claude/skills/x main",
        "git worktree add -b neu ~/.claude/skills/x",
        f"git -C ~/repo submodule add {URL} ~/.claude/skills/x",
        f"git submodule add -b main {URL} ~/.claude/skills/x",
        # R7: clone --revision schluckt Wert; globales --attr-source schluckt Wert.
        f"git clone --revision abc123 {URL} ~/.claude/skills/x",
        "git --attr-source HEAD -C ~/.claude/skills/to-spawn commit -am x",
    ],
)
def test_review_r5_r6_r7_umgehungen_werden_verweigert(home: Path, befehl: str) -> None:
    ergebnis = _hook(home, befehl, home / "repo")
    assert ergebnis.returncode == 2, (befehl, ergebnis.stderr)
    assert "Skill-Dateien sind auf dem Bau-Server schreibgeschützt" in ergebnis.stderr


@pytest.mark.parametrize(
    "befehl",
    [
        "git worktree add /tmp/x",
        "git worktree add -b neu /tmp/x main",
        "git worktree list",
        f"git submodule add {URL} vendor/x",
        f"git clone --revision abc123 {URL} /tmp/x",
        "git --attr-source HEAD -C ~/.claude/skills/to-spawn status",
        "GIT_DIR=/tmp/g.git git init",
        "GIT_DIR=~/.claude/skills/to-spawn/.git git log --oneline -3",
        "git --git-dir ~/.claude/skills/to-spawn/.git status",
    ],
)
def test_review_r5_r6_r7_erlaubte_faelle(home: Path, befehl: str) -> None:
    ergebnis = _hook(home, befehl, home / "repo")
    assert ergebnis.returncode == 0, (befehl, ergebnis.stderr)
