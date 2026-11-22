"""Bash-Pfad für Tests — fragt die eine Tür ``to_spawn.bash_wahl`` (keine eigene Suche, #68)."""

from __future__ import annotations

import sys
from pathlib import Path

_SKILL = str(Path(__file__).resolve().parents[2])
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)

from to_spawn import bash_wahl  # noqa: E402


def git_bash() -> str | None:
    """Pfad der bash aus ``bash_wahl.bash()`` (Windows nur Git-Bash, nie WSL) oder ``None``."""
    try:
        return bash_wahl.bash()
    except FileNotFoundError:
        return None
