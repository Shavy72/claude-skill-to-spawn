#!/usr/bin/env bash
# pre-push: im Modus SCHLANGE_MODUS=tickets kein direkter Push auf master
# (Test-Gate Etappe 2). master bewegt nur die Deploy-Schlange. Im Modus master
# tut der Hook nichts. ticket/*- und alle anderen Branches blockt er nie.
#
# Modus: NUR aus der .env der Schlange (SCHLANGE_ENV_DATEI, Standard
# /home/bau/duoplus-management/.env), direkt per Shell gelesen (kein Python
# nötig). Eine Umgebungsvariable SCHLANGE_MODUS zählt hier bewusst NICHT —
# sonst käme `SCHLANGE_MODUS=master git push` an der Sperre vorbei.
# Schlüssel fehlt/leer = master (Standard der Schlange). .env unlesbar oder
# Wert unbekannt = Modus UNLESBAR: Push auf master gesperrt.
#
# Meldungen bei JEDER Sperre eines master-Pushes:
#   Telegram an David (curl, Token/Chat aus derselben .env, im Hintergrund,
#     höchstens eine je 15 min und Grund, Stempel bleibt nur nach Erfolg).
#     Test-Schalter: WAECHTER_MELDUNG_DATEI (sonst SCHLANGE_MELDUNG_DATEI) →
#     statt curl eine JSON-Zeile {"art":"telegram","text":…} anhängen.
#   Zusätzlich bei Modus UNLESBAR: E-Mail an David (scripts/to_spawn_mail.py,
#     nach erfolgreichem Versand höchstens eine je 15 min).
#
# Ausnahmen (ohne Meldung):
#   DEPLOY_SCHLANGE_MERGE=1  setzt nur deploy_schlange.py beim Fast-Forward auf master.
#   Notausgang David (ein Befehl, nur mit Begründung):
#     MASTER_PUSH_ERLAUBT=1 git push origin HEAD:master
# Einrichten/entfernen: bash scripts/master_schutz.sh hook-an | hook-aus
set -u
[ "${MASTER_PUSH_ERLAUBT:-}" = "1" ] && exit 0
[ "${DEPLOY_SCHLANGE_MERGE:-}" = "1" ] && exit 0

# Refs zuerst lesen: ohne Push auf master ist der Modus egal.
auf_master=0
master_quelle=""
while read -r lokal_ref _lokal_sha fern_ref _fern_sha; do
  if [ "$fern_ref" = "refs/heads/master" ]; then
    auf_master=1
    master_quelle=$lokal_ref
  fi
done
[ "$auf_master" = "1" ] || exit 0
# „git push origin HEAD:master“ liefert als lokalen Ref nur „HEAD“ → Branchnamen dazu.
if [ "$master_quelle" = "HEAD" ]; then
  master_quelle="$(git symbolic-ref -q --short HEAD 2>/dev/null || echo losgelöst) (HEAD)"
fi

env_datei=${SCHLANGE_ENV_DATEI:-/home/bau/duoplus-management/.env}

# Gleiche Regeln wie env_werte() in deploy_schlange.py: letzte Zeile gewinnt,
# „export “ erlaubt, Anführungszeichen und Leerraum weg. Werte nie ausgeben.
env_wert() {
  sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\?$1[[:space:]]*=\(.*\)\$/\2/p" "$env_datei" 2>/dev/null |
    tail -n 1 | sed "s/^[[:space:]]*//; s/[[:space:]]*\$//; s/^[\"']//; s/[\"']\$//"
}

if [ -r "$env_datei" ]; then
  modus=$(env_wert SCHLANGE_MODUS)
  modus=${modus:-master}
else
  modus="?"
fi
modus=$(printf '%s' "$modus" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
[ "$modus" = "master" ] && exit 0

gemeinsam=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || echo /tmp)
wurzel=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

