"""Weiterleitung auf den Skill to-spawn (#205, Wächter-Tick #213).

Die Logik liegt in ``$TO_SPAWN_HOME/skripte/capo.py`` (Vorgabe
``~/.claude/skills/to-spawn/skripte/capo.py``), das Repo wird als ``TO_SPAWN_REPO``
weitergereicht. Aufruf und Optionen: ``python scripts/capo.py --help``.
"""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_to_spawn_weiterleitung", Path(__file__).resolve().with_name("_to_spawn_weiterleitung.py")
)
_helfer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helfer)
_helfer.weiterleiten("capo", __name__, globals())
