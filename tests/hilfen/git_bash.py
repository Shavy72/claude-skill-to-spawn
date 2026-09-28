"""Bash finden wie Claude Code: Hooks laufen auch unter Windows in Git Bash, nie in cmd.exe."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_WSL_BASH = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "bash.exe"


def _ist_wsl(pfad: Path) -> bool:
    try:
        return pfad.resolve() == _WSL_BASH.resolve()
    except OSError:
        return False


def git_bash() -> str | None:
    """Pfad zur Bash oder ``None``. Reihenfolge: ``CLAUDE_CODE_GIT_BASH_PATH``, Git Bash neben
    ``git``, ``shutil.which("bash")``. Die WSL-Bash in ``System32`` zählt nie."""
    if sys.platform != "win32":
        return shutil.which("bash")
    kandidaten: list[Path] = []
    if env := os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"):
        kandidaten.append(Path(env))
    if git := shutil.which("git"):
        # git liegt in <Git>/cmd, <Git>/bin oder <Git>/mingw64/bin
        for wurzel in list(Path(git).resolve().parents)[:3]:
            kandidaten += [wurzel / "bin" / "bash.exe", wurzel / "usr" / "bin" / "bash.exe"]
    if which := shutil.which("bash"):
        kandidaten.append(Path(which))
    for kandidat in kandidaten:
        if kandidat.is_file() and not _ist_wsl(kandidat):
            return str(kandidat)
    return None
