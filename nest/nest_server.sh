#!/usr/bin/env bash
# nest_server.sh — macht einen frischen Debian/Ubuntu-Server zum Bau-Server für EIN Repo.
#
# Läuft als root AUF dem Server, idempotent (mehrfach ausführbar). Repo-unabhängig:
# alles Repo-Eigene steht im Ziel-Repo unter .to-spawn/ (config.json, nest_repo.sh).
#
#   bash nest_server.sh --repo <owner/name | git-URL> [--ziel <ordner>] [--nutzer <name>]
#        [--git-name <name>] [--git-mail <mail>] [--stage <ordner>] [--wt-dir <ordner>]
#        [--ohne-werkzeuge] [--trocken]
#
# Reihenfolge beim Neuaufbau:
#   1. nest_push.sh vom Quellrechner (legt Nutzer an, schickt Zugangsdaten, ~/.claude,
#      den Skill und .env in die Staging-Kiste)
#   2. dieses Skript (aus der Staging-Kiste: <stage>/claude/skills/to-spawn/nest/)
#   3. Rechte-Schritt: ein Mensch tippt selbst `… nest rechte --eintragen`
#
# Geheimnisse (Claude-Zugang, gh-Token, bws-Token, .env) werden nur kopiert, nie ausgegeben.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ARG=""
NUTZER=bau
ZIEL=""
GIT_NAME=""
GIT_MAIL=""
STAGE=""
WT_DIR=""
OHNE_WERKZEUGE=0
TROCKEN=0
BWS_VERSION=2.1.0

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO_ARG="${2:-}"; shift 2 ;;
    --ziel) ZIEL="${2:-}"; shift 2 ;;
    --nutzer) NUTZER="${2:-}"; shift 2 ;;
    --git-name) GIT_NAME="${2:-}"; shift 2 ;;
    --git-mail) GIT_MAIL="${2:-}"; shift 2 ;;
    --stage) STAGE="${2:-}"; shift 2 ;;
    --wt-dir) WT_DIR="${2:-}"; shift 2 ;;
    --ohne-werkzeuge) OHNE_WERKZEUGE=1; shift ;;
    --trocken) TROCKEN=1; shift ;;
    -h|--help) sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unbekanntes Argument: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$REPO_ARG" ]; then
  echo "Pflicht: --repo <owner/name | git-URL>" >&2
  exit 2
fi
REPO_NAME="$(basename "${REPO_ARG%.git}")"
NUTZER_HOME="/home/$NUTZER"
ZIEL="${ZIEL:-$NUTZER_HOME/$REPO_NAME}"
STAGE="${STAGE:-$NUTZER_HOME/stage}"
WT_DIR="${WT_DIR:-$NUTZER_HOME/wt}"
SKILL_NUTZER="$NUTZER_HOME/.claude/skills/to-spawn"
# Projekt-Ordnername, den Claude Code für Gedächtnis-Dateien nutzt (/ und . → -)
PROJEKT_SLUG="$(printf '%s' "$ZIEL" | sed 's#[/.]#-#g')"

ok()   { echo "[ok] $*"; }
info() { echo "[..] $*"; }
FEHLER_LISTE=()
warn() { echo "[!!] $*" >&2; FEHLER_LISTE+=("$*"); }
q()    { printf '%q' "$1"; }
asnutzer() { su - "$NUTZER" -c "$1"; }
# Python-Kern des Skills (als root, HOME = Nutzer): JSON, bws, Werkzeuge
nest_py() { HOME="$NUTZER_HOME" BAU_WT_DIR="$WT_DIR" python3 "$SKILL_DIR/to_spawn.py" nest "$@"; }

# ---------------------------------------------------------------- Plan (trocken)
if [ "$TROCKEN" -eq 1 ]; then
  cat <<EOF
