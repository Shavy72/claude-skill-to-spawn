"""Welche ``bash`` der Skill startet — einzige Stelle dieses Wissens (#68).

Warum: Unter Windows trifft ein nacktes ``bash`` zuerst ``System32\\bash.exe`` (WSL-Stub ohne
``/bin/bash``), weil ``CreateProcess`` ``System32`` vor ``PATH`` absucht. Der Skill braucht dort
Git-Bash. Zweite Falle: Claude Code vererbt ``MSYS_NO_PATHCONV=1``; dann reicht Git-Bash
``/c/…``-Pfade (etwa aus ``~``) unverändert an Windows-Programme wie ``node``/``python``, die
sie als ``C:\\c\\…`` missverstehen. Linux/macOS: ``bash`` aus dem ``PATH``, Umgebung unverändert.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

log = logging.getLogger("to_spawn.bash_wahl")

#: Standard-Installationsort von Git für Windows.
_GIT_BASH_STANDARD = "C:/Program Files/Git/bin/bash.exe"
#: Schalter, mit denen MSYS die Pfad-Umsetzung für Windows-Programme abschaltet.
_PFAD_SPERREN = ("MSYS_NO_PATHCONV", "MSYS2_ARG_CONV_EXCL")


def bash() -> str:
    """Programm für jeden ``bash``-Aufruf des Skills.

    Windows: Git-Bash aus ``CLAUDE_CODE_GIT_BASH_PATH``, ``GIT_BASH``, dem Standardpfad oder
    neben ``git``. Fehlt sie → ``FileNotFoundError`` mit Anleitung, nie stiller Rückfall auf
    WSL. Sonst: ``bash`` aus dem ``PATH`` (fehlt sie, scheitert erst der Start, wie bisher).
    """
    if sys.platform != "win32":
        return shutil.which("bash") or "bash"
    kandidaten = [
        os.environ.get("CLAUDE_CODE_GIT_BASH_PATH") or "",
        os.environ.get("GIT_BASH") or "",
        _GIT_BASH_STANDARD,
    ]
    git = shutil.which("git")
    if git:
        kandidaten += [str(p / "bin" / "bash.exe") for p in Path(git).resolve().parents[:3]]
    for kandidat in kandidaten:
        if kandidat and Path(kandidat).is_file():
            return kandidat
    log.error("Git-Bash nicht gefunden, geprüft: %s", [k for k in kandidaten if k])
    raise FileNotFoundError(
        "Git-Bash nicht gefunden (Git für Windows installieren oder GIT_BASH auf bash.exe setzen)"
        " — die WSL-bash aus System32 taugt nicht"
    )


def bash_umgebung(basis: Mapping[str, str]) -> dict[str, str]:
    """Umgebung für einen Git-Bash-Kindprozess: Windows ohne MSYS-Pfadsperren, sonst Kopie."""
    if sys.platform != "win32":
        return dict(basis)
    return {k: v for k, v in basis.items() if k.upper() not in _PFAD_SPERREN}

