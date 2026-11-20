#!/bin/sh
# install_git_hooks.sh — Git-Hooks in diesem Klon anlegen (#219, idempotent).
#   pre-commit  -> scripts/pruef_belegbilder.py (Belegbilder WebP <= 300 KB)
#   commit-msg  -> [skip ci] anhaengen + scripts/hooks/test_zaehler_wache.py (B4, #282)
#   pre-push    -> scripts/hooks/pre-push-master-sperre.sh (master-Sperre im Modus tickets, #1442)
# Einzige Stelle mit dem pre-commit-Text: scripts/setup_bau_server.sh ruft dieses Skript.
# graphify-Hooks legt setup_bau_server.sh selbst an; commit-msg legt dieses Skript an.
# Aufruf: sh scripts/hooks/install_git_hooks.sh [<repo-pfad>]
set -e
repo=${1:-.}
hooks=$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir)/hooks
mkdir -p "$hooks"

# pre-commit und pre-merge-commit: git ruft beim Merge NICHT pre-commit, sonst
# rutschen PNG über einen Merge an der Prüfung vorbei.
for name in pre-commit pre-merge-commit; do
  ziel="$hooks/$name"
  if [ -f "$ziel" ] && ! grep -q "pruef_belegbilder.py" "$ziel"; then
    cp "$ziel" "$ziel.vor-219"
    echo "vorhandener $name gesichert: $ziel.vor-219"
  fi
  cat > "$ziel" <<'EOF'
#!/bin/sh
# Belegbilder prüfen, Logik in scripts/pruef_belegbilder.py (#219).
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -f "$root/scripts/pruef_belegbilder.py" ] || exit 0
py="$root/.venv/bin/python"
[ -x "$py" ] || py=$(command -v python3 || command -v python) || exit 0
"$py" "$root/scripts/pruef_belegbilder.py" || exit 1
exit 0
EOF
  chmod +x "$ziel"
  echo "$name angelegt: $ziel"
done

# commit-msg: haengt [skip ci] an (GitHub-Actions-Quota) und laesst den
# Test-Zahl-Waechter mit der ECHTEN Commit-Nachricht laufen (B4, #282) — im
# pre-commit-Hook ist die Nachricht noch nicht bekannt, der Marker
# "test-umbenannt:" waere dort unsichtbar.
ziel="$hooks/commit-msg"
if [ -f "$ziel" ] && ! grep -q "test_zaehler_wache.py" "$ziel"; then
  cp "$ziel" "$ziel.vor-282"
  echo "vorhandener commit-msg gesichert: $ziel.vor-282"
fi
cat > "$ziel" <<'EOF'
#!/bin/sh
# 1) [skip ci] anhaengen (Kontext-Taschenlampe 2026-09-10)
# 2) Test-Zahl-Waechter mit der echten Nachricht (B4, #282)
f="$1"
first=$(sed -n '1p' "$f")
case "$first" in
  *"[skip ci]"*|"") : ;;
  *) { printf '%s [skip ci]
' "$first"; sed -n '2,$p' "$f"; } > "$f.tmp" && mv "$f.tmp" "$f" ;;
esac
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -f "$root/scripts/hooks/test_zaehler_wache.py" ] || exit 0
py="$root/.venv/bin/python"
[ -x "$py" ] || py=$(command -v python3 || command -v python) || exit 0
"$py" "$root/scripts/hooks/test_zaehler_wache.py" --nachricht "$f" || exit 1
exit 0
EOF
chmod +x "$ziel"
echo "commit-msg angelegt: $ziel"

# pre-push (#1442): im Modus tickets kein direkter Push auf master — in JEDEM
# Klon, nicht nur im Hauptrepo. Die Regel lebt nur in
# scripts/hooks/pre-push-master-sperre.sh (Modus aus der .env der Schlange,
# Ausnahme DEPLOY_SCHLANGE_MERGE=1 für deren Fast-Forward); der Hook ruft sie
# nur auf. Fehlt das Skript im ausgecheckten Stand, läuft der Push mit Warnung
# weiter, statt einen erlaubten Push zu blockieren.
# Eine volle Kopie der Sperre (Hauptrepo, `master_schutz.sh hook-an`) bleibt stehen:
# master_schutz.sh erkennt seine Sperre nur per cmp, sonst bricht hook-an ab.
ziel="$hooks/pre-push"
quelle="$(cd "$(dirname "$0")" && pwd)/pre-push-master-sperre.sh"
if [ -f "$ziel" ] && cmp -s "$ziel" "$quelle"; then
  echo "pre-push ist volle Kopie der Sperre (master_schutz.sh), bleibt: $ziel"
  exit 0
fi
if [ -f "$ziel" ] && ! grep -q "pre-push-master-sperre.sh" "$ziel"; then
  cp "$ziel" "$ziel.vor-1442"
  echo "vorhandener pre-push gesichert: $ziel.vor-1442"
fi
cat > "$ziel" <<'EOF'
#!/bin/sh
# master-Sperre im Modus tickets (#1442), Regel in scripts/hooks/pre-push-master-sperre.sh.
root=$(git rev-parse --show-toplevel 2>/dev/null) || root=""
sperre="$root/scripts/hooks/pre-push-master-sperre.sh"
if [ -z "$root" ] || [ ! -f "$sperre" ]; then
  echo "⚠ pre-push: $sperre fehlt — master-Sperre (#1442) NICHT geprüft, Push läuft weiter." >&2
  exit 0
fi
if ! command -v bash >/dev/null 2>&1; then
  echo "⚠ pre-push: bash fehlt — master-Sperre (#1442) NICHT geprüft, Push läuft weiter." >&2
  exit 0
fi
exec bash "$sperre" "$@"
EOF
chmod +x "$ziel"
echo "pre-push angelegt: $ziel"
