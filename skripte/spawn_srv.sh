#!/usr/bin/env bash
# spawn_srv.sh — alle Ticket-Sessions einer Spec auf dem Bau-Server starten.
#
# Aufruf (auf dem Server, als Nutzer bau):
#   bash scripts/spawn_srv.sh <S> [--tickets 179,188] [--ohne-wache] [--dry-run]
#        [--umzug <branch>@<sha>:<pfad>] [--ohne-regularien] [--nur-wache]
#
# Umzug (#212): --umzug nur mit genau einem Ticket, startet ``bau <N> --umzug <ref>``
# (Handoff aus Commit <sha> auf origin/<branch> als Startkontext; ``<branch>:<pfad>``
# ohne SHA geht weiter) und prüft keine Regularien. Läuft das Ticket hier schon:
# Exit 4, nichts gestartet. --nur-wache startet nur Aufseher + Leitstand (fehlende).
# Exit 5: Speicher voll (to_spawn.py speicher) — begonnene Fenster laufen, der Rest nicht.
#   Läuft der Aufseher (``wache <S>``) schon, Exit 0 mit „wartet auf Platz“: er startet nach (#460).
# Exit 7: Platte voll (Start-Check des Hausmeisters, #1369) — wie Exit 5.
# Exit 6: Speicherprüfung kaputt oder Hauptzweig nicht ermittelbar — nichts weiter gestartet.
# Exit 9: Session ``spec-<S>`` gehört einem anderen Repo (gleiche Spec-Nummer, #457) — nichts gestartet.
# Exit 10: ein anderer Lauf für dieselbe Spec hält die Start-Sperre (#560) — nichts gestartet.
# Eine tmux-Session je Spec (``spec-<S>``), darin ``wache <S>``, ``leitstand <S>`` und
# je Ticket ein Fenster ``bau <N>``. Gewartet wird in bau.py (0 Token).
# Kontrolle: ``sessions <S>`` · ``tmux attach -t spec-<S>`` (raus: Strg+B d).
set -euo pipefail

# Repo: TO_SPAWN_REPO (setzt die Weiterleitung scripts/spawn_srv.sh im Repo), sonst Git-Wurzel
# des aktuellen Ordners. Skill-Ordner = Ordner über skripte/ (dieses Skript liegt im Skill, #205).
REPO="${TO_SPAWN_REPO:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
# Windows (Git Bash): ``pwd -W`` liefert ``C:/…`` statt ``/c/…`` — Python/Node verstehen nur die
# Windows-Form, wenn Claude Code ``MSYS_NO_PATHCONV=1`` vererbt (keine Pfad-Umsetzung). Linux: ``pwd``.
SKILL_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && { pwd -W 2>/dev/null || pwd; })"
cd "$REPO"

SPEC=""
TICKETS_ARG=""
OHNE_WACHE=0
DRY_RUN=0
UMZUG_REF=""
OHNE_REGULARIEN=0
NUR_WACHE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tickets) TICKETS_ARG="${2:-}"; shift 2 ;;
    --ohne-wache) OHNE_WACHE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --umzug) UMZUG_REF="${2:-}"; OHNE_REGULARIEN=1; shift 2 ;;
    --ohne-regularien) OHNE_REGULARIEN=1; shift ;;
    --nur-wache) NUR_WACHE=1; shift ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)
      if [ -z "$SPEC" ]; then SPEC="$1"; shift; else echo "Unbekanntes Argument: $1" >&2; exit 2; fi ;;
  esac
done
[ -n "$SPEC" ] || { echo "Spec-Nummer fehlt. Aufruf: bash scripts/spawn_srv.sh <S> [--tickets a,b] [--ohne-wache] [--dry-run]" >&2; exit 2; }
if [ -n "$UMZUG_REF" ]; then
  # Umzug (#212): genau ein Ticket, nie zusammen mit --nur-wache.
  if [ "$NUR_WACHE" -eq 1 ] || [ -z "$TICKETS_ARG" ] || [[ "$TICKETS_ARG" == *,* ]]; then
    echo "--umzug geht nur mit genau einem Ticket (--tickets <N>) und ohne --nur-wache." >&2
    exit 2
  fi
  case "$UMZUG_REF" in
    ?*:?*) ;;
    *) echo "--umzug erwartet <branch>@<sha>:<pfad>, bekam: '$UMZUG_REF'" >&2; exit 2 ;;
  esac
