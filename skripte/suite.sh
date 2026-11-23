#!/usr/bin/env bash
# Volle Skill-Suite — der eine Standard-Aufruf (#353), nur im Klon.
# Ohne Pfad-Angabe sammelt pytest tests/, review/ und skripte/test_*.py; die Wurzel-conftest.py
# (collect_ignore = review-hook, #214) wird nicht installiert, ohne sie bricht das Skript ab.
# -n 4 = vier Worker (pytest-xdist, steht in tests/requirements.txt). Zusätzliche Argumente gehen an pytest (z. B. --durations=25).
# Exit-Code = Exit-Code von pytest (64 = nicht im Klon, kein pytest-Code).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[ -f conftest.py ] || { echo "suite.sh: keine Wurzel-conftest.py — läuft nur im Klon, nicht im installierten Skill." >&2; exit 64; }
exec python -m pytest -q -p no:cacheprovider -n 4 "$@"
