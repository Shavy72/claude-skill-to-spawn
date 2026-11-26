#!/usr/bin/env bash
# Volle Skill-Suite — der eine Standard-Aufruf (#353, #197), nur im Klon.
# Ohne Pfad-Angabe sammelt pytest tests/, review/ und skripte/test_*.py; die Wurzel-conftest.py
# (collect_ignore = ["review-hook", "probe"], #214/#426) wird nicht installiert, ohne sie bricht das Skript ab.
# Windows: delegiert an skripte/suite.py (je Testdatei im Job-Objekt über to_spawn/sitzungsjob.py).
# Linux/macOS: -n <4..8> --dist worksteal = Worker nach CPU-Kernen (min. 4, max. 8; z. B. 8 auf 8-Kern-Bau-Server;
# per TO_SPAWN_SUITE_WORKER oder -n <N> überschreibbar; pytest-xdist steht in tests/requirements.txt).
# Ohne vorgegebenes TMPDIR lenkt der Linux-Teil Test-Temp über to_spawn.temp_ordner.pfad() auf die Platte statt tmpfs (#355/#136)
# und räumt den eigenen pt-*-Basetemp auch bei Exit != 0 ab (TO_SPAWN_SUITE_BASETEMP_RAEUMEN=0 hält ihn zum Nachsehen).
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
export TO_SPAWN_SUITE_BASETEMP_RAEUMEN="${TO_SPAWN_SUITE_BASETEMP_RAEUMEN:-1}"
_SUITE_WORKER="${TO_SPAWN_SUITE_WORKER:-}"
case "$_SUITE_WORKER" in
    ''|*[!0-9]*|0)
        _KERNE="$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
        case "$_KERNE" in ''|*[!0-9]*) _KERNE=4 ;; esac
        if [ "$_KERNE" -lt 4 ]; then
            _SUITE_WORKER=4
        elif [ "$_KERNE" -gt 8 ]; then
            _SUITE_WORKER=8
        else
            _SUITE_WORKER="$_KERNE"
        fi
        ;;
esac
exec python -m pytest -q -p no:cacheprovider -n "$_SUITE_WORKER" --dist worksteal "$@"