fi
if [ "$NUR_WACHE" -eq 1 ] && [ "$OHNE_WACHE" -eq 1 ]; then
  echo "--nur-wache und --ohne-wache schließen sich aus." >&2
  exit 2
fi

# Python für die Skill-Aufrufe: Repo-venv, wenn vorhanden, sonst python3 (#257 F4: ein PY für alles).
# Windows (Git-Bash): python3 ist dort oft nur der Store-Platzhalter → "python" wie
# leitstand_fenster.python_befehl; venv dort unter Scripts/.
PY=python3
case "$(uname -s)" in MINGW* | MSYS* | CYGWIN*) PY=python ;; esac
[ -x "$REPO/.venv/bin/python" ] && PY="$REPO/.venv/bin/python"
[ -x "$REPO/.venv/Scripts/python.exe" ] && PY="$REPO/.venv/Scripts/python.exe"

# Manifest-Ordner aus der Repo-Konfig (pfade.manifeste, L1 P4b); fehlt der Schlüssel, gilt die
# Vorgabe aus to_spawn/config.py. Nicht ermittelbar → Abbruch, kein stiller Rückfall (F4).
MANIFESTE="$(TO_SPAWN_REPO="$REPO" "$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from to_spawn import config; print(config.pfad(config.lade(Path(sys.argv[2])), "manifeste").as_posix())' "$SKILL_HOME" "$REPO")" \
  || { echo "Manifest-Ordner nicht ermittelbar (to_spawn.config.pfad)." >&2; exit 2; }
MANIFEST="$MANIFESTE/spec-$SPEC.json"
[ -f "$MANIFEST" ] || { echo "Manifest fehlt: $MANIFEST" >&2; exit 2; }
command -v tmux >/dev/null || { echo "tmux ist nicht installiert." >&2; exit 2; }

# --- Session je Repo (#457) --------------------------------------------------
# Der Name ``spec-<S>`` enthält das Repo nicht; jedes frische Repo beginnt bei Spec #1. Gehört
# eine vorhandene Session ``spec-<S>`` einem anderen Repo, legt dieser Start nichts hinein
# (Exit 9). Besitzer = Arbeitsordner der Session (``-c`` beim Anlegen, hier und in capo.py),
# Repo-Kennung = ``origin``-Adresse (ein anderer Klon desselben Repos ist dieselbe Spec), ohne
# ``origin`` der echte Pfad.
SESSION="spec-$SPEC"
repo_kennung() {
  local url
  url="$(git -C "$1" remote get-url origin 2>/dev/null || true)"
  if [ -n "$url" ]; then echo "${url%.git}"; else (cd "$1" 2>/dev/null && pwd -P) || echo "$1"; fi
}
if tmux has-session -t "=$SESSION" 2>/dev/null; then
  SESSION_ORDNER="$(tmux display-message -p -t "=$SESSION:" '#{session_path}' 2>/dev/null || true)"
  if [ -n "$SESSION_ORDNER" ] && [ "$(repo_kennung "$SESSION_ORDNER")" != "$(repo_kennung "$REPO")" ]; then
    echo "WEIGERUNG (#457): tmux-Session $SESSION gehört einem anderen Repo ($(repo_kennung "$SESSION_ORDNER"), Ordner $SESSION_ORDNER), nicht $(repo_kennung "$REPO") — nichts gestartet. Erst die andere Spec #$SPEC beenden (Session $SESSION schließen), dann erneut." >&2
    exit 9
  fi
fi

