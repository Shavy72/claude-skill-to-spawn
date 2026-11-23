#!/usr/bin/env bash
# Volle Skill-Suite — der eine Standard-Aufruf (#353).
# Ohne Pfad-Angabe sammelt pytest tests/ und review/; -n 4 = vier Worker (pytest-xdist,
# steht in tests/requirements.txt). Zusätzliche Argumente gehen an pytest (z. B. --durations=25).
# Exit-Code = Exit-Code von pytest.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec python -m pytest -q -p no:cacheprovider -n 4 "$@"