Nest-Plan (trocken — nichts wird geändert)
  Repo:       $REPO_ARG
  Ziel:       $ZIEL
  Nutzer:     $NUTZER ($NUTZER_HOME)
  Git-Name:   ${GIT_NAME:-(aus gh api user beim echten Lauf)}
  Git-Mail:   ${GIT_MAIL:-(aus gh api user beim echten Lauf)}
  Staging:    $STAGE
  Worktrees:  $WT_DIR
  Skill:      $SKILL_DIR
Schritte:
   1. Grundpakete (git tmux curl jq rsync python3 unzip …)
   2. Nutzer $NUTZER + sudo ohne Passwort + SSH-Schlüssel von root
   3. Node 22
   4. GitHub CLI (gh)
   5. tmux.conf + .bashrc (REPO=$ZIEL, BAU_WT_DIR=$WT_DIR, bau/wache/sessions)
   6. Zugangsdaten aus $STAGE (Claude, gh, bws-Token)
   7. ~/.claude aus $STAGE ergänzen (ohne Löschen), Skill to-spawn nach $SKILL_NUTZER
   8. Claude Code + uv
   9. ~/.claude.json: Erststart übersprungen, Vertrauen für $ZIEL und $WT_DIR
  10. Klon $REPO_ARG → $ZIEL, git config, gh auth setup-git
  11. Sandbox-Unterbau (bubblewrap socat ripgrep srt bws $BWS_VERSION) + ~/.ssh/config-Block
  12. .env: erst bws (nest secrets), sonst $STAGE/env
  13. Werkzeuge: $( [ "$OHNE_WERKZEUGE" -eq 1 ] && echo "übersprungen (--ohne-werkzeuge)" || echo "nest werkzeuge --installieren" ), MCPs aus $STAGE/mcp.json
  14. Repo-eigener Schritt: $ZIEL/.to-spawn/nest_repo.sh (falls vorhanden, als $NUTZER)
  15. Rechte-Schritt: nest rechte zeigen — eintragen nur durch einen Menschen
EOF
  exit 0
fi

[ "$(id -u)" -eq 0 ] || { warn "Bitte als root ausführen."; exit 1; }

# ---------------------------------------------------------------- 1. Grundpakete
export DEBIAN_FRONTEND=noninteractive
need_pkgs=()
for p in git tmux curl wget jq rsync build-essential ca-certificates gnupg sudo python3 unzip; do
  dpkg -s "$p" >/dev/null 2>&1 || need_pkgs+=("$p")