# --- 1. Stand holen (nie rebase/stash im geteilten Baum) --------------------
# Hauptzweig je Repo (master/main/…, #257): Konfig, origin/HEAD, main, master. Leer oder
# Fehler → Abbruch Exit 6, kein stiller Rückfall (F4).
rc=0
HAUPT="$(TO_SPAWN_REPO="$REPO" "$PY" "$SKILL_HOME/to_spawn.py" hauptzweig 2>&1)" || rc=$?
if [ "$rc" -ne 0 ] || [ -z "$HAUPT" ]; then
  echo "Hauptzweig nicht ermittelbar (to_spawn.py hauptzweig Exit $rc): ${HAUPT:-<leer>}" >&2
  exit 6
fi
echo "== Stand (origin/$HAUPT) =="
# Erst-Start (#283): nachziehen (nur fast-forward) oder Abbruch mit Zeile — Regel allein in
# to_spawn/arbeitsstand.py. fetch-Fehler/Ticket-Zweig = Hinweis, weiter. Probelauf prüft nur.
STAND_ARGS=(arbeitsstand)
if [ "$DRY_RUN" -eq 1 ]; then STAND_ARGS+=(--pruefen); fi
rc=0
TO_SPAWN_REPO="$REPO" "$PY" "$SKILL_HOME/to_spawn.py" "${STAND_ARGS[@]}" || rc=$?
if [ "$rc" -ne 0 ] && [ "$DRY_RUN" -ne 1 ]; then
  echo "Arbeitsordner nicht auf Hauptzweig-Stand — nichts gestartet." >&2
  exit 1
fi

# --- 1b. Regularien (#206) — auch im Probelauf; Weigerung startet nichts ----
# Eine Ticket-Auswahl (--tickets) geht mit in die Prüfung: jede Nummer muss im
# Manifest stehen, die Regeln laufen trotzdem über das ganze Manifest.
echo "== Regularien =="
if [ "$OHNE_REGULARIEN" -eq 1 ]; then
  echo "Umzug: Regularien galten beim ersten Start — übersprungen."
elif [ ! -f "$SKILL_HOME/to_spawn.py" ]; then
  echo "WEIGERUNG: Regularien-Prüfer fehlt ($SKILL_HOME/to_spawn.py) — Skill to-spawn installieren (github.com/Shavy72/claude-skill-to-spawn), dann erneut." >&2
  exit 3
else
  PRUEF_ARGS=(pruefen "$SPEC")
  if [ -n "$TICKETS_ARG" ]; then PRUEF_ARGS+=(--tickets "$TICKETS_ARG"); fi
  # Probelauf und --nur-wache (startet kein Ticket-Fenster) legen kein Einrichtungs-Ticket an (#410);
  # --nur-wache ist kein Probelauf: Konfig-Einrichtung (config.sicherstellen) läuft.
  if [ "$DRY_RUN" -eq 1 ]; then PRUEF_ARGS+=(--dry-run); fi
  if [ "$NUR_WACHE" -eq 1 ]; then PRUEF_ARGS+=(--ohne-einrichtung); fi
  rc=0
  "$PY" "$SKILL_HOME/to_spawn.py" "${PRUEF_ARGS[@]}" || rc=$?
  if [ "$rc" -eq 3 ]; then
    echo "WEIGERUNG — nichts gestartet." >&2
    exit 3
  elif [ "$rc" -ne 0 ]; then
    echo "Regularien-Prüfer brach ab (Exit $rc) — nichts gestartet." >&2
    exit "$rc"
  fi
fi

# --- 2. Tickets bestimmen ---------------------------------------------------
if [ "$NUR_WACHE" -eq 1 ]; then
  TICKETS=""
elif [ -n "$TICKETS_ARG" ]; then
  TICKETS="$(echo "$TICKETS_ARG" | tr ',' '\n' | sed '/^\s*$/d')"
else
  TICKETS="$("$PY" -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print('\n'.join(sorted((d.get('tickets') or {}), key=int)))" "$MANIFEST")"
fi
[ -n "$TICKETS" ] || [ "$NUR_WACHE" -eq 1 ] || { echo "Keine Tickets im Manifest $MANIFEST." >&2; exit 2; }