# Telegram an David: im Hintergrund (Push wartet nicht), Drossel 15 min je Grund.
# Der Token geht nur über stdin an curl (nicht in ps sichtbar), curl-Fehlertexte
# werden verworfen, weil sie die URL mit Token enthalten können.
melde_telegram() {
  local grund=$1 kennung=$2 stempel text datei token chat
  stempel="$gemeinsam/master-sperre-telegram-$kennung.stempel"
  if [ -n "$(find "$stempel" -mmin -15 2>/dev/null)" ]; then
    return 0
  fi
  touch "$stempel" 2>/dev/null
  text="Wächter: Push auf master gesperrt ($grund) — Repo $wurzel, Branch ${master_quelle:-?}, Rechner $(hostname), Zeit $(date '+%F %T')"
  datei=${WAECHTER_MELDUNG_DATEI:-${SCHLANGE_MELDUNG_DATEI:-}}
  if [ -n "$datei" ]; then
    local json
    json=$(printf '%s' "$text" | sed 's/\\/\\\\/g; s/"/\\"/g')
    printf '{"art": "telegram", "text": "%s"}\n' "$json" >>"$datei" 2>/dev/null || rm -f "$stempel"
    return 0
  fi
  token=""
  chat=""
  if [ -r "$env_datei" ]; then
    token=$(env_wert TELEGRAM_BOT_TOKEN)
    chat=$(env_wert TELEGRAM_CHAT_ID)
  fi
  if [ -z "$token" ] || [ -z "$chat" ]; then
    echo "$(date '+%F %T') Telegram: Token oder Chat-ID fehlt — Meldung nur hier: $text" >>"$gemeinsam/master-sperre-telegram.log"
    rm -f "$stempel"
    return 0
  fi
  (
    if ! printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" |
      timeout 30 curl -fsS --config - --data-urlencode "chat_id=$chat" \
        --data-urlencode "text=$text" -o /dev/null >/dev/null 2>&1; then
      echo "$(date '+%F %T') Telegram ($kennung) fehlgeschlagen — nächste Sperre versucht neu" >>"$gemeinsam/master-sperre-telegram.log"
      rm -f "$stempel"
    fi
  ) </dev/null >/dev/null 2>&1 &
}

if [ "$modus" = "tickets" ]; then
  echo "✋ SCHLANGE_MODUS=tickets: kein direkter Push auf master." >&2
  echo "   Eigenen Branch pushen: git push origin HEAD:refs/heads/ticket/<nr>-<kurzname>" >&2
  echo "   Die Deploy-Schlange gatet ihn und legt ihn bei Grün auf master." >&2
  echo "   Rückmeldung: python scripts/ticket_status.py warte ticket/<nr>-<kurzname>" >&2
  melde_telegram "Modus tickets" tickets
  exit 1
fi

# Modus unlesbar → sperren (Davids Entscheidung 2026-09-25) und Bescheid geben.
echo "✋ Push auf master gesperrt: SCHLANGE_MODUS nicht lesbar ($env_datei)." >&2
echo "   Sicherheitshalber gilt die Sperre wie im Modus tickets. Mail an David ist unterwegs." >&2
echo "   Eigenen Branch pushen geht weiter: git push origin HEAD:refs/heads/ticket/<nr>-<kurzname>" >&2
melde_telegram "Modus unlesbar" unlesbar

stempel="$gemeinsam/master-sperre-mail.stempel"
if [ -n "$(find "$stempel" -mmin -15 2>/dev/null)" ]; then
  exit 1
fi
touch "$stempel" 2>/dev/null
mail_skript="$wurzel/scripts/to_spawn_mail.py"
[ -f "$mail_skript" ] || mail_skript=/home/bau/duoplus-management/scripts/to_spawn_mail.py
mail_befehl=${MASTER_SPERRE_MAIL:-python3 $mail_skript}
text="Ein Push auf master wurde gesperrt, weil SCHLANGE_MODUS nicht lesbar ist.
Datei: $env_datei
Rechner: $(hostname) · Ordner: $wurzel · Zeit: $(date '+%F %T')
Bitte .env prüfen. Notausgang: MASTER_PUSH_ERLAUBT=1 git push origin HEAD:master"
nutzlast=$(TEXT="$text" python3 -c 'import json, os; print(json.dumps({"art": "master_sperre", "betreff": "Push auf master gesperrt: Modus unlesbar", "text": os.environ["TEXT"], "an": ""}, ensure_ascii=False))' 2>/dev/null)
# Im Hintergrund: der Push wartet nicht auf ssh/SMTP. Scheitert der Versand,
# fällt der Stempel weg — die nächste Sperre versucht es erneut.
(
  if ! printf '%s' "$nutzlast" | timeout 150 $mail_befehl >>"$gemeinsam/master-sperre-mail.log" 2>&1; then
    echo "$(date '+%F %T') Mail an David fehlgeschlagen — nächste Sperre versucht neu" >>"$gemeinsam/master-sperre-mail.log"
    rm -f "$stempel"
  fi
) </dev/null >/dev/null 2>&1 &
exit 1