done
if [ ${#need_pkgs[@]} -gt 0 ]; then
  apt-get update -qq
  apt-get install -y -qq "${need_pkgs[@]}"
fi
ok "Grundpakete vollständig (${#need_pkgs[@]} nachinstalliert)"

# ---------------------------------------------------------------- 2. Nutzer
if ! id -u "$NUTZER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "Bau-Server" "$NUTZER" >/dev/null
fi
usermod -aG sudo "$NUTZER"
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$NUTZER" > "/etc/sudoers.d/90-$NUTZER"
chmod 440 "/etc/sudoers.d/90-$NUTZER"
install -d -m 700 -o "$NUTZER" -g "$NUTZER" "$NUTZER_HOME/.ssh"
if [ -f /root/.ssh/authorized_keys ] && [ ! -f "$NUTZER_HOME/.ssh/authorized_keys" ]; then
  install -m 600 -o "$NUTZER" -g "$NUTZER" /root/.ssh/authorized_keys "$NUTZER_HOME/.ssh/authorized_keys"
fi
install -d -m 700 -o "$NUTZER" -g "$NUTZER" "$STAGE"
install -d -m 755 -o "$NUTZER" -g "$NUTZER" "$WT_DIR"
ok "Nutzer $NUTZER mit sudo ohne Passwort + SSH-Schlüssel"

# ---------------------------------------------------------------- 3. Node 22 LTS
if ! command -v node >/dev/null 2>&1 || [ "$(node -v | cut -c2-3)" -lt 22 ] 2>/dev/null; then
  info "Node 22 über NodeSource installieren"
  if curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource.sh && bash /tmp/nodesource.sh >/dev/null 2>&1; then
    apt-get install -y -qq nodejs
  else
    warn "NodeSource nicht verfügbar — Debian-Paket nodejs wird genommen"
    apt-get install -y -qq nodejs npm
  fi
fi
ok "Node $(node -v) / npm $(npm -v 2>/dev/null || echo '-')"

# ---------------------------------------------------------------- 4. GitHub CLI
if ! command -v gh >/dev/null 2>&1; then
  install -d -m 755 /etc/apt/keyrings
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
  chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -qq
  apt-get install -y -qq gh
fi
ok "gh $(gh --version | head -1 | awk '{print $3}')"

# ---------------------------------------------------------------- 5. tmux + bashrc
if [ ! -f "$NUTZER_HOME/.tmux.conf" ]; then
  cat > "$NUTZER_HOME/.tmux.conf" <<'EOF'
set -g mouse on
set -g history-limit 50000
set -g default-terminal "screen-256color"
setw -g mode-keys vi
EOF
  chown "$NUTZER:$NUTZER" "$NUTZER_HOME/.tmux.conf"
fi

if ! grep -q -e '>>> to-spawn nest <<<' -e '>>> bau-server <<<' "$NUTZER_HOME/.bashrc" 2>/dev/null; then
  cat >> "$NUTZER_HOME/.bashrc" <<'EOF'

# >>> to-spawn nest <<<
# nur für interaktive Shells: ins Repo wechseln + venv anschalten (falls vorhanden)
if [ -d "$REPO" ]; then
  cd "$REPO"
  [ -f .venv/bin/activate ] && . .venv/bin/activate
fi
# <<< to-spawn nest >>>
EOF
fi

# Kopf der .bashrc: Debians Vorlage steigt bei nicht-interaktiven Shells früh aus.
# PATH und bau/wache/sessions müssen davor stehen, sonst findet `ssh <server> bau 42` nichts.
if ! grep -q -e 'to-spawn Nest Kopf' -e 'bau-server Kopf' "$NUTZER_HOME/.bashrc" 2>/dev/null; then
  {
    echo "# to-spawn Nest Kopf — vor Debians Ausstieg für nicht-interaktive Shells"
    echo 'export PATH="$HOME/.local/bin:$HOME/.claude/local:$PATH"'
    echo "export REPO=$(q "$ZIEL")"
    echo "export BAU_WT_DIR=$(q "$WT_DIR")"
    cat <<'EOF'
_bau_py() {  # bau/wache/sessions: Weiterleitung im Repo, sonst direkt der Skill
  local skript="$1"; shift
  local py=python3
  [ -x "$REPO/.venv/bin/python" ] && py="$REPO/.venv/bin/python"
  if [ -f "$REPO/scripts/$skript" ]; then
    ( cd "$REPO" && "$py" "scripts/$skript" "$@" )
  elif [ -f "$HOME/.claude/skills/to-spawn/skripte/$skript" ]; then
    ( cd "$REPO" && TO_SPAWN_REPO="$REPO" "$py" "$HOME/.claude/skills/to-spawn/skripte/$skript" "$@" )
  else
    echo "$skript weder in $REPO/scripts noch im Skill to-spawn gefunden" >&2; return 1
  fi
}
bau()      { _bau_py bau.py "$@"; }
wache()    { _bau_py wache.py "$@"; }
sessions() { _bau_py sessions_stand.py "$@"; }

EOF
    cat "$NUTZER_HOME/.bashrc" 2>/dev/null || true
  } > "$NUTZER_HOME/.bashrc.neu" && mv "$NUTZER_HOME/.bashrc.neu" "$NUTZER_HOME/.bashrc"
fi
chown "$NUTZER:$NUTZER" "$NUTZER_HOME/.bashrc"
ok "tmux.conf + .bashrc (bau/wache/sessions, cd ins Repo)"

# ---------------------------------------------------------------- 6. Zugangsdaten aus der Staging-Kiste
install -d -m 700 -o "$NUTZER" -g "$NUTZER" "$NUTZER_HOME/.claude" "$NUTZER_HOME/.config"
if [ -f "$STAGE/credentials.json" ]; then
  install -m 600 -o "$NUTZER" -g "$NUTZER" "$STAGE/credentials.json" "$NUTZER_HOME/.claude/.credentials.json"
  ok "Claude-Zugang gesetzt"
elif [ ! -f "$NUTZER_HOME/.claude/.credentials.json" ]; then
  warn "$STAGE/credentials.json fehlt — Claude-Login noch offen (nest_push.sh laufen lassen)"
fi
rm -f "$STAGE/credentials.json"

gh_login=""
if [ -f "$STAGE/gh_token" ]; then
  gh_login="$(GH_TOKEN="$(cat "$STAGE/gh_token")" gh api user --jq .login 2>/dev/null || true)"
fi
if [ -f "$STAGE/gh_token" ] && [ -z "$gh_login" ]; then
  warn "gh-Token aus $STAGE ungültig (kein Login) — hosts.yml NICHT geschrieben"
elif [ -f "$STAGE/gh_token" ]; then
  if [ -z "$GIT_NAME" ]; then GIT_NAME="$gh_login"; fi
  if [ -z "$GIT_MAIL" ]; then
    GIT_MAIL="$(GH_TOKEN="$(cat "$STAGE/gh_token")" gh api user \
      --jq '"\(.id)+\(.login)@users.noreply.github.com"' 2>/dev/null || true)"
  fi
  install -d -m 700 -o "$NUTZER" -g "$NUTZER" "$NUTZER_HOME/.config/gh"
  umask 177
  { printf 'github.com:\n'
    printf '    oauth_token: %s\n' "$(cat "$STAGE/gh_token")"
    printf '    user: %s\n' "$gh_login"
    printf '    git_protocol: https\n'
  } > "$NUTZER_HOME/.config/gh/hosts.yml"
  umask 22
  chown -R "$NUTZER:$NUTZER" "$NUTZER_HOME/.config/gh"
  chmod 600 "$NUTZER_HOME/.config/gh/hosts.yml"
  ok "gh-Zugang gesetzt"
elif [ ! -f "$NUTZER_HOME/.config/gh/hosts.yml" ]; then
  warn "$STAGE/gh_token fehlt — gh-Login noch offen"
fi
rm -f "$STAGE/gh_token"

if [ -f "$STAGE/bws_token" ]; then
  install -d -m 700 -o "$NUTZER" -g "$NUTZER" "$NUTZER_HOME/.config/to-spawn"
  install -m 600 -o "$NUTZER" -g "$NUTZER" "$STAGE/bws_token" "$NUTZER_HOME/.config/to-spawn/bws_token"
  ok "bws-Token gesetzt (Inhalt bleibt verdeckt)"
fi
rm -f "$STAGE/bws_token"
[ -n "$GIT_NAME" ] && [ -n "$GIT_MAIL" ] \
  || warn "Git-Name/-Mail unbekannt — mit --git-name/--git-mail erneut laufen lassen"

# ---------------------------------------------------------------- 7. ~/.claude aus der Staging-Kiste (ergänzen, nie löschen)
if [ -d "$STAGE/claude" ]; then
  for teil in hooks rules skills agents commands; do
    if [ -d "$STAGE/claude/$teil" ]; then
      install -d "$NUTZER_HOME/.claude/$teil"
      rsync -a "$STAGE/claude/$teil/" "$NUTZER_HOME/.claude/$teil/"
    fi
  done
  [ -f "$STAGE/claude/CLAUDE.md" ] && install -m 644 "$STAGE/claude/CLAUDE.md" "$NUTZER_HOME/.claude/CLAUDE.md"
  if [ -f "$STAGE/claude/settings.json" ]; then
    nest_py einstellungen --quelle "$STAGE/claude/settings.json" --ziel "$NUTZER_HOME/.claude/settings.json" >/dev/null
  fi
  # Windows-Zeilenenden entfernen (sonst scheitern Hook-Shebangs)
  find "$NUTZER_HOME/.claude/hooks" "$NUTZER_HOME/.claude/rules" "$NUTZER_HOME/.claude/skills" \
       -type f \( -name '*.sh' -o -name '*.mjs' -o -name '*.md' -o -name '*.json' -o -name '*.js' -o -name '*.py' \) \
       -exec sed -i 's/\r$//' {} + 2>/dev/null || true
  chmod +x "$NUTZER_HOME/.claude/hooks/"*.sh 2>/dev/null || true
  if [ -d "$STAGE/claude/memory" ]; then
    MEM="$NUTZER_HOME/.claude/projects/$PROJEKT_SLUG/memory"
    install -d "$MEM"
    rsync -a "$STAGE/claude/memory/" "$MEM/"
  fi
  ok "~/.claude ergänzt ($(ls "$NUTZER_HOME/.claude/skills" 2>/dev/null | wc -l) Skills)"
else
  warn "$STAGE/claude fehlt — ~/.claude noch leer"
fi
# Fehlt der Skill oder weicht er vom laufenden Stand ab → neu installieren (alter Stand nach _alt/).
if [ "$(cd "$SKILL_DIR" && pwd -P)" != "$(cd "$SKILL_NUTZER" 2>/dev/null && pwd -P)" ] \
   && ! ( for teil in SKILL.md to_spawn.py to_spawn skripte nest; do
          diff -rq -x __pycache__ "$SKILL_DIR/$teil" "$SKILL_NUTZER/$teil" >/dev/null 2>&1 || exit 1
        done ); then
  bash "$SKILL_DIR/install.sh" --skills "$NUTZER_HOME/.claude/skills" >/dev/null
fi
chown -R "$NUTZER:$NUTZER" "$NUTZER_HOME/.claude"
ok "Skill to-spawn: $SKILL_NUTZER"

# ---------------------------------------------------------------- 8. Claude Code + uv (als Nutzer)
if [ ! -x "$NUTZER_HOME/.local/bin/claude" ] && ! asnutzer 'command -v claude >/dev/null'; then
  asnutzer 'curl -fsSL https://claude.ai/install.sh | bash' >/dev/null
fi
if claude_version="$(asnutzer 'claude --version' 2>/dev/null)"; then
  ok "Claude Code $claude_version"
else
  warn "Claude Code fehlt (Installation gescheitert)"
fi
if ! asnutzer 'command -v uv >/dev/null'; then
  asnutzer 'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null
fi
ok "uv $(asnutzer 'uv --version' 2>/dev/null | awk '{print $2}')"
# Aufpasser (#236): Cron-Hausmeister für die tmux-Fenster, alle 15 min, idempotent.
if asnutzer "python3 $(q "$SKILL_NUTZER/skripte/aufpasser.py") --cron-einrichten" >/dev/null 2>&1; then
  ok "Aufpasser-Cron (alle 15 min)"
else
  warn "Aufpasser-Cron nicht eingerichtet (crontab fehlt oder Skill nicht lesbar)"
fi

# ---------------------------------------------------------------- 9. ~/.claude.json: Erststart + Vertrauen
nest_py onboarding --claude-json "$NUTZER_HOME/.claude.json" --trust "$ZIEL" --trust "$WT_DIR"
chown "$NUTZER:$NUTZER" "$NUTZER_HOME/.claude.json"
ok "~/.claude.json: Erststart übersprungen, $ZIEL + $WT_DIR vertraut"

# ---------------------------------------------------------------- 10. Klon + git config
asnutzer "gh auth setup-git" >/dev/null 2>&1 || warn "gh auth setup-git fehlgeschlagen (git push über https braucht es)"
if [ -n "$GIT_NAME" ] && [ -n "$GIT_MAIL" ]; then
  asnutzer "git config --global user.name $(q "$GIT_NAME")"
  asnutzer "git config --global user.email $(q "$GIT_MAIL")"
fi
asnutzer "git config --global --get-all safe.directory | grep -qxF $(q "$ZIEL") || git config --global --add safe.directory $(q "$ZIEL")"
if [ ! -d "$ZIEL/.git" ]; then
  case "$REPO_ARG" in
    *://*|git@*) asnutzer "git clone -q $(q "$REPO_ARG") $(q "$ZIEL")" ;;
    *) asnutzer "gh repo clone $(q "$REPO_ARG") $(q "$ZIEL") -- -q" ;;
  esac
else
  asnutzer "cd $(q "$ZIEL") && git fetch -q origin" || warn "git fetch im Repo gescheitert"
  # Hauptbaum nur vorspulen, wenn er sauber ist — sonst bleibt fremde Arbeit unberührt.
  if [ -z "$(asnutzer "cd $(q "$ZIEL") && git status --porcelain")" ]; then
    asnutzer "cd $(q "$ZIEL") && git merge -q --ff-only @{u}" \
      || warn "Repo nicht fast-forward — bitte von Hand ansehen"
  else
    info "Hauptbaum hat Änderungen — nur git fetch, kein Vorspulen"
  fi
fi
ok "Repo: $(asnutzer "cd $(q "$ZIEL") && git log -1 --oneline")"

# ---------------------------------------------------------------- 11. Sandbox-Unterbau + ssh durch die Sandbox
sb_pkgs=()
for p in bubblewrap socat ripgrep; do dpkg -s "$p" >/dev/null 2>&1 || sb_pkgs+=("$p"); done
if [ ${#sb_pkgs[@]} -gt 0 ]; then apt-get install -y -qq "${sb_pkgs[@]}"; fi
command -v srt >/dev/null 2>&1 || npm install -g --silent @anthropic-ai/sandbox-runtime >/dev/null
if ! bws --version 2>/dev/null | grep -qF "$BWS_VERSION"; then
  case "$(uname -m)" in
    x86_64) bws_arch=x86_64 ;;
    aarch64|arm64) bws_arch=aarch64 ;;
    *) bws_arch="" ;;
  esac
  if [ -n "$bws_arch" ]; then
    bws_tmp="$(mktemp -d)"
    curl -fsSL -o "$bws_tmp/bws.zip" \
      "https://github.com/bitwarden/sdk-sm/releases/download/bws-v$BWS_VERSION/bws-$bws_arch-unknown-linux-gnu-$BWS_VERSION.zip" \
      && unzip -q -o "$bws_tmp/bws.zip" -d "$bws_tmp" \
      && install -m 755 "$bws_tmp/bws" /usr/local/bin/bws \
      || warn "bws $BWS_VERSION nicht installiert"
    rm -rf "$bws_tmp"
  else
    warn "bws: unbekannte Architektur $(uname -m)"
  fi