# --- 3. Was läuft schon? (nie Duplikate) ------------------------------------
# Sperre je Spec (#560): Prüfen „läuft schon?“ + Speicher und Starten sind ein Schritt — ein
# zweiter Lauf (Nachstarter aus wache.py, Aufpasser, Hand) wartet, sonst starteten beide
# dasselbe Ticket oder überschritten gemeinsam die Session-Obergrenze (#460). Exit 10 = Sperre
# nach TO_SPAWN_SPERRE_WARTEN_S (300) noch belegt. tmux-Aufrufe schließen fd 9 (``9>&-``),
# sonst hielten die Fenster die Sperre für immer.
# ponytail: ohne flock (Git Bash unter Windows) keine Sperre, dort läuft kein Nachstarter-Takt parallel
if [ "$DRY_RUN" -eq 0 ] && command -v flock >/dev/null; then
  mkdir -p "$REPO/.to-spawn"
  exec 9>"$REPO/.to-spawn/spawn-$SPEC.sperre"
  if ! flock -w "${TO_SPAWN_SPERRE_WARTEN_S:-300}" 9; then
    echo "Ein anderer spawn_srv.sh-Lauf für Spec #$SPEC startet gerade Fenster — nichts gestartet, später erneut." >&2
    exit 10
  fi
fi
STAND="$(TO_SPAWN_REPO="$REPO" "$PY" "$SKILL_HOME/skripte/sessions_stand.py" "$SPEC" --alle 2>/dev/null || true)"
zustand_von() { echo "$STAND" | awk -v n="#$1" '$1==n {print $2; exit} ($1=="Spec" && $2==n) {print $3; exit}'; }
# Lebt jetzt ein Aufseher-Prozess (``wache.py <S>`` in diesem Repo)? Frisch aus der Prozessliste,
# gleiche Erkennung wie oben — ein Fenster ``wache <S>`` ohne Prozess startet niemanden nach (#460).
aufseher_laeuft() {
  local STAND z
  STAND="$(TO_SPAWN_REPO="$REPO" "$PY" "$SKILL_HOME/skripte/sessions_stand.py" "$SPEC" --alle 2>/dev/null || true)"
  z="$(zustand_von "$SPEC")"
  [ -n "$z" ] && [ "$z" != "aus" ]
}

