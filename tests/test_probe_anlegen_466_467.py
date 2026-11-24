"""#466/#467: probe/ wird mitinstalliert, anlegen.sh --github legt main im Fern-Repo an.

Alles in Wegwerf-Ordnern, nie gegen das echte ``~/.claude``; ``gh`` ist eine Attrappe
(legt ein lokales bare-Repo als ``origin`` an), kein Netz.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
GIT = shutil.which("git")
brauche_bash = pytest.mark.skipif(BASH is None or GIT is None, reason="braucht bash und git")


@brauche_bash
def test_install_sh_kopiert_probe(tmp_path: Path) -> None:
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), PYTHONIOENCODING="utf-8",
               TO_SPAWN_PY=sys.executable)
    lauf = subprocess.run(
        [str(BASH), str(SKILL / "install.sh"), "--skills", str(tmp_path / "skills"),
         "--claude-md", str(tmp_path / "CLAUDE.md")],
        env=env, capture_output=True, text=True, encoding="utf-8", check=False, timeout=300,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert (tmp_path / "skills" / "to-spawn" / "probe" / "anlegen.sh").is_file()
    assert (tmp_path / "skills" / "to-spawn" / "probe" / "gemeinsam").is_dir()


def test_install_ps1_nennt_probe() -> None:
    text = (SKILL / "install.ps1").read_text(encoding="utf-8")
    assert '"probe"' in text


@brauche_bash
def test_anlegen_github_pusht_main_mit_startcommit(tmp_path: Path) -> None:
    bin_ordner = tmp_path / "bin"
    bin_ordner.mkdir()
    fern = tmp_path / "fern.git"
    gh = bin_ordner / "gh"
    # Attrappe: `gh repo create <name> --private --source . --remote origin` -> bare-Repo als origin.
    gh.write_text(
        f'#!/usr/bin/env bash\nset -e\ngit init -q --bare "{fern}"\ngit remote add origin "{fern}"\n',
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    ziel = tmp_path / "lauf"
    env = dict(os.environ, PATH=f"{bin_ordner}{os.pathsep}{os.environ['PATH']}")
    lauf = subprocess.run(
        [str(BASH), str(SKILL / "probe" / "anlegen.sh"), str(ziel), "mit-staging",
         "--github", "Shavy72/to-spawn-probe-x-mit"],
        env=env, capture_output=True, text=True, encoding="utf-8", check=False, timeout=60,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    zweige = subprocess.run([str(GIT), "-C", str(fern), "branch", "--list", "main"],
                            capture_output=True, text=True, check=True).stdout
    assert "main" in zweige
    n = subprocess.run([str(GIT), "-C", str(fern), "rev-list", "--count", "main"],
                       capture_output=True, text=True, check=True).stdout.strip()
    assert int(n) >= 1
