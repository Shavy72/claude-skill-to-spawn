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
# Exit 8: mindestens ein Bau-Fenster endete direkt nach dem Start mit Exit ≠ 0 (#641) — Aufseher über Briefkasten geweckt.
# Exit 9: Session ``spec-<S>`` gehört einem anderen Repo (gleiche Spec-Nummer, #457) — nichts gestartet.
# Exit 10: ein anderer Lauf für dieselbe Spec hält die Start-Sperre (#560, #604) — später erneut, kein Fehler.
# Alle Exit-Codes für Aufrufer an einer Stelle: to_spawn/spawn_exit.py.
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

# --- Session je Repo (#457) + Hauptzweig (#257) + Staffel (#257) -------------
# Ein gemeinsamer Python-Aufruf statt drei CLI-Starts (gleiche Quellen: tmux_aufruf, gh,
# speicher — entspricht ``to_spawn.py sitzung``, ``hauptzweig`` und ``speicher --staffel``).
# Exit 9: Session gehört anderem Repo; Exit 6: Hauptzweig leer/kaputt; sonst Exit 2.
rc=0
START_DATEN="$(TO_SPAWN_TMUX= TO_SPAWN_REPO="$REPO" "$PY" -c '
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from to_spawn import config, gh, speicher, tmux_aufruf
repo = config.repo_wurzel(Path(sys.argv[2]))
name, fremd = tmux_aufruf.sitzung_aufloesen(repo, sys.argv[3])
if fremd:
    print(fremd, file=sys.stderr)
    sys.exit(tmux_aufruf.EXIT_FREMD)
if not name:
    sys.exit(2)
try:
    haupt = gh.hauptzweig(repo)
except Exception as fehler:
    print(f"Hauptzweig nicht ermittelbar: {fehler}", file=sys.stderr)
    sys.exit(6)
if not haupt:
    print("Hauptzweig nicht ermittelbar: <leer>", file=sys.stderr)
    sys.exit(6)
try:
    staffel = speicher.staffel_s(config.lade(repo))
except Exception:
    staffel = 20
print(f"{name}\t{haupt}\t{staffel}")
' "$SKILL_HOME" "$REPO" "$SPEC")" || rc=$?
if [ "$rc" -ne 0 ] || [ -z "$START_DATEN" ]; then
  [ "$rc" -eq 9 ] && exit 9
  [ "$rc" -eq 6 ] && exit 6
  echo "tmux-Session-Name nicht ermittelbar (Exit $rc)." >&2
  exit 2
fi
IFS=$'\t' read -r SESSION HAUPT STAFFEL <<< "$START_DATEN"
case "$STAFFEL" in ''|*[!0-9]*) STAFFEL=20 ;; esac