# Fenster laufen im Repo und mit dem Skill dieses Aufrufs — nicht mit den Vorgaben aus
# ~/.bashrc (``REPO``), sonst startet ``bau`` in einem anderen Repo (#212). Seit #257 rufen
# die Fenster die Skill-Skripte direkt auf (``bau.py``/``wache.py``), nicht die Shell-
# Funktionen ``bau``/``wache`` aus ~/.bashrc — ein Fremd-Repo hat die nicht.
# Warte-Datei (#560): Tickets, die „wartet auf Platz“ bekamen; der Aufseher (wache.py) startet sie
# daraus in Sekunden nach, sobald Platz frei ist. Schreiben und Löschen nur hier, unter der Sperre.
WARTE_DATEI="$REPO/.to-spawn/wartet-auf-platz-$SPEC"
# >>> warteliste (#560) — Format: eine Zeile, Ticketnummern mit Komma, z. B. ``901,902``.
# warteliste_aendern <hinzu> <weg>: vorhandene Liste ∪ <hinzu> ohne <weg> (beides Komma-Listen),
# ohne Doppelte, alte Reihenfolge zuerst; in einem Zug per mv geschrieben (ein Leser sieht nie
# eine halbe Datei), leer → Datei weg. Trockenlauf ändert nichts.
warteliste_aendern() {
  [ "$DRY_RUN" -eq 0 ] || return 0
  local alt="" neu
  if [ -f "$WARTE_DATEI" ]; then alt="$(cat "$WARTE_DATEI")"; fi
  neu="$(printf '%s,%s\n' "$alt" "$1" | tr ',' '\n' | awk -v weg=",$2," 'NF && !index(weg, "," $1 ",") && !s[$1]++ {print $1}' | paste -sd, -)"
  if [ -z "$neu" ]; then rm -f "$WARTE_DATEI"; return 0; fi
  mkdir -p "${WARTE_DATEI%/*}"
  printf '%s\n' "$neu" > "$WARTE_DATEI.$$.neu" && mv -f "$WARTE_DATEI.$$.neu" "$WARTE_DATEI"
}
# <<< warteliste
# LAEUFT = Tickets dieses Laufs, die schon liefen oder gestartet wurden — nur die trägt der Lauf aus.
LAEUFT=""
VORSPANN="export REPO=$(printf %q "$REPO") TO_SPAWN_HOME=$(printf %q "$SKILL_HOME") TO_SPAWN_REPO=$(printf %q "$REPO") TO_SPAWN_WARTE_DATEI=$(printf %q "$WARTE_DATEI");"
# GEPLANT = echte Fensterbefehle, KURZ = Kurzform ``bau <N> [--umzug …]`` / ``wache <S>`` für
# Anzeige und Fenstername (erste zwei Wörter; sessions_stand/aufpasser lesen die Namen) —
# beide Listen laufen parallel.
GEPLANT=()
KURZ=()
if [ "$OHNE_WACHE" -eq 0 ]; then
  z="$(zustand_von "$SPEC")"
  if [ -z "$z" ] || [ "$z" = "aus" ]; then
    # Stirbt wache.py (Absturz, OOM-Kill), startet das Fenster es bis zu 3-mal neu (#560) —
    # sonst läge der wartende Rest still, bis der Aufpasser-Takt das Fenster neu anlegt.
    # Absturz = Exit 1 (Python-Traceback) oder Signal ABRT/KILL/SEGV (134/137/139); gewollte
    # Enden (0, 2 Konfig/Startklar, 130 Strg+C, 143 SIGTERM, Claude-Exit) starten nicht neu.
    # ponytail: feste 3 Versuche à 5 s, Zähler/Meldung über den Aufpasser wenn Abstürze häufig sind
    WACHE_BEFEHL="$(printf %q "$PY") $(printf %q "$SKILL_HOME/skripte/wache.py") $SPEC"
    GEPLANT+=("v=0; while :; do $WACHE_BEFEHL; rc=\$?; case \$rc in 1|134|137|139) ;; *) break ;; esac; [ \$v -lt 3 ] || break; v=\$((v + 1)); echo \"wache.py Exit \$rc — Neustart \$v/3\" >&2; sleep \${TO_SPAWN_WACHE_NEUSTART_S:-5}; done")
    KURZ+=("wache $SPEC")
  else
    echo "Aufseher für Spec #$SPEC läuft bereits ($z) — übersprungen."
  fi
  # Bau-Leitstand (Live-Seite): Fenster ``leitstand <S>`` mit interaktiver Sonnet-Session, nur bei
  # leitstand.aktiv an (Exit 0) und wenn das Fenster noch fehlt — auch nachträglich per --nur-wache.
  if ! TO_SPAWN_REPO="$REPO" "$PY" "$SKILL_HOME/skripte/leitstand.py" "$SPEC" aktiv >/dev/null 2>&1; then
    echo "Leitstand für Spec #$SPEC aus (leitstand.aktiv) — kein Fenster."
  elif tmux list-windows -t "=$SESSION" -F '#W' 2>/dev/null | grep -qx "leitstand $SPEC"; then
    echo "Leitstand für Spec #$SPEC läuft bereits — übersprungen."
  else
    GEPLANT+=("$(printf %q "$PY") $(printf %q "$SKILL_HOME/skripte/leitstand.py") $SPEC sitzung")
    KURZ+=("leitstand $SPEC")
  fi
fi
for n in $TICKETS; do
  z="$(zustand_von "$n")"
  if [ -z "$z" ] || [ "$z" = "aus" ]; then
    if [ -n "$UMZUG_REF" ]; then
      GEPLANT+=("$(printf %q "$PY") $(printf %q "$SKILL_HOME/skripte/bau.py") $n --umzug $(printf %q "$UMZUG_REF")")
      KURZ+=("bau $n --umzug $(printf %q "$UMZUG_REF")")
    else
      GEPLANT+=("$(printf %q "$PY") $(printf %q "$SKILL_HOME/skripte/bau.py") $n")
      KURZ+=("bau $n")
    fi
  elif [ -n "$UMZUG_REF" ]; then
    # Umzug: nie eine zweite Session für dasselbe Ticket — der Umzug bricht ab (Exit 4).
    echo "Ticket #$n läuft auf dem Server bereits ($z) — Umzug abgebrochen, nichts gestartet." >&2
    exit 4
  else
    echo "Ticket #$n läuft bereits ($z) — übersprungen."
    LAEUFT="$LAEUFT,$n"
  fi
