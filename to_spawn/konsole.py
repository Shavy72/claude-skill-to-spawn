"""Konsolen-Ausgabe der Skill-Skripte: immer UTF-8 (Windows-Pipes sind cp1252)."""

from __future__ import annotations

import sys


def utf8_ausgabe() -> None:
    """Stellt stdout/stderr auf UTF-8 (``errors="replace"``), damit Haken/Umlaute nie abstürzen."""
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(encoding="utf-8", errors="replace")
