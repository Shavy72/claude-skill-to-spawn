#!/usr/bin/env bash
# Neues Lauf-Repo für einen Probe-Lauf aus der Vorlage anlegen (#426 Teil F).
#
#   bash probe/anlegen.sh <ziel-ordner> <mit-staging|ohne-staging> \
#        [--app-port N] [--staging-port N] [--live-port N] [--github <owner/name>]
#
# Kopiert probe/gemeinsam/ und darüber probe/<variante>/ nach <ziel-ordner>, setzt die Ports ein
# (Vorgabe 18501 lokal, 18502 Staging, 18503 Übungs-Live), macht `git init` und einen ersten
# Commit. Das Beschluss-Protokoll der Probe-Spec liegt danach als docs/GRILL_<heute>_probe.md
# im Repo (Quelle probe/gemeinsam/docs/GRILL_probe.md). Mit --github: privates Repo anlegen und main pushen, sonst kein GitHub-Zugriff. Der Ziel-Ordner muss fehlen oder leer sein.
set -euo pipefail

hilfe() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; }
fehler() { echo "anlegen.sh: $*" >&2; exit 2; }

[ $# -ge 2 ] || { hilfe >&2; exit 2; }
ziel=$1
variante=$2
shift 2
app_port=18501
staging_port=18502
live_port=18503
github=
while [ $# -gt 0 ]; do
  case $1 in
    --app-port) app_port=${2:-}; shift 2 ;;
    --staging-port) staging_port=${2:-}; shift 2 ;;
    --live-port) live_port=${2:-}; shift 2 ;;
    --github) github=${2:-}; shift 2 ;;
    -h|--help) hilfe; exit 0 ;;
    *) fehler "unbekannter Schalter: $1" ;;
  esac
done

case $variante in
  mit-staging|ohne-staging) ;;
  *) fehler "Variante muss mit-staging oder ohne-staging sein, nicht „$variante“" ;;
esac
for port in "$app_port" "$staging_port" "$live_port"; do
  [[ $port =~ ^[0-9]+$ ]] && [ "$port" -ge 1024 ] && [ "$port" -le 65535 ] \
    || fehler "Port „$port“ ungültig (1024–65535)"
done
if [ "$variante" = ohne-staging ]; then
  [ "$app_port" != "$live_port" ] || fehler "Ports müssen verschieden sein"
else
  [ "$app_port" != "$staging_port" ] && [ "$app_port" != "$live_port" ] \
    && [ "$staging_port" != "$live_port" ] || fehler "Ports müssen verschieden sein"
fi
if [ -e "$ziel" ] && [ -n "$(ls -A "$ziel" 2>/dev/null)" ]; then
  fehler "Ziel „$ziel“ ist nicht leer"
fi

vorlage=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$ziel"
cp -R "$vorlage/gemeinsam/." "$ziel/"
cp -R "$vorlage/$variante/." "$ziel/"
find "$ziel" -type d \( -name __pycache__ -o -name .ruff_cache -o -name .pytest_cache \) \
  -prune -exec rm -rf {} +

if [ "$variante" = ohne-staging ]; then staging_text="keins (bewusst ohne Staging)"; else staging_text=$staging_port; fi
find "$ziel" -type f -exec sed -i \
  -e "s/@@APP_PORT@@/$app_port/g" \
  -e "s/@@STAGING_PORT@@/$staging_port/g" \
  -e "s/@@LIVE_PORT@@/$live_port/g" \
  -e "s/@@VARIANTE@@/$variante/g" \
  -e "s/@@STAGING_TEXT@@/$staging_text/g" {} +
# Beschluss-Protokoll der Probe-Spec mit Tagesdatum (Format docs/GRILL_<datum>_<thema>.md).
mv "$ziel/docs/GRILL_probe.md" "$ziel/docs/GRILL_$(date +%F)_probe.md"

cd "$ziel"
variante_zweig=main
git init -q -b "$variante_zweig"
git add -A
if [ -z "$(git config user.email || true)" ]; then
  git -c user.name=probe -c user.email=probe@localhost -c commit.gpgsign=false commit -q --no-verify -m "chore: Probe-App ($variante) aus Vorlage"
else
  git -c commit.gpgsign=false commit -q --no-verify -m "chore: Probe-App ($variante) aus Vorlage"
fi
if [ -n "$github" ]; then
  # #467: privates Repo anlegen und den Start-Commit pushen (sonst hat der erste PR kein Ziel).
  gh repo create "$github" --private --source . --remote origin || {
    echo "Fehler: gh repo create fehlgeschlagen; lokaler Ordner bleibt stehen: $(pwd) (wiederverwenden oder löschen)" >&2
    exit 1
  }
  # Über Variable: frisches leeres Probe-Repo, der erste Push von main ist dort der gewollte
  # Start-Commit; kein Weg für echte Repos.
  git push -u origin "$variante_zweig"
fi
echo "Lauf-Repo angelegt: $(pwd) ($variante; lokal $app_port, Staging $staging_text, Übungs-Live $live_port)"
