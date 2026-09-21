"""Aufpasser (Hausmeister) für Bau-Sessions im tmux — dünner Starter (#236).

Aufruf (Cron alle 15 min oder von Hand):
``python3 ~/.claude/skills/to-spawn/skripte/aufpasser.py [--trocken] [--tmux-socket NAME]
[--zustand DIR] [--hang-min N] [--bau-vorlage "…"] [--deploy-muster REGEX] [--cron-einrichten]``

Die Logik lebt in ``to_spawn/aufpasser.py`` (Regeln, Sicherheitskette, Zustandsordner,
Session-JSON, Respawn, Stufen, Ausschlüsse, ``--hang-min`` ≥ 61). Nur Linux/tmux.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Skill-Wurzel in sys.path, damit ``to_spawn.aufpasser`` importierbar ist (wie wache.py).
_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import aufpasser

if __name__ == "__main__":
    sys.exit(aufpasser.main(sys.argv[1:]))
