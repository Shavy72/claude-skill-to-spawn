#!/usr/bin/env bash
# Installer für /to-spawn (Linux/macOS): Skill nach ~/.claude/skills/to-spawn, Alias-Skills
# (meta-exec, to-spawn-local, to-spawn-remote) nach ~/.claude/skills/<alias>, optional die
# Repo-Weiterleitungen (repo-scripts/ → <Repo>/scripts, nur fehlende Dateien) + .to-spawn/config.json.
#
# Aufruf: bash install.sh [--repo <Pfad-zum-Repo>] [--skills <Ordner>]
#   --skills  Zielordner der Skills (Vorgabe ~/.claude/skills)
# Ein vorhandener Skill-Ordner wird nie gelöscht, sondern nach <skills>/_alt/to-spawn-<zeit> verschoben.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS="$HOME/.claude/skills"
REPO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --skills) SKILLS="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,8p' "${BASH_SOURCE[0]}"; exit 0 ;;
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
               to_spawn skripte repo-scripts aliase tests docs nest; do
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

# --- 4. Repo-Hälfte (optional): nur fehlende Dateien --------------------------
if [ -n "$REPO" ]; then
  REPO="$(cd "$REPO" && pwd)"
  mkdir -p "$REPO/scripts" "$REPO/docs/agents/manifests"
  for datei in "$HIER"/repo-scripts/*; do
    name="$(basename "$datei")"
    if [ "$name" = "_default.json" ]; then
      ziel_datei="$REPO/docs/agents/manifests/_default.json"
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
  python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from to_spawn import config; print("Konfig:", config.sicherstellen(Path(sys.argv[2])))' "$ZIEL" "$REPO"
fi

echo "Fertig. Im Repo: /to-spawn <SpecNr> (fragt lokal/Server) · /to-spawn-local <S> · /to-spawn-remote <S>"