fi
SSH_CFG="$NUTZER_HOME/.ssh/config"
touch "$SSH_CFG"
# Alter Block (prüfte HTTPS_PROXY statt der eigenen Kennung) → entfernen, unten neu
if grep -q 'HTTPS_PROXY\$https_proxy' "$SSH_CFG"; then
  sed -i '/>>> to-spawn Sandbox >>>/,/<<< to-spawn Sandbox <<</d' "$SSH_CFG"
fi
if ! grep -q '>>> to-spawn Sandbox >>>' "$SSH_CFG"; then
  {
    echo "# >>> to-spawn Sandbox >>>  (ssh in srt nur über deren Proxy, siehe nest/ssh_durch_sandbox.sh)"
    echo 'Match exec "test -n \"$TO_SPAWN_SANDBOX\""'
    echo "    ProxyCommand $SKILL_NUTZER/nest/ssh_durch_sandbox.sh %h %p"
    echo "Match all"
    echo "# <<< to-spawn Sandbox <<<"
    echo
    cat "$SSH_CFG"
  } > "$SSH_CFG.neu" && mv "$SSH_CFG.neu" "$SSH_CFG"
fi
chown "$NUTZER:$NUTZER" "$SSH_CFG"; chmod 600 "$SSH_CFG"
for befehl in srt bwrap socat rg bws; do
  command -v "$befehl" >/dev/null 2>&1 || warn "Sandbox-Unterbau: $befehl fehlt"
