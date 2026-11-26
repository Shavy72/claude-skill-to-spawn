#!/usr/bin/env bash
# Volle Skill-Suite — der eine Standard-Aufruf (#353, #197), nur im Klon.
# Ohne Pfad-Angabe sammelt pytest tests/, review/ und skripte/test_*.py; die Wurzel-conftest.py
# (collect_ignore = ["review-hook", "probe"], #214/#426) wird nicht installiert, ohne sie bricht das Skript ab.
# Windows: delegiert an skripte/suite.py (je Testdatei im Job-Objekt über to_spawn/sitzungsjob.py).
# Linux/macOS: -n 4 --dist worksteal = vier Worker mit Arbeits-Ausgleich (pytest-xdist, steht in tests/requirements.txt).
# Ohne vorgegebenes TMPDIR lenkt der Linux-Teil Test-Temp über to_spawn.temp_ordner.pfad() auf die Platte statt tmpfs (#355/#136).
# Zusätzliche Argumente gehen an pytest (z. B. --durations=25 oder -n 6 zum Überschreiben der Worker-Zahl).
# Exit-Code = Exit-Code von pytest (64 = nicht im Klon, kein pytest-Code).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[ -f conftest.py ] || { echo "suite.sh: keine Wurzel-conftest.py — läuft nur im Klon, nicht im installierten Skill." >&2; exit 64; }
case "$(uname -s 2>/dev/null || true)" in
    MINGW*|MSYS*|CYGWIN*)
        if [ -f skripte/suite.py ]; then
            exec python skripte/suite.py "$@"
        fi
        ;;
esac
if [ -z "${TMPDIR:-}" ] && [ -f to_spawn/temp_ordner.py ]; then
    _TO_SPAWN_TEMP="$(python -c 'from to_spawn import temp_ordner; print(temp_ordner.pfad())' 2>/dev/null || true)"
    if [ -n "$_TO_SPAWN_TEMP" ] && [ -d "$_TO_SPAWN_TEMP" ]; then
        export TMPDIR="$_TO_SPAWN_TEMP" TEMP="$_TO_SPAWN_TEMP" TMP="$_TO_SPAWN_TEMP"
    fi
fi
exec python -m pytest -q -p no:cacheprovider -n 4 --dist worksteal "$@"