# --- 1. Stand holen (nie rebase/stash im geteilten Baum) --------------------
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
# Sperre je Spec (#560, #604): schützt Lesen/Schreiben der Warteliste und die Start-Entscheidung
# (läuft schon? + Speicher + tmux-Start), NICHT die Staffel-Pausen und NICHT den Anlauf — so
# blockiert ein zweiter Lauf nicht minutenlang. Exit 10 = Sperre nach TO_SPAWN_SPERRE_WARTEN_S
# (300) noch belegt (Exit-Codes: to_spawn/spawn_exit.py). tmux-Aufrufe schließen fd 9 (``9>&-``),
# sonst hielten die Fenster die Sperre für immer. Fehlt ``flock`` (Git Bash unter Windows),
# erscheint genau eine Hinweis-Zeile auf stderr.
SPERRE_DATEI="$REPO/.to-spawn/spawn-$SPEC.sperre"
SPERRE_GEHALTEN=0
SPERRE_WARNUNG_GEZEIGT=0
sperre_holen() {
  [ "$DRY_RUN" -eq 0 ] || return 0
  [ "$SPERRE_GEHALTEN" -eq 0 ] || return 0
  if ! command -v flock >/dev/null 2>&1; then
    if [ "$SPERRE_WARNUNG_GEZEIGT" -eq 0 ]; then
      echo "Hinweis: flock fehlt — Start-Sperre für Spec #$SPEC nicht aktiv (kein Parallelschutz)." >&2
      SPERRE_WARNUNG_GEZEIGT=1
    fi
    return 0
  fi
  mkdir -p "$REPO/.to-spawn"
  exec 9>"$SPERRE_DATEI"
  if ! flock -w "${TO_SPAWN_SPERRE_WARTEN_S:-300}" 9; then
    exec 9>&-
    echo "Ein anderer spawn_srv.sh-Lauf für Spec #$SPEC startet gerade Fenster — nichts gestartet, später erneut." >&2
    exit 10
  fi
  SPERRE_GEHALTEN=1
}
sperre_frei() {
  [ "$SPERRE_GEHALTEN" -eq 1 ] || return 0
  flock -u 9 2>/dev/null || true
  exec 9>&-
  SPERRE_GEHALTEN=0
}
sperre_holen
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
# Warte-Datei (#560, #604): Tickets, die „wartet auf Platz“ bekamen (auch ``--umzug``); der
# Aufseher (wache.py) startet sie daraus in Sekunden nach, sobald Platz frei ist. Schreiben und
# Löschen nur hier, unter der Sperre.
WARTE_DATEI="$REPO/.to-spawn/wartet-auf-platz-$SPEC"
# >>> warteliste (#560, #604) — Format: optional eine Zeile normale Ticketnummern mit Komma
# (z. B. ``901,902``) sowie optional je eine Zeile ``umzug <N> <ref>`` für wartende Umzug-Fenster.
# warteliste_aendern <hinzu> <weg> [umzug_ticket] [umzug_ref]: vorhandene Liste ∪ <hinzu> ohne
# <weg>, ohne Doppelte, alte Reihenfolge zuerst; in einem Zug per mv geschrieben (ein Leser sieht
# nie eine halbe Datei), leer → Datei weg. Trockenlauf ändert nichts.
warteliste_aendern() {
  [ "$DRY_RUN" -eq 0 ] || return 0
  local hinzu="${1:-}" weg="${2:-}" umzug_n="${3:-}" umzug_ref="${4:-}"
  local alt_norm="" alt_umzug="" neu_norm="" neu_umzug="" weg_norm="" inhalt=""
  if [ -f "$WARTE_DATEI" ]; then
    alt_norm="$(awk '$1!="umzug" && NF {print}' "$WARTE_DATEI" | paste -sd, -)"
    alt_umzug="$(awk '$1=="umzug" && NF>=3 {print}' "$WARTE_DATEI")"
  fi
  weg_norm="$weg"
  [ -n "$umzug_n" ] && weg_norm="$weg_norm,$umzug_n"
  neu_norm="$(printf '%s,%s\n' "$alt_norm" "$hinzu" | tr ',' '\n' | awk -v weg=",$weg_norm," 'NF && !index(weg, "," $1 ",") && !s[$1]++ {print $1}' | paste -sd, -)"
  neu_umzug="$(
    if [ -n "$alt_umzug" ]; then
      printf '%s\n' "$alt_umzug" | awk -v weg=",$weg_norm," '$1=="umzug" && NF>=3 && !index(weg, "," $2 ",") && !s[$2]++ {print}'
    fi
    if [ -n "$umzug_n" ] && [ -n "$umzug_ref" ]; then
      case ",$weg," in
        *",$umzug_n,"*) ;;
        *) printf 'umzug %s %s\n' "$umzug_n" "$umzug_ref" ;;
      esac
    fi
  )"
  if [ -n "$neu_norm" ] && [ -n "$neu_umzug" ]; then
    inhalt="$(printf '%s\n%s' "$neu_norm" "$neu_umzug")"
  elif [ -n "$neu_norm" ]; then
    inhalt="$neu_norm"
  elif [ -n "$neu_umzug" ]; then
    inhalt="$neu_umzug"
  else
    rm -f "$WARTE_DATEI"
    return 0
  fi
  mkdir -p "${WARTE_DATEI%/*}"
  printf '%s\n' "$inhalt" > "$WARTE_DATEI.$$.neu" && mv -f "$WARTE_DATEI.$$.neu" "$WARTE_DATEI"
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
    warteliste_aendern "" ",$n"
    sperre_frei
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
  sperre_frei
  exit 0
