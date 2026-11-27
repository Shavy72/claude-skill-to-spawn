#!/usr/bin/env bash
# Installer für /to-spawn (Linux/macOS): Skill nach ~/.claude/skills/to-spawn, Alias-Skills
# (meta-exec, to-spawn-local, to-spawn-remote) nach ~/.claude/skills/<alias>, optional die
# Repo-Weiterleitungen (repo-scripts/ → <Repo>/scripts, nur fehlende Dateien) + .to-spawn/config.json.
# Dazu die Kette: kette/skills/* → <skills>/<name>, Regel-Blöcke kette/global/*.md → globale CLAUDE.md.
#
# Aufruf: bash install.sh [--repo <Pfad-zum-Repo>] [--skills <Ordner>] [--claude-md <Datei>] [--erzwingen]
#   --skills     Zielordner der Skills (Vorgabe ~/.claude/skills)
#   --claude-md  globale CLAUDE.md für die Regel-Blöcke (Vorgabe ~/.claude/CLAUDE.md)
#   --erzwingen  Installations-Sperre bewusst überstimmen (roter/unbekannter Stand)
# Ein vorhandener Skill-Ordner wird nie gelöscht, sondern nach <skills>/_alt/<name>-<zeit> verschoben.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS="$HOME/.claude/skills"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
REPO=""
ERZWINGEN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --skills) SKILLS="${2:-}"; shift 2 ;;
    --claude-md) CLAUDE_MD="${2:-}"; shift 2 ;;
    --erzwingen) ERZWINGEN=1; shift ;;
    -h|--help) sed -n '2,11p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unbekanntes Argument: $1" >&2; exit 2 ;;
  esac
done

# nativ <pfad>: unter Windows (Git Bash) C:/…-Form, sonst unverändert. Python versteht /c/… nicht,
# und mit MSYS_NO_PATHCONV=1 (z. B. in Claude Code) übersetzt Git Bash die Argumente nicht selbst.
nativ() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi
}
HIER="$(nativ "$HIER")"
SKILLS="$(nativ "$SKILLS")"
CLAUDE_MD="$(nativ "$CLAUDE_MD")"

ZIEL="$SKILLS/to-spawn"
if [ -e "$ZIEL" ] && [ "$(cd "$ZIEL" && pwd -P)" = "$(cd "$HIER" && pwd -P)" ]; then
  echo "Abbruch: install.sh läuft aus dem Zielordner ($ZIEL) — aus dem Git-Klon starten." >&2
  exit 2
fi

# Python vor dem ersten Kopieren wählen: sonst bliebe ein halber Stand (kein STAND.json, alte Alias-Skills).
# Reihenfolge TO_SPAWN_PY (Python des Aufrufers, #325), python3, python; zählt nur ab 3.11 (datetime.UTC) —
# unter Windows ist „python3“ in Git Bash oft der alte Store-Stub, das echte Python heißt „python“.
PY=""
for kandidat in "${TO_SPAWN_PY:-}" python3 python; do
  if [ -n "$kandidat" ] && "$kandidat" -c 'import datetime; datetime.UTC' >/dev/null 2>&1; then
    PY="$kandidat"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Abbruch: python3 (ab 3.11) fehlt — erst Python installieren, dann install.sh erneut starten (nichts kopiert)." >&2
  exit 127
fi

ist_windows() {
  case "$(uname -s 2>/dev/null || true)" in
    MINGW*|MSYS*|CYGWIN*) return 0 ;;
    *) return 1 ;;
  esac
}

besitzer_info() {
  if stat -c '%u %U:%G' "$1" 2>/dev/null; then
    return 0
  fi
  stat -f '%u %Su:%Sg' "$1" 2>/dev/null || printf '%s %s\n' "" "unbekannt"
}

# _dienst_gilt_fuer_hooks <dienst> <hooks_dir>: prüft, ob die systemd-Unit für diesen Hooks-Ordner gilt.
# Nennt die Unit einen bestimmten /.claude/hooks-Pfad, gilt sie nur für diesen (Tests unter tmp_path bleiben getrennt).
_dienst_gilt_fuer_hooks() {
  local dienst="$1" hooks_dir="$2"
  [ -f "$dienst" ] || return 1
  if grep -Fq "$hooks_dir" "$dienst" 2>/dev/null; then
    return 0
  fi
  if grep -q '/\.claude/hooks' "$dienst" 2>/dev/null; then
    return 1
  fi
  return 0
}