done
ok "Sandbox-Unterbau geprüft: srt $(srt --version 2>/dev/null) · bws $(bws --version 2>/dev/null | awk '{print $2}')"

# ---------------------------------------------------------------- 12. .env: erst bws, sonst Staging
env_da=0
if command -v bws >/dev/null 2>&1 && { [ -n "${BWS_ACCESS_TOKEN:-}" ] || [ -f "$NUTZER_HOME/.config/to-spawn/bws_token" ]; }; then
  if nest_py secrets --ziel "$ZIEL/.env"; then env_da=1; else warn "bws-Abruf gescheitert — .env aus der Staging-Kiste"; fi
fi
if [ "$env_da" -eq 0 ] && [ -f "$STAGE/env" ]; then
  if [ -f "$ZIEL/.env" ]; then
    info "Im Repo liegt schon eine .env — Staging-.env wird NICHT darübergelegt"
  else
    install -m 600 "$STAGE/env" "$ZIEL/.env"
  fi
fi
rm -f "$STAGE/env"
[ -f "$ZIEL/.env" ] && env_da=1
if [ "$env_da" -eq 1 ]; then
  chown "$NUTZER:$NUTZER" "$ZIEL/.env"; chmod 600 "$ZIEL/.env"
  ok ".env im Repo ($(wc -l < "$ZIEL/.env") Zeilen, Inhalt bleibt verdeckt)"
