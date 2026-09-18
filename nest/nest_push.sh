#!/usr/bin/env bash
# nest_push.sh — schiebt vom Quellrechner (Laptop) alles auf den Server, was nicht ins Repo darf.
#
# Aufruf (Git-Bash/Linux, aus dem Repo-Wurzelordner):
#   bash ~/.claude/skills/to-spawn/nest/nest_push.sh --host-root <ssh-alias> [--repo <ordner>]
#        [--nutzer <name>] [--stage-remote <ordner>] [--skills "<liste>"] [--mcps "<liste>"]
#        [--claude-md <datei>] [--settings <datei>] [--hooks "<liste>"] [--rules "<liste>"]
#        [--memory <ordner>] [--bws-token <datei>] [--ohne-env] [--trocken]
#
# Vorgaben: Skills/MCPs = freigegebene aus <repo>/.to-spawn/werkzeuge.json (sonst alle hier),
# der Skill to-spawn selbst kommt immer mit; CLAUDE.md/settings.json/hooks/rules = die
# lokalen aus ~/.claude. `permissions` aus settings.json werden NIE mitgeschickt (Rechte
# trägt ein Mensch auf dem Server selbst ein). Legt den Nutzer auf dem Server an, falls er fehlt.
# Geheimnisse (Claude-Zugang, gh-Token, .env, MCP-Schlüssel, bws-Token) werden nur
# übertragen, NIE ausgegeben oder geloggt.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_CLAUDE="$HOME/.claude"
HOST_ROOT=""
REPO="."
NUTZER=bau
STAGE_REMOTE=""
SKILLS=""
MCPS=""
CLAUDE_MD="$LOCAL_CLAUDE/CLAUDE.md"
SETTINGS="$LOCAL_CLAUDE/settings.json"
HOOKS=""
RULES=""
MEMORY=""
BWS_TOKEN_DATEI=""
OHNE_ENV=0
TROCKEN=0
SKILLS_GESETZT=0
MCPS_GESETZT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --host-root) HOST_ROOT="${2:-}"; shift 2 ;;
    --repo) REPO="${2:-}"; shift 2 ;;
    --nutzer) NUTZER="${2:-}"; shift 2 ;;
    --stage-remote) STAGE_REMOTE="${2:-}"; shift 2 ;;
    --skills) SKILLS="${2:-}"; SKILLS_GESETZT=1; shift 2 ;;
    --mcps) MCPS="${2:-}"; MCPS_GESETZT=1; shift 2 ;;
    --claude-md) CLAUDE_MD="${2:-}"; shift 2 ;;
    --settings) SETTINGS="${2:-}"; shift 2 ;;
    --hooks) HOOKS="${2:-}"; shift 2 ;;
    --rules) RULES="${2:-}"; shift 2 ;;
    --memory) MEMORY="${2:-}"; shift 2 ;;
    --bws-token) BWS_TOKEN_DATEI="${2:-}"; shift 2 ;;
    --ohne-env) OHNE_ENV=1; shift ;;
    --trocken) TROCKEN=1; shift ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unbekanntes Argument: $1" >&2; exit 2 ;;
  esac
done

ok()   { echo "[ok] $*"; }
warn() { echo "[!!] $*" >&2; }
[ -n "$HOST_ROOT" ] || { warn "Pflicht: --host-root <ssh-alias mit root-Zugang>"; exit 2; }
REPO="$(cd "$REPO" && pwd)"
STAGE_REMOTE="${STAGE_REMOTE:-/home/$NUTZER/stage}"
PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { warn "python fehlt lokal."; exit 1; }
nest_py() { "$PY" "$SKILL_DIR/to_spawn.py" nest "$@"; }

# ---------------------------------------------------------------- Auswahl
if [ "$SKILLS_GESETZT" -eq 0 ]; then
  SKILLS="$(nest_py auswahl --art skill --repo "$REPO" | tr '\n' ' ')"
fi
case " $SKILLS " in
  *" to-spawn "*) ;;
  *) [ -d "$LOCAL_CLAUDE/skills/to-spawn" ] && SKILLS="$SKILLS to-spawn" ;;
esac
if [ "$MCPS_GESETZT" -eq 0 ]; then
  MCPS="$(nest_py auswahl --art mcp --repo "$REPO" | tr '\n' ' ')"
fi
if [ -z "$HOOKS" ] && [ -d "$LOCAL_CLAUDE/hooks" ]; then
  HOOKS="$(ls "$LOCAL_CLAUDE/hooks" | tr '\n' ' ')"
fi
if [ -z "$RULES" ] && [ -d "$LOCAL_CLAUDE/rules" ]; then
  RULES="$(ls "$LOCAL_CLAUDE/rules" | tr '\n' ' ')"
fi

if [ "$TROCKEN" -eq 1 ]; then
  cat <<EOF
