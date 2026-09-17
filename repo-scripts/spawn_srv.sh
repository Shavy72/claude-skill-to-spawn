#!/usr/bin/env bash
# spawn_srv.sh — alle Ticket-Sessions einer Spec auf dem Bau-Server starten.
#
# Aufruf (auf dem Server, als Nutzer bau):
#   bash scripts/spawn_srv.sh <S> [--tickets 179,188] [--ohne-wache] [--dry-run]
#
# Eine tmux-Session je Spec (``spec-<S>``), darin ein Fenster ``wache <S>`` und
# je Ticket ein Fenster ``bau <N>``. Gewartet wird in bau.py (0 Token).
# Kontrolle: ``sessions <S>`` · ``tmux attach -t spec-<S>`` (raus: Strg+B d).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

SPEC=""
TICKETS_ARG=""
OHNE_WACHE=0
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tickets) TICKETS_ARG="${2:-}"; shift 2 ;;
    --ohne-wache) OHNE_WACHE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,9p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)
      if [ -z "$SPEC" ]; then SPEC="$1"; shift; else echo "Unbekanntes Argument: $1" >&2; exit 2; fi ;;
  esac
done
[ -n "$SPEC" ] || { echo "Spec-Nummer fehlt. Aufruf: bash scripts/spawn_srv.sh <S> [--tickets a,b] [--ohne-wache] [--dry-run]" >&2; exit 2; }

MANIFEST="docs/agents/manifests/spec-$SPEC.json"
[ -f "$MANIFEST" ] || { echo "Manifest fehlt: $MANIFEST" >&2; exit 2; }
command -v tmux >/dev/null || { echo "tmux ist nicht installiert." >&2; exit 2; }

# --- 1. Stand holen (nie rebase/stash im geteilten Baum) --------------------
echo "== Stand =="
git fetch origin --quiet
if [ "$DRY_RUN" -eq 1 ]; then
  echo "Probelauf — kein merge. HEAD $(git rev-parse --short HEAD), origin/master $(git rev-parse --short origin/master)."
elif ! git merge --ff-only origin/master; then
  echo "Abbruch: Repo lässt sich nicht vorspulen (eigene Commits oder dreckiger Baum). Von Hand klären, nicht rebasen/stashen." >&2
  exit 1
fi

# --- 2. Tickets bestimmen ---------------------------------------------------
if [ -n "$TICKETS_ARG" ]; then
  TICKETS="$(echo "$TICKETS_ARG" | tr ',' '\n' | sed '/^\s*$/d')"
else
  TICKETS="$(python3 -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print('\n'.join(sorted((d.get('tickets') or {}), key=int)))" "$MANIFEST")"
fi
[ -n "$TICKETS" ] || { echo "Keine Tickets im Manifest $MANIFEST." >&2; exit 2; }

# --- 3. Was läuft schon? (nie Duplikate) ------------------------------------
STAND="$(python3 scripts/sessions_stand.py "$SPEC" --alle 2>/dev/null || true)"
zustand_von() { echo "$STAND" | awk -v n="#$1" '$1==n {print $2; exit} ($1=="Spec" && $2==n) {print $3; exit}'; }

SESSION="spec-$SPEC"
GEPLANT=()
if [ "$OHNE_WACHE" -eq 0 ]; then
  z="$(zustand_von "$SPEC")"
  if [ -z "$z" ] || [ "$z" = "aus" ]; then
    GEPLANT+=("wache $SPEC")
  else
    echo "Wächter für Spec #$SPEC läuft bereits ($z) — übersprungen."
  fi
fi
for n in $TICKETS; do
  z="$(zustand_von "$n")"
  if [ -z "$z" ] || [ "$z" = "aus" ]; then
    GEPLANT+=("bau $n")
  else
    echo "Ticket #$n läuft bereits ($z) — übersprungen."
  fi
done

if [ "${#GEPLANT[@]}" -eq 0 ]; then
  echo "Nichts zu starten — alle Fenster laufen schon."
  exit 0
fi

echo "== Geplante Fenster in tmux-Session $SESSION =="
for befehl in "${GEPLANT[@]}"; do echo "  - $befehl"; done

if [ "$DRY_RUN" -eq 1 ]; then
  echo "Probelauf — nichts gestartet."
  exit 0
fi

# --- 4. tmux-Fenster anlegen ------------------------------------------------
for befehl in "${GEPLANT[@]}"; do
  if tmux has-session -t "=$SESSION" 2>/dev/null; then
    tmux new-window -t "=$SESSION" -n "$befehl" -c "$REPO" bash -lc "$befehl"
  else
    tmux new-session -d -s "$SESSION" -n "$befehl" -c "$REPO" bash -lc "$befehl"
  fi
  echo "gestartet: $befehl"
done

echo "== Anlauf (10 s) =="
sleep 10
python3 scripts/sessions_stand.py "$SPEC" --alle || true
echo
echo "Kontrolle: sessions $SPEC · tmux attach -t $SESSION (Fenster wechseln Strg+B n, raus Strg+B d)"
