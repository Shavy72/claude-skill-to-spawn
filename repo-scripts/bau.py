"""Weiterleitung auf den Skill to-spawn (#205).

Die Logik liegt in ``$TO_SPAWN_HOME/skripte/bau.py`` (Vorgabe
``~/.claude/skills/to-spawn/skripte/bau.py``), das Repo wird als ``TO_SPAWN_REPO``
weitergereicht. Aufruf und Optionen wie bisher: ``python scripts/bau.py --help``.
"""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_to_spawn_weiterleitung", Path(__file__).resolve().with_name("_to_spawn_weiterleitung.py")
)
_helfer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helfer)
_helfer.weiterleiten("bau", __name__, globals())