fi

echo "== Geplante Fenster in tmux-Session $SESSION =="
for i in "${!GEPLANT[@]}"; do echo "  - ${KURZ[$i]}  →  ${GEPLANT[$i]}"; done

# Speicher-Schutz (#257 Paket B): vor JEDEM Fenster fragt ``to_spawn.py speicher``, ob noch
# Platz ist (Exit 0 = frei, 5 = voll, 3 = voll durch fremde Sessions → Rest nicht starten,
# alles andere = Prüfung kaputt → Exit 6, F4). Zwischen zwei Fenstern eine Pause von STAFFEL
# Sekunden (aus ``speicher.staffel_s`` / ``--staffel``), damit die Claude-Starts sich nicht
# stapeln (OOM-Absturz 21.09.).

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
GESTARTETE_NAMEN=()
GESTARTETE_OFFSETS=()
BAU_GESTARTET_NR=()
BAU_GESTARTET_BEFEHL=()
BAU_GESTARTET_OFFSET=()
BAU_START_FEHLER=0

# Prüft, ob ``.to-spawn/fenster_<nr>.log`` ab Byte-Offset ``off`` eine neue Zeile
# ``bau.py #<nr> Exit <rc>`` mit ``rc != 0`` enthält (#641).
bau_log_hat_exit() {
  local nr="$1" off="$2"
  local log_datei="$REPO/.to-spawn/fenster_${nr}.log" groesse
  [ -f "$log_datei" ] || return 1
  groesse="$(wc -c < "$log_datei" | tr -d '[:space:]')"
  case "$groesse" in ''|*[!0-9]*) groesse=0 ;; esac
  if [ "$groesse" -lt "$off" ]; then off=0; fi
  tail -c "+$((off + 1))" "$log_datei" 2>/dev/null \
    | grep -Eq "(^|[^[:alnum:]_])bau\.py[[:space:]]+#${nr}[[:space:]]+Exit[[:space:]]+-?[1-9][0-9]*([^0-9]|$)"
}

