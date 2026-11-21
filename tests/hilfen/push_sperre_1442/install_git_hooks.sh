#!/bin/sh
# install_git_hooks.sh — Git-Hooks in diesem Klon anlegen (#219, idempotent).
#   pre-commit  -> scripts/pruef_belegbilder.py (Belegbilder WebP <= 300 KB)
#   commit-msg  -> [skip ci] anhaengen + scripts/hooks/test_zaehler_wache.py (B4, #282)
#   pre-push    -> feste Kopie der master-Sperre in .git/hooks (#1442, #1450)
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

# pre-push (#1442, #1450): im Modus tickets kein direkter Push auf master — in
# JEDEM Klon. Die Regel lebt nur in scripts/hooks/pre-push-master-sperre.sh.
# Der Installer kopiert sie FEST ins gemeinsame Git-Verzeichnis
# (.git/hooks/pre-push-master-sperre.sh); der pre-push ruft nur diese Kopie auf.
# So entschärft eine Session die Sperre nicht über ihren Arbeitsordner (#1450).
# Erneuter Lauf ersetzt eine veraltete Kopie (Vergleich per cmp).
# Quelle (#1450 Fix-Runde 1): immer die Sperre aus origin/<Hauptzweig> des Klons
# (origin/HEAD, sonst master, sonst main) — wer die Sperre im Arbeitsordner ändert
# und den Installer startet, entschärft die gemeinsame Kopie nicht. Nur wenn
# origin den Pfad nicht hat: Rückfall auf die Datei neben diesem Skript, mit
# Warnung. Ohne jede Quelle bleibt die Kopie, wie sie ist, der pre-push wird
# trotzdem angelegt (meldet beim Push „fehlt“); Exit bleibt 0 (Session-Start).
# Eine volle Kopie der Sperre als pre-push (Hauptrepo, `master_schutz.sh hook-an`)
# bleibt stehen: master_schutz.sh erkennt seine Sperre nur per cmp.
kopie="$hooks/pre-push-master-sperre.sh"
neu="$kopie.neu"
neben="$(cd "$(dirname "$0")" && pwd)/pre-push-master-sperre.sh"
quelle=""
for ref in $(git -C "$repo" symbolic-ref --short -q refs/remotes/origin/HEAD 2>/dev/null) origin/master origin/main; do
  if git -C "$repo" show "$ref:scripts/hooks/pre-push-master-sperre.sh" >"$neu" 2>/dev/null; then
    quelle=$ref
    break
  fi
done
if [ -z "$quelle" ] && [ -f "$neben" ]; then
  cp "$neben" "$neu"
  quelle=$neben
  echo "⚠ pre-push: Sperre nicht in origin/<Hauptzweig> — Rückfall auf Arbeitsordner: $neben" >&2
fi
if [ -z "$quelle" ]; then
  rm -f "$neu"
  echo "⚠ pre-push: Sperre weder in origin/<Hauptzweig> noch neben $0 — Sperr-Kopie unverändert, pre-push wird trotzdem angelegt." >&2
elif [ -f "$kopie" ] && cmp -s "$kopie" "$neu"; then
  rm -f "$neu"
  echo "Sperr-Kopie ist aktuell: $kopie"
else
  chmod +x "$neu"
  mv -f "$neu" "$kopie"
  echo "Sperr-Kopie angelegt aus $quelle: $kopie"
fi

ziel="$hooks/pre-push"
if [ -f "$ziel" ] && cmp -s "$ziel" "$kopie"; then
  echo "pre-push ist volle Kopie der Sperre (master_schutz.sh), bleibt: $ziel"
  exit 0
fi
if [ -f "$ziel" ] && ! grep -q "pre-push-master-sperre.sh" "$ziel"; then
  cp "$ziel" "$ziel.vor-1442"
  echo "vorhandener pre-push gesichert: $ziel.vor-1442"
fi
cat > "$ziel" <<'EOF'
#!/bin/sh
# master-Sperre im Modus tickets (#1442). Feste Kopie neben diesem Hook (#1450),
# angelegt von scripts/hooks/install_git_hooks.sh — nie aus dem Arbeitsordner.
sperre="$(dirname "$0")/pre-push-master-sperre.sh"
if [ ! -f "$sperre" ]; then
  echo "⚠ pre-push: $sperre fehlt — master-Sperre (#1442) NICHT geprüft, Push läuft weiter. Neu: sh scripts/hooks/install_git_hooks.sh" >&2
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
