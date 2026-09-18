#!/usr/bin/env bash
# Weiterleitung auf den Skill to-spawn (#205): Logik in $TO_SPAWN_HOME/skripte/spawn_srv.sh
# (Vorgabe ~/.claude/skills/to-spawn). Aufruf wie bisher: bash scripts/spawn_srv.sh <S> [--help].
set -euo pipefail
TO_SPAWN_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export TO_SPAWN_REPO
SKILL="${TO_SPAWN_HOME:-$HOME/.claude/skills/to-spawn}"
if [ ! -f "$SKILL/skripte/spawn_srv.sh" ]; then
  echo "WEIGERUNG: Skill to-spawn fehlt ($SKILL/skripte/spawn_srv.sh) — Skill to-spawn installieren (github.com/Shavy72/claude-skill-to-spawn, install.sh bzw. install.ps1), dann erneut." >&2
  exit 3
fi
exec bash "$SKILL/skripte/spawn_srv.sh" "$@"
