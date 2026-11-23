"""Test-Helfer des Review-Werkzeugs (#329).

Eigener Modulname statt ``conftest``: im gemeinsamen Lauf aus der Repo-Wurzel gibt es
zwei ``conftest.py`` (``tests/`` und ``review/tests/``); ``from conftest import …``
würde dort das falsche Modul treffen.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REVIEW = Path(__file__).resolve().parent.parent
BASIS_CONFIG = json.loads((REVIEW / "config.json").read_text(encoding="utf-8"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def lauf(
    skript: str, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REVIEW / skript), *args],
        check=False,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
    )


def route(repo: Path, *args: str) -> dict:
    ergebnis = lauf("review_route.py", "--repo", str(repo), *args)
    assert ergebnis.returncode == 0, ergebnis.stderr
    return json.loads(ergebnis.stdout)