# Anlauf (#604) und Sofort-Exit-Prüfung (#641): wartet auf das Ereignis (gestartete tmux-Fenster
# sind da oder schon mit Exit ≠ 0 im Fenster-Log), prüft dann bis TO_SPAWN_ANLAUF_PRUEF_S
# (Vorgabe 2 s) die Fenster-Logs der gestarteten Bau-Fenster und meldet erst danach ehrlich
# „gestartet“ oder „nicht gestartet“ (samt Briefkasten-Weckruf an den Aufseher).
anlauf_und_bau_pruefen() {
  local anlauf_max_s anlauf_pruef_s anlauf_schritte anlauf_i pruef_schritte pruef_i
  local idx nr off befehl alle_raus meldung rc_py
  anlauf_max_s="${TO_SPAWN_ANLAUF_MAX_S:-10}"
  case "$anlauf_max_s" in ''|*[!0-9]*) anlauf_max_s=10 ;; esac
  anlauf_pruef_s="${TO_SPAWN_ANLAUF_PRUEF_S:-2}"
  case "$anlauf_pruef_s" in ''|*[!0-9]*) anlauf_pruef_s=2 ;; esac

  anlauf_bereit() {
    local fenster n i_n nr_n off_n
    fenster="$(tmux list-windows -t "=$SESSION" -F '#W' 2>/dev/null || true)"
    for i_n in "${!GESTARTETE_NAMEN[@]}"; do
      n="${GESTARTETE_NAMEN[$i_n]}"
      if [ -n "$fenster" ] && printf '%s\n' "$fenster" | grep -qxF "$n"; then
        continue
      fi
      case "$n" in
        "bau "*)
          nr_n="${n#bau }"
          off_n="${GESTARTETE_OFFSETS[$i_n]}"
          bau_log_hat_exit "$nr_n" "$off_n" && continue
          ;;
      esac
      return 1
    done
    return 0
  }

  echo "== Anlauf (wartet auf Fenster, max. ${anlauf_max_s} s) =="
  if [ "${#GESTARTETE_NAMEN[@]}" -gt 0 ] && [ "$anlauf_max_s" -gt 0 ]; then
    anlauf_schritte=$((anlauf_max_s * 5))
    anlauf_i=0
    while ! anlauf_bereit && [ "$anlauf_i" -lt "$anlauf_schritte" ]; do
      sleep 0.2 2>/dev/null || sleep 1
      anlauf_i=$((anlauf_i + 1))
    done
    if [ "${#BAU_GESTARTET_NR[@]}" -gt 0 ] && [ "$anlauf_pruef_s" -gt 0 ]; then
      if [ "$anlauf_pruef_s" -gt "$anlauf_max_s" ]; then
        anlauf_pruef_s="$anlauf_max_s"
      fi
      pruef_schritte=$((anlauf_pruef_s * 5))
      pruef_i=0
      while [ "$pruef_i" -lt "$pruef_schritte" ]; do
        alle_raus=1
        for idx in "${!BAU_GESTARTET_NR[@]}"; do
          if ! bau_log_hat_exit "${BAU_GESTARTET_NR[$idx]}" "${BAU_GESTARTET_OFFSET[$idx]}"; then
            alle_raus=0
            break
          fi
        done
        [ "$alle_raus" -eq 1 ] && break
        sleep 0.2 2>/dev/null || sleep 1
        pruef_i=$((pruef_i + 1))
      done
    fi
  fi

  if [ "${#BAU_GESTARTET_NR[@]}" -gt 0 ]; then
    for idx in "${!BAU_GESTARTET_NR[@]}"; do
      nr="${BAU_GESTARTET_NR[$idx]}"
      befehl="${BAU_GESTARTET_BEFEHL[$idx]}"
      off="${BAU_GESTARTET_OFFSET[$idx]}"
      if bau_log_hat_exit "$nr" "$off"; then
        BAU_START_FEHLER=1
        rc_py=0
        meldung="$(env -u TO_SPAWN_TICKET TO_SPAWN_REPO="$REPO" "$PY" -c '
import re, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from to_spawn import briefkasten, capo

repo = Path(sys.argv[2])
nr = sys.argv[3]
off = int(sys.argv[4])
log_pfad = capo.fenster_log(repo, int(nr))
daten = log_pfad.read_bytes()
ausschnitt = daten[off:] if off <= len(daten) else daten
zeilen = [z.strip() for z in ausschnitt.decode("utf-8", errors="replace").splitlines() if z.strip()]
muster = re.compile(rf"(?:^|[^\w])bau\.py\s+#{re.escape(nr)}\s+Exit\s+(-?[1-9][0-9]*)\b")
exit_idx = -1
exit_code = ""
for i, z in enumerate(zeilen):
    m = muster.search(z)
    if m:
        exit_idx = i
        exit_code = m.group(1)
exit_zeile = zeilen[exit_idx] if exit_idx >= 0 else f"bau.py #{nr} Exit ≠ 0"
kontext = zeilen[exit_idx - 1][:240] if exit_idx > 0 else ""
code_text = exit_code or "≠ 0"
frage = f"Start von bau #{nr} fehlgeschlagen (Exit {code_text}) — Fenster-Log prüfen und Ursache beheben"
vorschlag = f"Fenster-Log .to-spawn/fenster_{nr}.log prüfen, Ursache beheben und Ticket #{nr} neu starten"
belege = [f".to-spawn/fenster_{nr}.log: {exit_zeile}"]
if kontext:
    belege.append(kontext)