done

# Teil-Start-Auswahl je Spec und Rechner merken (#271), damit Aufpasser, Aufseher-Stand,
# sessions_stand, neustart, capo und Takt begrenzt bleiben. Trockenlauf und Nachstart aus
# wache.py ändern die gespeicherte Auswahl nicht.
if [ "$DRY_RUN" -eq 0 ] && [ "${TO_SPAWN_NACHSTART:-0}" != "1" ]; then
  AUSWAHL_ARGS=(auswahl "$SPEC")
  if [ "$NUR_WACHE" -eq 1 ]; then
    AUSWAHL_ARGS+=(--nur-wache)
  elif [ -n "$TICKETS_ARG" ]; then
    AUSWAHL_ARGS+=(--tickets "$TICKETS_ARG")
  fi
  if [ "$OHNE_WACHE" -eq 1 ]; then AUSWAHL_ARGS+=(--ohne-wache); fi
  if [ -n "$UMZUG_REF" ]; then AUSWAHL_ARGS+=(--umzug); fi
  TO_SPAWN_REPO="$REPO" "$PY" "$SKILL_HOME/to_spawn.py" "${AUSWAHL_ARGS[@]}"
fi

if [ "${#GEPLANT[@]}" -eq 0 ]; then
  echo "Nichts zu starten — alle Fenster laufen schon."
  warteliste_aendern "" "$LAEUFT"
  exit 0
fi

echo "== Geplante Fenster in tmux-Session $SESSION =="
for i in "${!GEPLANT[@]}"; do echo "  - ${KURZ[$i]}  →  ${GEPLANT[$i]}"; done

# Speicher-Schutz (#257 Paket B): vor JEDEM Fenster fragt ``to_spawn.py speicher``, ob noch
# Platz ist (Exit 0 = frei, 5 = voll, 3 = voll durch fremde Sessions → Rest nicht starten, alles andere = Prüfung kaputt →
# Exit 6, F4). Zwischen zwei Fenstern eine Pause von STAFFEL Sekunden, damit die
# Claude-Starts sich nicht stapeln (OOM-Absturz 21.09.).
STAFFEL="$(TO_SPAWN_REPO="$REPO" "$PY" "$SKILL_HOME/to_spawn.py" speicher --staffel 2>/dev/null || echo 20)"
case "$STAFFEL" in ''|*[!0-9]*) STAFFEL=20 ;; esac

# speicher_pruefen <Kurzname>: 0 = frei, 5 = voll (Meldung), 7 = Platte voll (Meldung, #1369),
# 6 = Prüfung kaputt (Meldung).
# Zweites Argument --trocken (Probelauf): nur messen, der Hausmeister löscht nie.
# TO_SPAWN_TMUX leer: die Sitzungs-Suche fragt dasselbe blanke ``tmux`` wie die Fenster unten
# (#409) — sonst zählte sie auf einem anderen tmux-Server als dem, in dem $SESSION entsteht.
# 8 = voll, aber der Aufseher-Prozess lebt (#460): keine Meldung hier, der Aufrufer meldet „wartet auf Platz“
# mit dem Grund aus WARTE_GRUND.
# speicher Exit 3 = voll nur durch Sessions außerhalb von $SESSION (#460): das Fenster ``wache <S>``
# öffnet trotzdem — wache.py wartet darin selbst auf Platz (auf_platz_warten, gleiche Grenzen);
# alle anderen Fenster behandeln es wie 5.
WARTE_GRUND=""
NL=$'\n'  # letzte Zeile des Stands = das Urteil (davor stehen Hausmeister-Warnungen)
speicher_pruefen() {
  local rc=0 stand
  stand="$(TO_SPAWN_TMUX= TO_SPAWN_REPO="$REPO" "$PY" "$SKILL_HOME/to_spawn.py" speicher --sitzung "$SESSION" ${2:+"$2"} 2>&1)" || rc=$?
  case "$rc" in
    0) return 0 ;;
    3|5)
       if [ "$rc" -eq 3 ] && [ -z "${2:-}" ] && [ "${1%% *}" = "wache" ]; then
         echo "Aufseher-Fenster öffnet, wartet darin auf Platz: $1 — ${stand##*"$NL"}"
         return 0
       fi
       if [ -z "${2:-}" ] && aufseher_laeuft; then WARTE_GRUND="${stand##*"$NL"}"; return 8; fi
       echo "nicht gestartet: $1 — $stand" >&2; return 5 ;;
    7) echo "nicht gestartet: $1 — $stand" >&2; return 7 ;;
    *) echo "Speicherprüfung kaputt (to_spawn.py speicher Exit $rc): $stand" >&2; return 6 ;;
  esac
}

