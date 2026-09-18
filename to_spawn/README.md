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
| `python to_spawn.py pruefen <S> [--tickets a,b] [--ohne-github]` | nur die Regularien; Exit 0 = frei, **Exit 3 = Weigerung** |
| `python to_spawn.py log <S>` | Gesamt-Tabelle aller Tickets der Spec (Schätzung, Ist, Sessions, Staffel, Subagenten, Dauer) |
| `python to_spawn.py lernstoff [--letzte 30]` | Zeilen für `/to-tickets` (Schätzung → Ist, Sessions, Faktor je Ticket + Faustregeln für den Schnitt) |
| `python to_spawn.py deploy-status [--datei P] [--ticket N] [--still-min 60]` | liest nur die Deploy-Statusdatei (Vorgabe `<Repo>/.deploy_status.jsonl`, sonst `DEPLOY_STATUS_DATEI`), schreibt jede neue Phase als `deploy_phase` ins Bau-Log des Repos, in dem die Datei liegt (ohne Doppel; Ticket aus `--ticket`, `BAU_TICKET` oder `wt-<N>`), Ausgabe ≤ 3 Zeilen; **Exit 0 = grün, 1 = rot (auch: Gate-Prozess laut PID tot ohne Ende-Zeile), 3 = läuft, 4 = still über der Grenze oder Zeitstempel unlesbar, 2 = keine Datei** |
| `python to_spawn.py hook-stop` | Stop-Hook, JSON auf stdin |
| `python to_spawn.py hook-subagent-stop` | SubagentStop-Hook, JSON auf stdin |
| `python to_spawn.py inventur [--json] [--abwahl a,b] [--anwahl a,b] [--schreiben] [--ausgabe PFAD] [--letzte 200]` | Setup-Wizard Teil 2: Werkzeug-Inventur (Skills, Plugin-Skills, Agenten, MCP-Server) nach 12 Kategorien, Standard alles an; Setup-Zeilen für fehlende Unterbauten, tote Winkel; `--schreiben` legt `.to-spawn/werkzeuge.json` an, unbekannter Name = **Exit 2** |

`--ziel` überspringt die Frage; ohne Angabe gilt `ziel_default` aus der Konfig.

## Regularien (Weigerung, Exit 3)

Gelesen wird `docs/agents/manifests/spec-<S>.json`. `pruefen <S> [--tickets a,b] [--ohne-github]`
läuft automatisch vor jedem Spawn (`spawn`, `scripts/spawn_srv.sh`,
`spawn_local.ps1`); Exit 0 = frei, **Exit 3 = Weigerung**, nichts wird gestartet,
jede Fehlerzeile nennt ihre Abhilfe.

**Weigerung (Exit 3) bei:**
- Pflichtfeld fehlt: `schaetzung_k` (Zahl, Tausend Token, > 0 und < `staffel.grenze_k`,
  Vorgabe 200) oder `umfang` (Klartext, was das Ticket umfasst)
- Schätzung ≥ `staffel.grenze_k`
- Ticket-Schlüssel doppelt im Manifest
- gewähltes Ticket (`--tickets`) steht nicht im Manifest — die Auswahl läuft immer
  durch die Prüfung (`spawn`, `spawn_srv.sh`, `spawn_local.ps1` reichen sie durch),
  die Regeln gelten trotzdem fürs ganze Manifest
- offenes Ticket steht zusätzlich in einer anderen `spec-*.json` (jede außer der
  eigenen Datei, auch `spec-149-ticket-179.json`); mit `--ohne-github` zählt der
  unbekannte Zustand wie offen („Zustand ohne GitHub unbekannt“)
- Ticket-Text nennt „Blocked by #X“ ohne native Kante
  (`gh api repos/<owner>/<repo>/issues/<N>/dependencies/blocked_by`), und X ist ein
  Ticket der Spec **oder** offen (Zustand nicht abfragbar = auch Weigerung; je Nummer
  eine Abfrage) — die Fehlermeldung nennt den passenden `gh api`-Befehl zum Setzen.
  Erkannt: Überschriften `#`–`######` und fett `**Blocked by:**` (Groß/Klein egal,
  Doppelpunkt optional), Verweise `#12` oder `…/issues/12`; „None“/„Keine“/„-“ = kein
  Verweis. Abschnitt endet an der nächsten Überschrift; die Fett-Form gilt für den Rest
  der Zeile, ist der leer, bis zur nächsten Leerzeile
- kein Ticket der Spec trägt das Label `checkpoint:human` (Name aus Konfig-Schlüssel
  `regularien.checkpoint_label`); solange andere Tickets offen sind, muss das
  Checkpoint-Ticket offen sein und mindestens eine native Kante haben
- GitHub nicht abfragbar (fail-closed) — außer bewusst ohne, per `--ohne-github`

**Nur Warnung (kein Abbruch):**
- Blocker-Verweis auf eine geschlossene Nummer außerhalb der Spec (Entwurfs-Nummer?)
- kein Ticket der Spec hat überhaupt eine Kante
- offenes Ticket hat schon einen Assignee (läuft woanders eine Session? sonst
  Wiederaufnahme)

Für Tests kennt der Stub (`gh_stub.py`, über `TO_SPAWN_GH_STUB`) `GH_STUB_DATEN`
— eine JSON-Datei mit `labels`/`body`/`assignees` je Ticket, damit die Regularien
ohne echtes GitHub geprüft werden können — dazu `GH_STUB_KAPUTT` (diese Nummern
scheitern) und `GH_STUB_PROTOKOLL` (jeder Aufruf als Zeile in eine Datei).

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
- Deploy-Statusdatei: `safe_deploy_vps.sh` (DuoPlus-Repo) schreibt sie, `deploy-status`
  überträgt sie als `deploy_phase` ins Bau-Log (#207). Andere Repos brauchen dafür ein
  eigenes Deploy-Skript mit demselben Zeilenformat.
- `spawn` ist nur Delegation; `spawn_srv.ps1`/`spawn_local.ps1` kennen die Staffel
  noch nicht — sie starten `bau <N>` des Repos, nicht `bau_loop.run_ticket`.
- `ist_k` summiert die `usage`-Felder aller assistant-Zeilen; das ist ein
  Verbrauchsmaß, kein Kontext-Höchststand.
- Marker-Weg braucht den echten Beweis mit laufender Claude-Session (Probesitz).
