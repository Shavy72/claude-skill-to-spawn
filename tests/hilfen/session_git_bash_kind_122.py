"""Session-Ersatz für #122: startet ein Git-Bash-Kind und wartet darauf.

Aufruf: ``python session_git_bash_kind_122.py <bash> <pid-datei>``. Schreibt
``<eigene PID> <Windows-PID von sleep>`` in die Datei, dann blockiert es wie eine Session.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

bash, datei = sys.argv[1], Path(sys.argv[2])
skript = 'sleep 37 & echo "$PPID_SELBST $(cat /proc/$!/winpid)" > "$1.tmp"; mv "$1.tmp" "$1"; wait'
umgebung = dict(os.environ, PPID_SELBST=str(os.getpid()))
sys.exit(subprocess.run([bash, "-c", skript, "_", str(datei)], env=umgebung, check=False).returncode)