# hooks_fremdverwaltet <hooks_dir> (#851): erkennt absichtlich root-/fremdverwalteten Hooks-Ordner
# (fremder Besitzer oder nicht schreibbar + claude-config-hooks-Dienst oder Marker im Ordner).
hooks_fremdverwaltet() {
  local hooks_dir="$1" meine_uid besitzer_zeile uid_besitzer dienst
  ist_windows && return 1
  [ -e "$hooks_dir" ] || return 1
  meine_uid="$(id -u 2>/dev/null || echo "")"
  [ -n "$meine_uid" ] && [ "$meine_uid" != "0" ] || return 1
  besitzer_zeile="$(besitzer_info "$hooks_dir")"
  uid_besitzer="${besitzer_zeile%% *}"
  if { [ -z "$uid_besitzer" ] || [ "$uid_besitzer" = "$meine_uid" ]; } && [ -w "$hooks_dir" ]; then
    return 1
  fi
  dienst="${TO_SPAWN_HOOKS_DIENST:-/etc/systemd/system/claude-config-hooks.service}"
  if _dienst_gilt_fuer_hooks "$dienst" "$hooks_dir"; then
    return 0
  fi
  if [ -e "$hooks_dir/.claude-config-hooks" ] || [ -d "$hooks_dir/config-sync" ]; then
    return 0
  fi
  return 1
}

# pruefe_zielrechte (#297): vor dem ersten Schreiben Besitzer und Schreibrecht aller Zielordner prüfen.
# Bricht vor jeder Änderung mit allen betroffenen Pfaden, Besitzer und Behebungsbefehl ab.
pruefe_zielrechte() {
  local meine_uid mein_nutzer meine_gruppe fehler_text="" gesehen=$'\n' pfad pruef_pfad ist_eltern
  local besitzer_zeile uid_besitzer name_besitzer grund behebung naechst
  meine_uid="$(id -u 2>/dev/null || echo "")"
  mein_nutzer="$(id -un 2>/dev/null || echo "${USER:-nutzer}")"
  meine_gruppe="$(id -gn 2>/dev/null || echo "$mein_nutzer")"
  for pfad in "$@"; do
    [ -n "$pfad" ] || continue
    ist_eltern=0
    pruef_pfad="$pfad"
    if [ ! -e "$pruef_pfad" ]; then
      ist_eltern=1
      while [ ! -e "$pruef_pfad" ] && [ "$pruef_pfad" != "/" ] && [ "$pruef_pfad" != "." ]; do
        naechst="$(dirname "$pruef_pfad")"
        [ "$naechst" = "$pruef_pfad" ] && break
        pruef_pfad="$naechst"
      done
    fi
    [ -e "$pruef_pfad" ] || continue
    case "$gesehen" in *$'\n'"$pruef_pfad"$'\n'*) continue ;; esac
    gesehen="${gesehen}${pruef_pfad}"$'\n'

    besitzer_zeile="$(besitzer_info "$pruef_pfad")"
    uid_besitzer="${besitzer_zeile%% *}"
    name_besitzer="${besitzer_zeile#* }"
    [ "$name_besitzer" = "$besitzer_zeile" ] && name_besitzer="unbekannt"

    grund=""
    behebung=""
    if ! ist_windows && [ -n "$meine_uid" ] && [ "$meine_uid" != "0" ] && [ -n "$uid_besitzer" ] && [ "$uid_besitzer" != "$meine_uid" ]; then
      if [ "$ist_eltern" -eq 0 ] || [ ! -w "$pruef_pfad" ]; then
        grund="Besitzer: $name_besitzer, erwartet: $mein_nutzer"
        behebung="sudo chown -R $mein_nutzer:$meine_gruppe \"$pruef_pfad\""
      fi
    fi
    if [ -z "$grund" ] && [ ! -w "$pruef_pfad" ]; then
      grund="Besitzer: $name_besitzer, nicht beschreibbar"
      behebung="chmod u+w \"$pruef_pfad\""
    fi
    if [ -n "$grund" ]; then
      fehler_text="${fehler_text}  - ${pruef_pfad} (${grund}) — Behebung: ${behebung}"$'\n'
    fi
  done
  if [ -n "$fehler_text" ]; then
    echo "Abbruch: Zielordner nicht schreibbar oder fremder Besitzer (nichts geändert):" >&2
    printf '%s' "$fehler_text" >&2
    exit 1
  fi
}

