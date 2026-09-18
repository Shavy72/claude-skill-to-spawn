#!/usr/bin/env bash
# Weg-Test #212 — echter Umzug einer laufenden Bau-Session „lokal“ → Bau-Server.
#
# Keine Attrappen: echte Claude-Session (Haiku, billig), echtes bau.py, echter Stop-Hook,
# echter Skill-Aufruf /to-spawn-of (per tmux send-keys, wie ein Mensch tippt), echtes
# git push, echtes ssh, echtes spawn_srv.sh + tmux auf der Server-Seite.
# Einzige Vereinfachung: „lokal“ und „Server“ sind derselbe Rechner (ssh localhost),
# die beiden Seiten sind getrennte Klone eines Wegwerf-Repos.
#
# Aufruf:  bash tests/weg/weg_umzug_212.sh <arbeitsordner>
# Voraussetzung: tmux, claude (eingeloggt), ssh localhost ohne Passwort,
#                Alias-Skill ~/.claude/skills/to-spawn-of installiert.
# Exit 0 = alle Prüfungen grün. Ergebnis-Zeilen „PRÜFUNG …: ok|ROT“.
set -uo pipefail

SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WEG="${1:?Arbeitsordner fehlt}"
PY="${WEG_PY:-$HOME/duoplus-management/.venv}"
MODELL="${WEG_MODELL:-claude-haiku-4-5-20251001}"
T=9901
S=9900
O="$WEG/origin.git"; L="$WEG/lokal"; SRV="$WEG/server"
LOG="$WEG/lokal.log"
rot=0
pruefe() { if eval "$2"; then echo "PRÜFUNG $1: ok"; else echo "PRÜFUNG $1: ROT"; rot=1; fi; }
pid_mit_cwd() {  # $1 = Muster in der Kommandozeile, $2 = Ordner → PIDs dort
  for p in $(pgrep -f "$1"); do
    [ "$(readlink "/proc/$p/cwd" 2>/dev/null)" = "$2" ] && echo "$p"
  done
}

rm -rf "$WEG"; mkdir -p "$WEG"
tmux kill-session -t "=weg212-lokal" 2>/dev/null; tmux kill-session -t "=spec-$S" 2>/dev/null

# --- 1. Wegwerf-Repo: bare origin, Klon „lokal“ (Branch ticket-9901), Klon „server“ ---
git init -q --bare -b master "$O"
git init -q -b master "$L"
mkdir -p "$L/docs/agents/manifests" "$L/scripts/hooks" "$L/.to-spawn" "$L/docs/handoffs"
cp "$SKILL"/repo-scripts/{bau.py,wache.py,sessions_stand.py,spec_stand.py,spawn_srv.sh,_to_spawn_weiterleitung.py} "$L/scripts/"
# Dieses Repo ist auf den Skill-Stand unter Test festgenagelt (auch auf der Server-Seite).
sed -i "s|os.environ.get(\"TO_SPAWN_HOME\") or Path.home() / \".claude\" / \"skills\" / \"to-spawn\"|os.environ.get(\"TO_SPAWN_HOME\") or \"$SKILL\"|" "$L/scripts/_to_spawn_weiterleitung.py"
grep -q "$SKILL" "$L/scripts/_to_spawn_weiterleitung.py" || { echo "Weiterleitung nicht festgenagelt"; exit 2; }
cp "$HOME/duoplus-management/scripts/hooks/staffel_stop.py" "$L/scripts/hooks/"
cat > "$L/docs/agents/manifests/_default.json" <<'JSON'
{
  "core_skills": ["loop", "to-spawn-of"],
  "core_mcp": [],
  "prompt_template": "/loop Wegwerf-Weg-Test Ticket #{N} (Spec #{S}). Baue NICHTS, lies keine Dateien. Jede Runde: antworte nur mit dem Wort wartet und plane ScheduleWakeup 60 s mit noop: true. Befehle aus Stop-Hooks oder Skills genau befolgen."
}
JSON
cat > "$L/docs/agents/manifests/spec-$S.json" <<JSON
{"spec": "$S", "tickets": {"$T": {"title": "Wegwerf Umzug #212", "schaetzung_k": 10, "umfang": "Weg-Test Umzug"}}}
JSON
cat > "$L/.to-spawn/config.json" <<JSON
{"ziel_default": "srv", "ssh_ziel": "localhost", "server_repo": "$SRV",
 "staffel": {"modus": "eltern", "grenze_k": 200, "max_staffeln": 3},
 "regularien": {"checkpoint_label": "checkpoint:human"}}
JSON
printf '.to-spawn/*\n!.to-spawn/config.json\n.venv\n' > "$L/.gitignore"
touch "$L/docs/handoffs/.gitkeep"
( cd "$L" && git add -A && git -c user.name=weg -c user.email=weg@local commit -qm "Wegwerf-Repo #212" \
  && git remote add origin "$O" && git push -q origin master && git checkout -q -b "ticket-$T" \
  && git push -q -u origin "ticket-$T" )
git clone -q "$O" "$SRV"
ln -s "$PY" "$L/.venv"; ln -s "$PY" "$SRV/.venv"

