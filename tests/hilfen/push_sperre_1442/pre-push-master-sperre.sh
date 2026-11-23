#!/usr/bin/env bash
# pre-push: im Modus tickets kein direkter Push auf master (Test-Gate Etappe 2,
# #1442, #1445). master bewegt nur die Deploy-Schlange. ticket/*- und alle anderen
# Branches blockt der Hook nie. Läuft unter Linux und Git-Bash (Windows-PC):
# nur bash + git, kein Python, kein fester Server-Pfad.
#
# Modus — EINE Quelle, hier und nur hier gelesen (#1445):
#   eingecheckte Datei scripts/hooks/schlange_modus auf <remote>/master
#   (Git-Objekt, nicht Arbeitsordner, nicht Umgebung, nicht .env).
#   Inhalt genau „master“ = Modus master; alles andere, auch Datei fehlt
#   oder unlesbar = tickets (Sperre greift, nie stiller Rückfall auf master).
#   Auskunft für Sessions: `bash scripts/hooks/pre-push-master-sperre.sh --modus`
#   (ruft auch `python scripts/ticket_status.py modus`).
#
# Ausnahme Protokoll des Aufsehers: ändert der Push auf master netto nur
# Protokoll-Dateien (ist_protokoll_pfad unten), geht er durch — Regel nach Pfaden,
# nie nach Commit-Nachricht.
#
# Weitere Ausnahmen:
#   DEPLOY_SCHLANGE_MERGE=1  setzt nur deploy_schlange.py beim Fast-Forward auf master.
#   Notausgang für den Menschen (ein Befehl, nur mit Begründung):
#     MASTER_PUSH_ERLAUBT=1 git push origin HEAD:master
# Abgewiesener Push: nur Meldung im Terminal (kein Telegram, keine Mail, #1445).
# Einrichten: sh scripts/hooks/install_git_hooks.sh
set -u

MODUS_DATEI=scripts/hooks/schlange_modus

# Modus aus <remote>/master (Standard origin): „master“ oder „tickets“.
schlange_modus() {
  local inhalt
  inhalt=$(git show "refs/remotes/${1:-origin}/master:$MODUS_DATEI" 2>/dev/null |
    tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
  if [ "$inhalt" = "master" ]; then echo master; else echo tickets; fi
}

# Ist der Pfad eine Protokoll-Datei des Aufsehers? (wache.py-Prompt: Handoff,
# Entscheidungen, Thermo, Rückblick, Manifest-Befund; capo: bau_log; Belege.)
# Die eine Stelle der Regel: nur Endung .md/.json/.jsonl (Manifeste nur .json,
# verify-hard zusätzlich Bild-Belege .webp/.png), genau die Tiefe, in der das
# Protokoll real liegt — flach, nur verify-hard <datei> oder <ticket>/<datei>.
ist_protokoll_pfad() {
  local rest
  case "$1" in
    docs/agents/manifests/*)
      rest=${1#docs/agents/manifests/}
      case "$rest" in */*) return 1 ;; *.json) return 0 ;; *) return 1 ;; esac
      ;;
    docs/verify-hard/*)
      rest=${1#docs/verify-hard/}
      case "$rest" in */*/*) return 1 ;; *.md | *.json | *.jsonl | *.webp | *.png) return 0 ;; *) return 1 ;; esac
      ;;
    docs/agents/bau_log/* | docs/handoffs/*)
      rest=${1#docs/agents/bau_log/}
      rest=${rest#docs/handoffs/}
      ;;
    docs/agents/entscheidungen_* | docs/agents/thermo_* | docs/agents/rueckblick_* | docs/HANDOFF_*)
      rest=${1#docs/agents/}
      rest=${rest#docs/}
      ;;
    *) return 1 ;;
  esac
  case "$rest" in */*) return 1 ;; *.md | *.json | *.jsonl) return 0 ;; esac
  return 1
}

# Ändert alt..neu netto nur Protokoll-Dateien? Nur reguläre Dateien zählen
# (Modus 100644 vorher/nachher, 000000 = neu/gelöscht) — Symlink 120000,
# ausführbar 100755 und Submodul 160000 sperren.
nur_protokoll() {
  local zeilen modus_alt modus_neu _sha_alt _sha_neu _status pfad
  zeilen=$(git -c core.quotePath=false diff --raw --no-renames "$1" "$2" 2>/dev/null) || return 1
  [ -n "$zeilen" ] || return 1
  while read -r modus_alt modus_neu _sha_alt _sha_neu _status pfad; do
    case "${modus_alt#:}" in 100644 | 000000) ;; *) return 1 ;; esac
    case "$modus_neu" in 100644 | 000000) ;; *) return 1 ;; esac
    ist_protokoll_pfad "$pfad" || return 1
  done <<EOF
$zeilen
EOF
  return 0
}

if [ "${1:-}" = "--modus" ]; then
  schlange_modus origin
  exit 0
fi

[ "${MASTER_PUSH_ERLAUBT:-}" = "1" ] && exit 0
[ "${DEPLOY_SCHLANGE_MERGE:-}" = "1" ] && exit 0

remote=${1:-origin}
[ "$(schlange_modus "$remote")" = "master" ] && exit 0

null=0000000000000000000000000000000000000000
gesperrt=0
while read -r _lokal_ref lokal_sha fern_ref fern_sha; do
  [ "$fern_ref" = "refs/heads/master" ] || continue
  # Neu anlegen oder löschen von master ist nie ein Protokoll-Push.
  if [ "$lokal_sha" != "$null" ] && [ "$fern_sha" != "$null" ] && nur_protokoll "$fern_sha" "$lokal_sha"; then
    continue
  fi
  gesperrt=1
done
[ "$gesperrt" = "1" ] || exit 0

echo "✋ Modus tickets: kein direkter Push auf master (Quelle: $MODUS_DATEI auf $remote/master)." >&2
echo "   Eigenen Branch pushen: git push origin HEAD:refs/heads/ticket/<nr>-<kurzname>" >&2
echo "   Die Deploy-Schlange gatet ihn und legt ihn bei Grün auf master." >&2
echo "   Rückmeldung: python scripts/ticket_status.py warte ticket/<nr>-<kurzname>" >&2
exit 1
