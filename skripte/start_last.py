"""Messbefehl für die Start-Last je Fenstertyp (Bau-Session und Aufseher, #412) — dünner Starter.

Aufruf:
``python skripte/start_last.py [--repo <pfad>] [--spec <S>] [--ticket <N>] [--pruefen] [--json]``

Die Logik lebt allein in ``to_spawn/start_last.py`` (gleiche Argumente wie
``to_spawn.py start-last``).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import start_last  # noqa: E402

if __name__ == "__main__":
    sys.exit(start_last.main(sys.argv[1:]))
