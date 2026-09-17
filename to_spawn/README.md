# to-spawn Version 2 — Kern (vorläufig)

Python-Kern des Skills: Regularien-Prüfung, Bau-Log, Stop-/SubagentStop-Hooks und
die Staffel-Schleife. Die Terminal-Arbeit machen weiterhin `spawn_local.ps1`
(Windows Terminal) und `spawn_srv.ps1` (tmux auf dem Bau-Server) — der Kern ruft
sie nur auf.

## Befehle

Immer im Repo-Wurzelordner ausführen:

| Befehl | Wirkung |
|---|---|
| `python to_spawn.py spawn <S> [--ziel local\|srv] [--tickets a,b] [--dry-run]` | prüft die Regularien, fragt „lokal (1) oder Server (2)?" und startet das Terminal-Skript |
| `python to_spawn.py pruefen <S> [--ohne-github]` | nur die Regularien; Exit 0 = frei, **Exit 3 = Weigerung** |
| `python to_spawn.py log <S>` | Gesamt-Tabelle aller Tickets der Spec (Schätzung, Ist, Sessions, Staffel, Subagenten, Dauer) |
| `python to_spawn.py lernstoff [--letzte 30]` | Zeilen für `/to-tickets` (Schätzung → Ist) |
| `python to_spawn.py hook-stop` | Stop-Hook, JSON auf stdin |
| `python to_spawn.py hook-subagent-stop` | SubagentStop-Hook, JSON auf stdin |

`--ziel` überspringt die Frage; ohne Angabe gilt `ziel_default` aus der Konfig.

## Regularien (Weigerung, Exit 3)

Gelesen wird `docs/agents/manifests/spec-<S>.json`. Je Ticket Pflicht:

- `schaetzung_k` — Zahl in Tausend Token, höchstens `staffel.grenze_k` (200)
- `umfang` — Klartext, was das Ticket umfasst

Fehlt eines oder liegt die Schätzung über der Grenze: klare Meldung „erst
/to-tickets", nichts wird gestartet. Zusätzlich werden die nativen
`blocked_by`-Kanten über `gh api repos/<owner>/<repo>/issues/<N>/dependencies/blocked_by`
geprüft; hat **kein** Ticket eine Kante, gibt es eine Warnung (kein Abbruch).

## Bau-Log

Eine Datei je Ticket: `docs/agents/bau_log/<N>.jsonl`, **nur anhängen** (so wandert
sie konfliktfrei über Git). Eine Zeile je Ereignis:

```json
{"ts":"2026-09-18T00:12:03+02:00","typ":"session_ende","ticket":"901","session_id":"…",
 "staffel":1,"modell":"claude-opus-5","effort":"medium","runner":"claude",
 "tokens":{"input":105,"cache_read":200,"cache_creation":30,"output":47,"gesamt":382},
 "dauer_s":3120,"text":"Kern gebaut, Tests grün."}
```

Typen: `auftrag` · `session_start` · `entscheidung` · `deploy_phase` · `handoff` ·
`session_ende` · `subagent_ende` (zusätzlich `eltern_session`) · `staffel_limit`.
Zahlen schreiben ausschließlich die Hooks, Worte schreibt die Session.

Die Ticket-Nummer holt sich der Hook aus `TO_SPAWN_TICKET` (setzt die
Staffel-Schleife) oder aus einem Worktree-Pfad `wt-<N>`. Findet er keine, schreibt
er nichts.

## Hook-Einträge für `settings.json`

Von Hand eintragen (dieser Kern ändert `settings.json` **nicht**):

```json
{
  "hooks": {
    "Stop": [
      {"hooks": [{"type": "command",
        "command": "python ~/.claude/skills/to-spawn/to_spawn.py hook-stop"}]}
    ],
    "SubagentStop": [
      {"hooks": [{"type": "command",
        "command": "python ~/.claude/skills/to-spawn/to_spawn.py hook-subagent-stop"}]}
    ]
  }
}
```

## Staffel (Smart Zone, 200k)

Die Session schreibt bei 200k einen Handoff nach `docs/handoffs/HANDOFF_<datum>_<N>.md`
und endet. `to_spawn/bau_loop.py:run_ticket()` sieht den Exit, prüft per
`gh issue view`, ob das Ticket noch offen ist, findet die frische Handoff-Datei,
schreibt die Zeile `handoff` und startet dieselbe Session erneut — der Handoff-Text
hängt als Startkontext am Prompt. Höchstens `staffel.max_staffeln` (3) Läufe, danach
Zeile `staffel_limit` und Stopp.

Zwei Wege, Schalter `staffel.modus`:

- **`eltern` (Vorgabe, funktioniert):** Der Stop-Hook legt die Marker-Datei
  `.to-spawn/stop-<N>` an; die Schleife überwacht sie und beendet den Kindprozess.
- **`hook` (Ausweichweg, unsicher):** Der Stop-Hook gibt zusätzlich
  `{"continue": false, "stopReason": "…"}` aus.

Grund für die Vorgabe: die Hook-Doku (`code.claude.com/docs/en/hooks`) kennt für
Stop/SubagentStop nur `decision: "block"` (hält Claude am Laufen, Gegenteil eines
Endes) und `hookSpecificOutput.additionalContext`. Das allgemeine Feld `continue:
false` heißt „Claude stops processing entirely after the hook runs" — das beendet
die Runde, nicht den Prozess. Ein Hook kann den Claude-Prozess also nicht beenden;
der Elternprozess muss es tun.

## Konfig `.to-spawn/config.json`

Fehlt die Datei, gelten die Vorgaben aus `to_spawn/config.py`:

```json
{
  "ziel_default": "srv",
  "terminal": "wt",
  "ssh_ziel": "bau-server",
  "runner": "claude",
  "modelle": {"ticket": "claude-opus-5", "ticket_leicht": "claude-sonnet-5",
              "waechter": "claude-fable-5-1"},
  "effort": {"ticket": "medium", "ticket_leicht": "low", "waechter": "low"},
  "staffel": {"modus": "eltern", "grenze_k": 200, "max_staffeln": 3},
  "mail": {"ziel": "", "nur_kritisch": true}
}
```

## Test

`cd ~/.claude/skills/to-spawn && python -m pytest tests -q` — Weg-Test gegen ein
Wegwerf-Repo mit Bare-Remote. Gestellt sind nur zwei Programme:

1. `claude` (`tests/hilfen/fake_claude.py`) — die Session selbst.
2. `gh` (`tests/hilfen/gh_stub.py`, über `TO_SPAWN_GH_STUB`) — GitHub ist ein
   externer Dienst; ohne diese Variable läuft echtes `gh`.

Alles andere (CLI, Manifest-Prüfung, Bau-Log, Hooks, Staffel-Schleife, Git) läuft echt.

## Offene Punkte

- Setup-Wizard, Werkzeug-Inventur, Nest-Bau, Staging, `/to-spawn-of`, Mail und
  Remote Control fehlen (bewusst, eigene Tickets der Spec #202).
- Deploy-Statusdatei (`deploy_phase`-Zeilen) ist im Log-Format vorgesehen, aber noch
  schreibt niemand sie.
- `spawn` ist nur Delegation; `spawn_srv.ps1`/`spawn_local.ps1` kennen die Staffel
  noch nicht — sie starten `bau <N>` des Repos, nicht `bau_loop.run_ticket`.
- `ist_k` summiert die `usage`-Felder aller assistant-Zeilen; das ist ein
  Verbrauchsmaß, kein Kontext-Höchststand.
- Marker-Weg braucht den echten Beweis mit laufender Claude-Session (Probesitz).