Push-Plan (trocken — nichts wird übertragen)
  Server (root):  $HOST_ROOT
  Nutzer:         $NUTZER
  Staging:        $STAGE_REMOTE
  Repo:           $REPO
  Skills:         ${SKILLS:-—}
  MCPs:           ${MCPS:-—}
  Hooks:          ${HOOKS:-—}
  Regeln:         ${RULES:-—}
  CLAUDE.md:      $CLAUDE_MD
  settings.json:  $SETTINGS (ohne permissions)
  Gedächtnis:     ${MEMORY:-—}
  .env:           $( [ "$OHNE_ENV" -eq 1 ] && echo "nein (--ohne-env, bws auf dem Server)" || echo "$REPO/.env" )
  bws-Token:      ${BWS_TOKEN_DATEI:-—}
EOF
  exit 0
fi

# ---------------------------------------------------------------- 1. Voraussetzungen
[ -f "$LOCAL_CLAUDE/.credentials.json" ] || { warn "~/.claude/.credentials.json fehlt — bitte lokal bei Claude anmelden."; exit 1; }
if [ "$OHNE_ENV" -eq 0 ]; then
  [ -f "$REPO/.env" ] || { warn "$REPO/.env fehlt (oder --ohne-env, wenn bws genutzt wird)."; exit 1; }
fi
command -v gh >/dev/null || { warn "gh fehlt lokal."; exit 1; }
ok "Voraussetzungen da"

# ---------------------------------------------------------------- 2. Staging-Kiste bauen
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/claude/hooks" "$STAGE/claude/rules" "$STAGE/claude/skills"
cp "$LOCAL_CLAUDE/.credentials.json" "$STAGE/credentials.json"
gh auth token > "$STAGE/gh_token"
[ "$OHNE_ENV" -eq 0 ] && cp "$REPO/.env" "$STAGE/env"
[ -n "$BWS_TOKEN_DATEI" ] && cp "$BWS_TOKEN_DATEI" "$STAGE/bws_token"

for h in $HOOKS; do
  [ -e "$LOCAL_CLAUDE/hooks/$h" ] && cp -r "$LOCAL_CLAUDE/hooks/$h" "$STAGE/claude/hooks/" || warn "Hook $h fehlt lokal"
done
for r in $RULES; do
  [ -e "$LOCAL_CLAUDE/rules/$r" ] && cp -r "$LOCAL_CLAUDE/rules/$r" "$STAGE/claude/rules/" || warn "Regel $r fehlt lokal"
done
fehlend=""
for s in $SKILLS; do
  if [ -d "$LOCAL_CLAUDE/skills/$s" ]; then
    cp -r "$LOCAL_CLAUDE/skills/$s" "$STAGE/claude/skills/"
  else
    fehlend="$fehlend $s"
  fi
done
[ -n "$fehlend" ] && warn "Skills lokal nicht gefunden:$fehlend"
find "$STAGE/claude/skills" \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache -o -name .git \) -prune -exec rm -rf {} +
if [ -n "$MEMORY" ]; then
  if [ -d "$MEMORY" ]; then
    mkdir -p "$STAGE/claude/memory" && cp -r "$MEMORY/." "$STAGE/claude/memory/"
  else
    warn "Gedächtnis-Ordner $MEMORY fehlt"
  fi
fi
[ -f "$CLAUDE_MD" ] && cp "$CLAUDE_MD" "$STAGE/claude/CLAUDE.md" || warn "CLAUDE.md $CLAUDE_MD fehlt"
if [ -f "$SETTINGS" ]; then
  nest_py einstellungen --quelle "$SETTINGS" --ziel "$STAGE/claude/settings.json" >/dev/null
else
  warn "settings.json $SETTINGS fehlt"
fi
# shellcheck disable=SC2086
nest_py mcp-export --ziel "$STAGE/mcp.json" $MCPS
ok "Staging-Kiste: $(ls "$STAGE/claude/skills" | wc -l) Skills, $(ls "$STAGE/claude/rules" | wc -l) Regeln, $(ls "$STAGE/claude/hooks" | wc -l) Hooks"
for f in credentials.json gh_token env mcp.json bws_token; do
  [ -f "$STAGE/$f" ] && chmod 600 "$STAGE/$f"
done

# ---------------------------------------------------------------- 3. Hochladen
ssh "$HOST_ROOT" "id -u $NUTZER >/dev/null 2>&1 || adduser --disabled-password --gecos Bau-Server $NUTZER >/dev/null; install -d -m 700 -o $NUTZER -g $NUTZER $STAGE_REMOTE && rm -rf $STAGE_REMOTE/claude"
tar -czf - -C "$STAGE" . | ssh "$HOST_ROOT" "tar -xzf - -C $STAGE_REMOTE"   # ein Strom statt vieler scp-Verbindungen
ssh "$HOST_ROOT" "chown -R $NUTZER:$NUTZER $STAGE_REMOTE && chmod 700 $STAGE_REMOTE && for f in credentials.json gh_token env mcp.json bws_token; do [ -f $STAGE_REMOTE/\$f ] && chmod 600 $STAGE_REMOTE/\$f; done; true"
ok "Staging-Kiste liegt auf dem Server unter $STAGE_REMOTE"

echo
ok "Fertig. Jetzt auf dem Server:"
echo "     ssh $HOST_ROOT 'bash $STAGE_REMOTE/claude/skills/to-spawn/nest/nest_server.sh --repo <owner/name>'"