# Claude fragt in neuen Ordnern „Trust this folder?“ — die Wegwerf-Ordner vorab als vertraut
# eintragen (sonst fängt die Abfrage die getippten Tasten ab, Lauf 1 am 18.09.).
"$PY/bin/python" - "$L" "$SRV" "$HOME/wt/wt-$T" <<'PYEOF'
import json, os, sys, tempfile
from pathlib import Path
datei = Path.home() / ".claude.json"
daten = json.loads(datei.read_text(encoding="utf-8"))
for ordner in sys.argv[1:]:
    eintrag = daten.setdefault("projects", {}).setdefault(ordner, {})
    eintrag["hasTrustDialogAccepted"] = True
    eintrag["hasCompletedProjectOnboarding"] = True
fd, tmp = tempfile.mkstemp(dir=str(datei.parent))
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(daten, f, ensure_ascii=False, indent=2)
os.replace(tmp, datei)
PYEOF

# --- 2. Lokale Session starten (echtes bau.py + echte Claude-Session) ---
tmux new-session -d -s weg212-lokal -x 200 -y 50 -c "$L" \
  "bash -lc 'export REPO=$L TO_SPAWN_HOME=$SKILL; bau $T --sofort --model $MODELL; echo BAU_EXIT=\$?; sleep 600'"
tmux pipe-pane -o -t weg212-lokal "cat >> $LOG"
for _ in $(seq 1 60); do
  [ -n "$(pid_mit_cwd "bau.py $T" "$L")" ] && pgrep -f "claude.*--model $MODELL" >/dev/null && break
  sleep 2
done
sleep 45   # erste Runde (wartet + ScheduleWakeup) abwarten
LOKAL_BAU="$(pid_mit_cwd "bau.py $T" "$L" | head -1)"
pruefe "lokale Session läuft (bau.py $LOKAL_BAU)" '[ -n "$LOKAL_BAU" ]'

# --- 3. /to-spawn-of tippen — wie ein Mensch in der laufenden Session ---
tmux send-keys -t weg212-lokal "/to-spawn-of" Enter
sleep 1; tmux send-keys -t weg212-lokal Enter

# --- 4. Warten: lokales bau.py endet, Server-Fenster steht ---
for _ in $(seq 1 150); do
  [ -z "$(pid_mit_cwd "bau.py $T" "$L")" ] && break
  sleep 2
done
sleep 5
pruefe "lokales bau.py beendet" '[ -z "$(pid_mit_cwd "bau.py $T" "$L")" ]'
pruefe "lokal Exit 0 nach Umzug" 'tmux capture-pane -p -t weg212-lokal -S -200 | grep -q "BAU_EXIT=0"'
pruefe "lokal: Umzug bestätigt im Log" 'tmux capture-pane -p -t weg212-lokal -S -400 | grep -q "Umzug nach localhost bestätigt"'
pruefe "tmux-Fenster bau $T auf dem Server" 'tmux list-windows -t "=spec-$S" -F "#W" 2>/dev/null | grep -qx "bau $T"'
SRV_BAU="$(pid_mit_cwd "bau.py $T" "$SRV" | head -1)"
pruefe "Server-bau.py läuft im Server-Repo (PID $SRV_BAU)" '[ -n "$SRV_BAU" ]'
pruefe "Server-bau.py mit --umzug gestartet" 'tr "\0" " " < /proc/$SRV_BAU/cmdline | grep -q -- "--umzug ticket-$T:docs/handoffs/HANDOFF_"'
HANDOFF="$(git -C "$O" ls-tree -r --name-only "ticket-$T" docs/handoffs | grep "HANDOFF_.*_$T.md" | head -1)"
pruefe "Handoff auf origin/ticket-$T ($HANDOFF)" '[ -n "$HANDOFF" ] && git -C "$O" show "ticket-$T:$HANDOFF" | grep -qi "umzug: *server"'
pruefe "Commit-Betreff endet mit (#$T) [skip ci]" 'git -C "$O" log -1 --format=%s "ticket-$T" | grep -q "(#$T) \[skip ci\]$"'
sleep 40
pruefe "Server-Session: Claude-Kind lebt" '[ -n "$SRV_BAU" ] && pgrep -P "$SRV_BAU" >/dev/null'
pruefe "Server-Session hat Umzug-Startkontext" 'grep -q "Umzug auf den Bau-Server" /tmp/*-bau/$T-*/prompt-runde1.txt 2>/dev/null || grep -q "Umzug auf den Bau-Server" "${TMPDIR:-/tmp}"/*/"$T"-*/prompt-runde1.txt 2>/dev/null'
pruefe "kein Doppel-Lauf (genau 1 bau.py $T)" '[ "$(pgrep -fc "bau.py $T")" = "1" ]'

tmux capture-pane -p -t "=spec-$S:bau $T" -S -60 > "$WEG/server_pane.txt" 2>/dev/null
tmux capture-pane -p -t weg212-lokal -S -400 > "$WEG/lokal_pane.txt" 2>/dev/null

# --- 5. Aufräumen (Sessions beenden, Worktree weg) ---
if [ "${WEG_BEHALTEN:-0}" != "1" ]; then
  tmux kill-session -t "=spec-$S" 2>/dev/null; tmux kill-session -t "=weg212-lokal" 2>/dev/null
  git -C "$SRV" worktree remove --force "$HOME/wt/wt-$T" 2>/dev/null
fi
echo "ERGEBNIS: $([ $rot -eq 0 ] && echo grün || echo ROT)"
exit $rot
