#!/usr/bin/env bash
# Installer für /to-spawn (Linux/macOS): Skill nach ~/.claude/skills/to-spawn, Alias-Skills
# (meta-exec, to-spawn-local, to-spawn-remote) nach ~/.claude/skills/<alias>, optional die
# Repo-Weiterleitungen (repo-scripts/ → <Repo>/scripts, nur fehlende Dateien) + .to-spawn/config.json.
# Dazu die Kette: kette/skills/* → <skills>/<name>, Regel-Blöcke kette/global/*.md → globale CLAUDE.md.
#
# Aufruf: bash install.sh [--repo <Pfad-zum-Repo>] [--skills <Ordner>] [--claude-md <Datei>]
#   --skills     Zielordner der Skills (Vorgabe ~/.claude/skills)
#   --claude-md  globale CLAUDE.md für die Regel-Blöcke (Vorgabe ~/.claude/CLAUDE.md)
# Ein vorhandener Skill-Ordner wird nie gelöscht, sondern nach <skills>/_alt/<name>-<zeit> verschoben.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS="$HOME/.claude/skills"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
REPO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --skills) SKILLS="${2:-}"; shift 2 ;;
    --claude-md) CLAUDE_MD="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unbekanntes Argument: $1" >&2; exit 2 ;;
  esac
done

ZIEL="$SKILLS/to-spawn"
mkdir -p "$SKILLS"
if [ -e "$ZIEL" ] && [ "$(cd "$ZIEL" && pwd -P)" = "$(cd "$HIER" && pwd -P)" ]; then
  echo "Abbruch: install.sh läuft aus dem Zielordner ($ZIEL) — aus dem Git-Klon starten." >&2
  exit 2
fi

# --- 1. Alten Stand sichern (verschieben, nie löschen) -----------------------
if [ -e "$ZIEL" ]; then
  SICHERUNG="$SKILLS/_alt/to-spawn-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$SKILLS/_alt"
  mv "$ZIEL" "$SICHERUNG"
  echo "Alter Stand gesichert: $SICHERUNG"
fi

# --- 2. Skill kopieren (ohne Git, Caches) ------------------------------------
mkdir -p "$ZIEL"
for eintrag in SKILL.md README.md LICENSE to_spawn.py install.sh install.ps1 spawn_local.ps1 spawn_srv.ps1 \
               to_spawn skripte repo-scripts aliase tests docs nest leitstand kette; do
  if [ -e "$HIER/$eintrag" ]; then
    cp -a "$HIER/$eintrag" "$ZIEL/"
  fi
done
find "$ZIEL" \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
chmod +x "$ZIEL/skripte/spawn_srv.sh" "$ZIEL/repo-scripts/spawn_srv.sh" "$ZIEL"/nest/*.sh
echo "Skill installiert: $ZIEL"

# --- 3. Alias-Skills ----------------------------------------------------------
for alias_ordner in "$HIER"/aliase/*/; do
  name="$(basename "$alias_ordner")"
  mkdir -p "$SKILLS/$name"
  cp "$alias_ordner/SKILL.md" "$SKILLS/$name/SKILL.md"
  echo "Alias installiert: $SKILLS/$name"
done

# --- 3b. Kette: Skills (grill-with-docs, to-spec, ...) + globale Regel-Blöcke ----
ZEIT="$(date +%Y%m%d-%H%M%S)"
for kette_ordner in "$HIER"/kette/skills/*/; do
  name="$(basename "$kette_ordner")"
  if [ -e "$SKILLS/$name" ]; then
    mkdir -p "$SKILLS/_alt"
    mv "$SKILLS/$name" "$SKILLS/_alt/$name-$ZEIT"
    echo "Alter Stand gesichert: $SKILLS/_alt/$name-$ZEIT"
  fi
  cp -a "$kette_ordner" "$SKILLS/$name"
  echo "Kette-Skill installiert: $SKILLS/$name"
done
PYTHONPATH="$ZIEL" python3 -m to_spawn.globale_regeln --ziel "$CLAUDE_MD"

# --- 4. Repo-Hälfte (optional): nur fehlende Dateien --------------------------
if [ -n "$REPO" ]; then
  REPO="$(cd "$REPO" && pwd)"
  # Manifest-Ordner aus der Repo-Konfig (pfade.manifeste, L1 P4b), sonst die Vorgabe aus config.py.
  MANIFESTE="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from to_spawn import config; print(config.pfad(config.lade(Path(sys.argv[2])), "manifeste"))' "$ZIEL" "$REPO")"
  mkdir -p "$REPO/scripts" "$MANIFESTE"
  for datei in "$HIER"/repo-scripts/*; do
    name="$(basename "$datei")"
    if [ "$name" = "_default.json" ]; then
      ziel_datei="$MANIFESTE/_default.json"
    else
      ziel_datei="$REPO/scripts/$name"
    fi
    if [ -e "$ziel_datei" ]; then
      echo "übersprungen (existiert): $ziel_datei"
    else
      cp "$datei" "$ziel_datei"
      echo "kopiert: $ziel_datei"
    fi
  done
  # Konfig anlegen (+ .gitignore-Block) und Checkpoint-Label im GitHub-Repo sicherstellen (#257).
  python3 - "$ZIEL" "$REPO" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from to_spawn import config, gh
repo = Path(sys.argv[2])
print("Konfig:", config.sicherstellen(repo))
label = str(config.lade(repo).get("regularien", {}).get("checkpoint_label") or "checkpoint:human")
if gh.label_sicherstellen(repo, label):
    print(f"Label {label}: vorhanden")
else:
    print(f"Label {label}: nicht angelegt — später von Hand: gh label create {label}")
PY
fi

echo "Fertig. Im Repo: /to-spawn <SpecNr> (fragt lokal/Server) · /to-spawn-local <S> · /to-spawn-remote <S>"