try:
    vorhanden = next(
        (o["id"] for o in briefkasten.offene([nr], hauptbaum=repo) if o["id"] and o["frage"] == frage),
        None,
    )
    fid = vorhanden or briefkasten.einwerfen(
        nr,
        frage,
        vorschlag,
        3,
        belege,
        art="start-fehlgeschlagen",
        hauptbaum=repo,
    )
    print(f"{exit_zeile} (Aufseher über Briefkasten geweckt, id {fid})")
except Exception as fehler:
    print(f"{exit_zeile} (Briefkasten-Weckruf fehlgeschlagen: {fehler})")
' "$SKILL_HOME" "$REPO" "$nr" "$off" 2>/dev/null)" || rc_py=$?
        if [ "$rc_py" -ne 0 ] || [ -z "$meldung" ]; then
          meldung="bau.py #$nr Exit ≠ 0 (Log: .to-spawn/fenster_${nr}.log)"
        fi
        echo "nicht gestartet: bau $nr — $meldung" >&2
      else
        echo "gestartet: bau $nr ($befehl)"
      fi
    done
  fi
}

fenster_laeuft_schon() {
  local kurz="$1" art nr z
  art="${kurz%% *}"
  if [ "$art" = "wache" ]; then
    aufseher_laeuft
    return $?
  fi
  if [ "$art" = "leitstand" ]; then
    tmux list-windows -t "=$SESSION" -F '#W' 2>/dev/null | grep -qx "leitstand $SPEC"
    return $?
  fi
  nr="$(echo "$kurz" | awk '{print $2}')"
  STAND="$(TO_SPAWN_REPO="$REPO" "$PY" "$SKILL_HOME/skripte/sessions_stand.py" "$SPEC" --alle 2>/dev/null || true)"
  z="$(zustand_von "$nr")"
  if [ -n "$z" ] && [ "$z" != "aus" ]; then return 0; fi
  tmux list-windows -t "=$SESSION" -F '#W' 2>/dev/null | grep -qx "bau $nr"
}