else
  warn "keine .env: weder bws (Token + Programm) noch $STAGE/env"
fi

# ---------------------------------------------------------------- 13. Werkzeuge + MCPs
if [ "$OHNE_WERKZEUGE" -eq 0 ]; then
  nest_py werkzeuge --installieren --repo "$ZIEL" || warn "Werkzeuge: etwas fehlt (Tabelle oben)"
else
  info "Werkzeuge übersprungen (--ohne-werkzeuge)"
fi
if [ -f "$STAGE/mcp.json" ]; then
  # direkt in ~/.claude.json mischen (0600) — Schlüssel stehen nie in argv/ps
  if nest_py mcp-import --quelle "$STAGE/mcp.json" --claude-json "$NUTZER_HOME/.claude.json"; then
    chown "$NUTZER:$NUTZER" "$NUTZER_HOME/.claude.json"
  else
    warn "MCPs nicht eingetragen"
  fi
fi
rm -f "$STAGE/mcp.json"

# ---------------------------------------------------------------- 14. Repo-eigener Schritt
if [ -f "$ZIEL/.to-spawn/nest_repo.sh" ]; then
  info "Repo-eigener Schritt: $ZIEL/.to-spawn/nest_repo.sh"
  asnutzer "REPO=$(q "$ZIEL") NUTZER=$(q "$NUTZER") WT_DIR=$(q "$WT_DIR") bash $(q "$ZIEL/.to-spawn/nest_repo.sh")" \
    || warn "nest_repo.sh endete mit Fehler — Ausgabe oben ansehen"
else
  info "kein .to-spawn/nest_repo.sh im Repo — kein Repo-eigener Schritt"
fi

# ---------------------------------------------------------------- 15. Rechte-Schritt (nur Anzeige)
echo
asnutzer "BAU_WT_DIR=$(q "$WT_DIR") python3 $(q "$SKILL_NUTZER/to_spawn.py") nest rechte" || true
echo
if [ ${#FEHLER_LISTE[@]} -gt 0 ]; then
  echo "[!!] Nest NICHT fertig — ${#FEHLER_LISTE[@]} Fehler:" >&2
  for fehler in "${FEHLER_LISTE[@]}"; do echo "     - $fehler" >&2; done
  echo "     Beheben und dieses Skript erneut laufen lassen (idempotent)." >&2
  exit 3
fi
ok "Nest steht. Letzter Schritt macht ein MENSCH selbst: als $NUTZER einloggen und"
echo "     python3 $SKILL_NUTZER/to_spawn.py nest rechte --eintragen"
echo "     tippen. Danach: tmux → bau <N>"