CLAUDE_DIR="$(dirname "$CLAUDE_MD")"
HOME_CLAUDE="$(nativ "$HOME/.claude")"
HOOKS_FREMDVERWALTET=0
if hooks_fremdverwaltet "$HOME_CLAUDE/hooks"; then
  HOOKS_FREMDVERWALTET=1
fi
ZIEL_PFADE=(
  "$SKILLS"
  "$CLAUDE_DIR"
  "$HOME_CLAUDE"
  "$HOME_CLAUDE/review"
)
if [ "$HOOKS_FREMDVERWALTET" -eq 0 ]; then
  ZIEL_PFADE+=(
    "$HOME_CLAUDE/hooks"
    "$HOME_CLAUDE/hooks/review"
  )
  [ -e "$HOME_CLAUDE/hooks/_alt" ] && ZIEL_PFADE+=("$HOME_CLAUDE/hooks/_alt")
fi
for vorhanden in \
  "$SKILLS/_alt" \
  "$ZIEL" \
  "$SKILLS/review-dirigent" \
  "$CLAUDE_MD" \
  "$CLAUDE_DIR/bin" \
  "$CLAUDE_DIR/settings.json" \
  "$HOME_CLAUDE/_alt"; do
  [ -e "$vorhanden" ] && ZIEL_PFADE+=("$vorhanden")
done
for unter in "$HIER"/aliase/*/ "$HIER"/kette/skills/*/; do
  [ -d "$unter" ] || continue
  vorhanden="$SKILLS/$(basename "$unter")"
  [ -e "$vorhanden" ] && ZIEL_PFADE+=("$vorhanden")
done
if [ -n "$REPO" ] && [ -d "$REPO" ]; then
  REPO_PRUEF="$(nativ "$(cd "$REPO" && pwd)")"
  MANIFESTE_PRUEF="$("$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from to_spawn import config; print(config.pfad(config.lade(Path(sys.argv[2])), "manifeste"))' "$HIER" "$REPO_PRUEF")"
  ZIEL_PFADE+=("$REPO_PRUEF" "$REPO_PRUEF/scripts" "$MANIFESTE_PRUEF")
fi
pruefe_zielrechte "${ZIEL_PFADE[@]}"

# Installations-Sperre vor jedem Tausch prüfen (#415, #577): GitHub Actions, sonst lokale Suite;
# rot/unbekannt → Abbruch ohne Änderung (+ Fehler-Log, bei Rot Ticket); --erzwingen überstimmt bewusst.
PRUEF_ARGS=(--klon "$HIER" --python "$PY")
if [ "$ERZWINGEN" -eq 1 ]; then
  PRUEF_ARGS+=(--erzwingen)
fi
PYTHONPATH="$HIER" "$PY" -m to_spawn.install_pruefung pruefen "${PRUEF_ARGS[@]}"

mkdir -p "$SKILLS"

ZEIT="$(date +%Y%m%d-%H%M%S)"

# sichere_und_kopiere <name> <quelle>...: kopiert erst vollständig in einen Staging-Ordner in <skills>,
# bereinigt Caches, setzt bei to-spawn Ausführungsrechte + STAND.json und tauscht danach atomar (#415).
# Ein vorhandener Ordner wird nie gelöscht, sondern nach _alt/<name>-<zeit> verschoben; Dateien, die es
# nur im alten Stand gibt, werden gemeldet (STAND.json nicht).
sichere_und_kopiere() {
  local name="$1"
  shift
  local -a extra=()
  if [ "$name" = "to-spawn" ]; then
    extra=(--klon "$HIER" --ausfuehrbar skripte/spawn_srv.sh --ausfuehrbar repo-scripts/spawn_srv.sh --ausfuehrbar 'nest/*.sh')
  fi
  PYTHONPATH="$HIER" "$PY" -m to_spawn.install_pruefung tauschen \
    --skills "$SKILLS" --name "$name" --zeit "$ZEIT" "${extra[@]}" -- "$@"
}

# --- 1+2. Alten Stand sichern, Skill atomar kopieren (ohne Git, Caches) -------
quellen=()
for eintrag in SKILL.md README.md LICENSE to_spawn.py install.sh install.ps1 spawn_local.ps1 spawn_srv.ps1 \
               to_spawn skripte repo-scripts aliase tests docs nest leitstand kette review-hook review review-dirigent probe; do
  quellen+=("$HIER/$eintrag")