for i in "${!GEPLANT[@]}"; do
  befehl="${GEPLANT[$i]}"
  if [ "$GESTARTET" -gt 0 ]; then
    echo "Staffel: $STAFFEL s Pause vor dem nächsten Fenster"
    if [ "$STAFFEL" -gt 0 ]; then
      # Sperre vor der Staffel-Pause freigeben (#604), damit parallele Läufe nicht
      # über alle Pausen hinweg blockieren; danach Sperre erneut holen und Stand prüfen.
      sperre_frei
      sleep "$STAFFEL"
      sperre_holen
      if fenster_laeuft_schon "${KURZ[$i]}"; then
        case "${KURZ[$i]}" in
          "bau "*)
            nr="$(echo "${KURZ[$i]}" | awk '{print $2}')"
            if [ -n "$UMZUG_REF" ]; then
              echo "Ticket #$nr läuft auf dem Server bereits — Umzug abgebrochen, nichts gestartet." >&2
              warteliste_aendern "" ",$nr"
              sperre_frei
              exit 4
            fi
            echo "Ticket #$nr läuft bereits — übersprungen."
            LAEUFT="$LAEUFT,$nr" ;;
          *)
            echo "${KURZ[$i]} läuft bereits — übersprungen." ;;
        esac
        continue
      fi
    fi
  fi
  rc=0
  speicher_pruefen "${KURZ[$i]}" || rc=$?
  # Kein Platz, aber der Aufseher läuft (#460): er startet die fehlenden Fenster selbst nach,
  # sobald Platz frei ist — das ist der vorgesehene Weg, kein Fehler.
  if [ "$rc" -eq 8 ]; then
    if [ -n "$UMZUG_REF" ]; then
      # Umzug-Fenster samt Ref in der Warteliste vermerken (#604), damit der Aufseher nachstartet.
      warteliste_aendern "" "$LAEUFT" "$TICKETS_ARG" "$UMZUG_REF"
    else
      # Rest-bau-Fenster zur vorhandenen Liste dazu, Laufendes austragen (#560).
      liste="$(printf '%s\n' "${KURZ[@]:$i}" | awk '$1=="bau"{print $2}' | paste -sd, -)"
      warteliste_aendern "$liste" "$LAEUFT"
    fi
    sperre_frei
    if [ "${#BAU_GESTARTET_NR[@]}" -gt 0 ]; then
      anlauf_und_bau_pruefen
    fi
    echo "wartet auf Platz: ${KURZ[*]:$i} — $WARTE_GRUND; Aufseher startet nach, sobald Platz frei ist."
    if [ "$BAU_START_FEHLER" -ne 0 ]; then exit 8; fi
    exit 0
  fi
  if [ "$rc" -ne 0 ]; then
    sperre_frei
    if [ "${#BAU_GESTARTET_NR[@]}" -gt 0 ]; then
      anlauf_und_bau_pruefen
    fi
    echo "Rest bleibt aus; später erneut: bash scripts/spawn_srv.sh $SPEC" >&2
    exit "$rc"
  fi
  # Fenstername = die ersten zwei Wörter der Kurzform (``bau <N>`` / ``wache <S>``), auch mit --umzug.
  name="$(echo "${KURZ[$i]}" | awk '{print $1" "$2}')"
  fenster_befehl="$VORSPANN $befehl"
  log_offset=0
  case "$name" in
    "bau "*)
      nr="${name#bau }"
      log_datei="$REPO/.to-spawn/fenster_${nr}.log"
      if [ -f "$log_datei" ]; then
        log_offset="$(wc -c < "$log_datei" | tr -d '[:space:]')"
        case "$log_offset" in ''|*[!0-9]*) log_offset=0 ;; esac
      fi
      # Wie capo.bau_startzeile (#277, #641): stderr zusätzlich ins Fenster-Log und bei
      # Exit ≠ 0 die Zeile ``<zeit> bau.py #<N> Exit <rc>`` anhängen, damit der Anlauf-Check
      # sofortige Fehlstarts erkennt.
      q_log="$(printf %q "$log_datei")"
      q_ordner="$(printf %q "$REPO/.to-spawn")"
      fenster_befehl="$VORSPANN mkdir -p $q_ordner; exec 3>&2 2> >(tee -a $q_log >&3); tee_pid=\$!; $befehl; rc=\$?; exec 2>&3 3>&-; [ \"\$rc\" -eq 0 ] || { timeout 3 tail -s 0.1 --pid=\"\$tee_pid\" -f /dev/null 2>/dev/null; echo \"\$(date -Is) bau.py #$nr Exit \$rc\" >> $q_log; }; exit \"\$rc\""
      ;;
  esac
  if tmux has-session -t "=$SESSION" 2>/dev/null; then
    tmux new-window -t "=$SESSION" -n "$name" -c "$REPO" bash -lc "$fenster_befehl" 9>&-
  else
    tmux new-session -d -s "$SESSION" -n "$name" -c "$REPO" bash -lc "$fenster_befehl" 9>&-
  fi
  case "$name" in
    "bau "*)
      nr="${name#bau }"
      LAEUFT="$LAEUFT,$nr"
      BAU_GESTARTET_NR+=("$nr")
      BAU_GESTARTET_BEFEHL+=("$befehl")
      BAU_GESTARTET_OFFSET+=("$log_offset")
      ;;
    *)
      echo "gestartet: $name ($befehl)"
      ;;
  esac
  GESTARTETE_NAMEN+=("$name")
  GESTARTETE_OFFSETS+=("$log_offset")
  GESTARTET=$((GESTARTET + 1))
done
warteliste_aendern "" "$LAEUFT"
sperre_frei

anlauf_und_bau_pruefen
TO_SPAWN_REPO="$REPO" "$PY" "$SKILL_HOME/skripte/sessions_stand.py" "$SPEC" --alle || true
echo
echo "Kontrolle: sessions $SPEC · tmux attach -t $SESSION (Fenster wechseln Strg+B n, raus Strg+B d)"
if [ "$BAU_START_FEHLER" -ne 0 ]; then
  exit 8
fi