if [ "$DRY_RUN" -eq 1 ]; then
  echo "Staffel: $STAFFEL s zwischen den Fenstern, Speicherprüfung vor jedem Start"
  rc=0
  speicher_pruefen "Probelauf" --trocken || rc=$?
  [ "$rc" -eq 6 ] && exit 6
  echo "Probelauf — nichts gestartet."
  exit 0
fi

# --- 4. tmux-Fenster anlegen ------------------------------------------------
GESTARTET=0
for i in "${!GEPLANT[@]}"; do
  befehl="${GEPLANT[$i]}"
  if [ "$GESTARTET" -gt 0 ]; then
    echo "Staffel: $STAFFEL s Pause vor dem nächsten Fenster"
    sleep "$STAFFEL"
  fi
  rc=0
  speicher_pruefen "${KURZ[$i]}" || rc=$?
  # Kein Platz, aber der Aufseher läuft (#460): er startet die fehlenden Fenster selbst nach,
  # sobald Platz frei ist — das ist der vorgesehene Weg, kein Fehler.
  if [ "$rc" -eq 8 ]; then
    # ponytail: Umzug-Fenster nicht vermerkt (brauchen ihre Ref), die startet der Aufpasser-Takt nach
    if [ -z "$UMZUG_REF" ]; then
      # Rest-bau-Fenster zur vorhandenen Liste dazu, Laufendes austragen (#560).
      liste="$(printf '%s\n' "${KURZ[@]:$i}" | awk '$1=="bau"{print $2}' | paste -sd, -)"
      warteliste_aendern "$liste" "$LAEUFT"
    fi
    echo "wartet auf Platz: ${KURZ[*]:$i} — $WARTE_GRUND; Aufseher startet nach, sobald Platz frei ist."
    exit 0
  fi
  if [ "$rc" -ne 0 ]; then
    echo "Rest bleibt aus; später erneut: bash scripts/spawn_srv.sh $SPEC" >&2
    exit "$rc"
  fi
  # Fenstername = die ersten zwei Wörter der Kurzform (``bau <N>`` / ``wache <S>``), auch mit --umzug.
  name="$(echo "${KURZ[$i]}" | awk '{print $1" "$2}')"
  if tmux has-session -t "=$SESSION" 2>/dev/null; then
    tmux new-window -t "=$SESSION" -n "$name" -c "$REPO" bash -lc "$VORSPANN $befehl" 9>&-
  else
    tmux new-session -d -s "$SESSION" -n "$name" -c "$REPO" bash -lc "$VORSPANN $befehl" 9>&-
  fi
  echo "gestartet: $name ($befehl)"
  case "$name" in "bau "*) LAEUFT="$LAEUFT,${name#bau }" ;; esac
  GESTARTET=$((GESTARTET + 1))
done
warteliste_aendern "" "$LAEUFT"

echo "== Anlauf (10 s) =="
sleep 10
TO_SPAWN_REPO="$REPO" "$PY" "$SKILL_HOME/skripte/sessions_stand.py" "$SPEC" --alle || true
echo
echo "Kontrolle: sessions $SPEC · tmux attach -t $SESSION (Fenster wechseln Strg+B n, raus Strg+B d)"