done
sichere_und_kopiere to-spawn "${quellen[@]}"
echo "Skill installiert: $ZIEL"

# --- 3. Alias-Skills ----------------------------------------------------------
for alias_ordner in "$HIER"/aliase/*/; do
  name="$(basename "$alias_ordner")"
  sichere_und_kopiere "$name" "$alias_ordner"/*
  echo "Alias installiert: $SKILLS/$name"
done

# --- 3b. Kette: Skills (grill-with-docs, to-spec, ...) + globale Regel-Blöcke ----
for kette_ordner in "$HIER"/kette/skills/*/; do
  name="$(basename "$kette_ordner")"
  sichere_und_kopiere "$name" "$kette_ordner"*
  echo "Kette-Skill installiert: $SKILLS/$name"
done
PYTHONPATH="$ZIEL" "$PY" -m to_spawn.globale_regeln --ziel "$CLAUDE_MD"
# Ja-Sperre (PreToolUse-Hook in settings.json neben der CLAUDE.md) + Befehl `ja` nach bin/ daneben (#531)
PYTHONPATH="$ZIEL" "$PY" -m to_spawn.ja_einrichten --claude-dir "$(dirname "$CLAUDE_MD")" --python "$PY" --cli "$ZIEL/to_spawn.py"

# --- 3c. Review-Hook (review-hook/ → ~/.claude/hooks/review, #214) + Werkzeug (review/ → ~/.claude/review, #329); Regel in to_spawn/review_hook.py
if [ "$HOOKS_FREMDVERWALTET" -eq 1 ]; then
  echo "Hooks übersprungen: $HOME_CLAUDE/hooks (Hooks kommen über claude-config, nicht von hier)"
  PYTHONPATH="$ZIEL" "$PY" -m to_spawn.review_hook --klon "$HIER" --ohne-hook
else
  PYTHONPATH="$ZIEL" "$PY" -m to_spawn.review_hook --klon "$HIER"  # Klon: nur versionierte Dateien
fi

# --- 3d. Review-Dirigent (review-dirigent/ → <skills>/review-dirigent, #396) ---
if [ -d "$HIER/review-dirigent" ]; then
  sichere_und_kopiere review-dirigent "$HIER"/review-dirigent/*
  echo "Review-Dirigent installiert: $SKILLS/review-dirigent"
fi

# --- 3e. Grünen installierten SHA im Fehler-Log vermerken (#415) --------------
PYTHONPATH="$ZIEL" "$PY" -m to_spawn.install_pruefung verbuchen --klon "$HIER" --ziel "$ZIEL"
# --- 3f. Maschinen-Liste: veraltete ticket_repo-Vorgaben nachziehen (#261) ----
"$PY" "$ZIEL/to_spawn.py" ticket-repo --nachziehen || echo "Hinweis: Maschinen-Liste nicht nachgezogen" >&2

# --- 4. Repo-Hälfte (optional): nur fehlende Dateien --------------------------
if [ -n "$REPO" ]; then
  REPO="$(nativ "$(cd "$REPO" && pwd)")"
  # Manifest-Ordner aus der Repo-Konfig (pfade.manifeste, L1 P4b), sonst die Vorgabe aus config.py.
  MANIFESTE="$("$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from to_spawn import config; print(config.pfad(config.lade(Path(sys.argv[2])), "manifeste"))' "$ZIEL" "$REPO")"
  mkdir -p "$REPO/scripts" "$MANIFESTE"
  for datei in "$HIER"/repo-scripts/*; do
    [ -f "$datei" ] || continue  # nur Dateien: __pycache__ u. a. Ordner nie ins Repo (#155)
    name="$(basename "$datei")"
    # Start-Text kommt aus der Skill-Vorlage; Repo-Regeln über start_text.zusatz_datei (#274 P1).
    [ "$name" = "_default.json" ] && continue
    ziel_datei="$REPO/scripts/$name"
    if [ -e "$ziel_datei" ]; then
      echo "übersprungen (existiert): $ziel_datei"
    else
      cp "$datei" "$ziel_datei"
      echo "kopiert: $ziel_datei"
    fi
  done
  # Konfig anlegen (+ .gitignore-Block) und Checkpoint-Label im GitHub-Repo sicherstellen (#257).
  "$PY" - "$ZIEL" "$REPO" <<'PY'
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
