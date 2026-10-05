#!/bin/bash
# Schlaf-Attrappe für ``claude`` (Test #236): nimmt beliebige Argumente an und
# schläft eine Stunde. Der Test legt sie unter dem Namen ``claude`` auf den PATH,
# damit der Aufpasser im tmux-Fenster einen Prozess ``claude … --session-id <id>``
# sieht — argv[0] heißt wie beim echten Programm ``claude``, die Argumente folgen.
#
# Wie das echte Claude Code schreibt sie beim Start ``$HOME/.claude/sessions/$$.json``
# (Fixrunde #236, F1): ``pid``, ``sessionId`` (aus ``--session-id``/``--resume``),
# ``cwd``, ``status`` (Env ``FAKE_CLAUDE_STATUS``, Standard ``idle``) und ``tmux``
# (``sitzung:@fenster.%pane`` aus ``$TMUX_PANE``; Socket aus ``FAKE_CLAUDE_TMUX_SOCKET``).
# Bei Ende wird die Datei gelöscht (trap).
#   FAKE_CLAUDE_STATUS=exit   → endet sofort (Nachweis-Test F5)
#   FAKE_CLAUDE_OHNE_JSON=1   → schreibt keine Datei (Negativ-Test F1)
#   FAKE_CLAUDE_SID=<id>      → ID, wenn argv keine trägt (Weg-Test F1, zwei Fenster)
#   FAKE_CLAUDE_JSON_VERZOEGERUNG_S=<n> → wartet ``n`` Sekunden vor dem Schreiben
#                             der JSON (Nachfix N3: Nachweis-Fenster NACHWEIS_S)
#   FAKE_CLAUDE_EXIT_BEI_EINGABE=1 → liest stdin zeilenweise und endet bei ``/exit``
#                             (#592: Aufpasser beendet per /exit statt kill-window)
#   FAKE_CLAUDE_KIND_PID=<datei> → startet ein Kind, das SIGHUP ignoriert (wie ein
#                             MCP-Server), und schreibt dessen PID in ``datei``
# Schutz: ins echte Login-Home wird nie geschrieben — Tests setzen ein Temp-HOME.

sid=""
vorher=""
for arg in "$@"; do
  if [ "$vorher" = "--session-id" ] || [ "$vorher" = "--resume" ]; then
    sid="$arg"
  fi
  case "$arg" in
    --session-id=*) sid="${arg#--session-id=}" ;;
    --resume=*) sid="${arg#--resume=}" ;;
  esac
  vorher="$arg"
done
# Ohne ID in argv (wie eine frisch per Prompt gestartete Session): Env ``FAKE_CLAUDE_SID``.
if [ -z "$sid" ]; then
  sid="${FAKE_CLAUDE_SID:-}"
fi

if [ -n "${FAKE_CLAUDE_JSON_VERZOEGERUNG_S:-}" ] && [ "${FAKE_CLAUDE_JSON_VERZOEGERUNG_S}" != "0" ]; then
  sleep "$FAKE_CLAUDE_JSON_VERZOEGERUNG_S"
fi

login_heim="$(getent passwd "$(id -u)" | cut -d: -f6)"
datei=""
if [ -z "$FAKE_CLAUDE_OHNE_JSON" ] && [ -n "$HOME" ] && [ "$HOME" != "$login_heim" ]; then
  mkdir -p "$HOME/.claude/sessions"
  datei="$HOME/.claude/sessions/$$.json"
  tmux_feld=""
  if [ -n "$TMUX_PANE" ]; then
    if [ -n "$FAKE_CLAUDE_TMUX_SOCKET" ]; then
      tmux_feld="$(tmux -L "$FAKE_CLAUDE_TMUX_SOCKET" display -p -t "$TMUX_PANE" '#{session_name}:#{window_id}.#{pane_id}' 2>/dev/null)"
    else
      tmux_feld="$(tmux display -p -t "$TMUX_PANE" '#{session_name}:#{window_id}.#{pane_id}' 2>/dev/null)"
    fi
  fi
  {
    printf '{"pid": %d, "sessionId": "%s", "cwd": "%s", "status": "%s"' \
      "$$" "$sid" "$PWD" "${FAKE_CLAUDE_STATUS:-idle}"
    if [ -n "$tmux_feld" ]; then
      printf ', "tmux": "%s"' "$tmux_feld"
    fi
    printf '}\n'
  } > "$datei"
  trap 'rm -f "$datei"' EXIT
elif [ -z "$FAKE_CLAUDE_OHNE_JSON" ]; then
  echo "fake claude: HOME ist das Login-Home — keine Session-Datei geschrieben" >&2
fi

if [ "${FAKE_CLAUDE_STATUS:-}" = "exit" ]; then
  exit 0
fi
if [ -n "${FAKE_CLAUDE_KIND_PID:-}" ]; then
  ( trap '' HUP; exec sleep 3600 ) </dev/null >/dev/null 2>&1 &
  echo $! > "$FAKE_CLAUDE_KIND_PID"
fi
if [ "${FAKE_CLAUDE_EXIT_BEI_EINGABE:-}" = "1" ]; then
  while IFS= read -r zeile; do
    [ "$zeile" = "/exit" ] && exit 0
  done
  exit 0
fi
sleep 3600
