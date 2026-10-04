---
name: respawn
description: "Bau-Session nach fester SOP ablösen: Handoff anfordern, frische Opus-Session im neuen Fenster starten, Start-Prompt einfügen, alte Session erst nach Beweis beenden. Nur Bau-Server (tmux). Trigger: /respawn, „Session ablösen“, „Bau-Session hängt“, „Handoff-Grenze erreicht“."
disable-model-invocation: false
---

# /respawn — Bau-Session ablösen (Skill `to-spawn`, #431)
Logik: `to_spawn/respawn.py`, Aufruf über `to_spawn.py respawn <S> <N>`. Nur Bau-Server (tmux); das Windows-Pendant ist ein eigenes Ticket.

## Wofür
Der Aufseher löst eine still stehende Bau-Session (oder eine an der Handoff-Grenze) immer nach derselben SOP ab (E2). Ralph-Prinzip: frische Session je Runde, Gedächtnis nur in Datei. Keine Endlos-Schleife ohne Aufseher und Staging-Tor (E6).

## SOP (Reihenfolge ist Gesetz)
Davor: Duplikat-Prüfung — Fenster `bau <N>` muss genau einmal existieren, kein Fenster `bau <N> neu`, nur ein `bau.py` je Ticket. Sonst Exit 3, nichts anfassen.
- a) Handoff anfordern: alte Session schreibt `docs/handoffs/HANDOFF_<JJJJ-MM-TT>_<N>.md` und die Start-Prompt-Datei `docs/handoffs/START_<JJJJ-MM-TT>_<N>.txt`, committet beide und tut nichts mehr.
- b) Neues Fenster `bau <N> neu`: nur `claude` mit Opus 5.5, Denkstufe mittel, **ohne Start-Prompt-Argument**.
- c) `/remote-control` setzt der Skill selbst im neuen Fenster.
- d) Prüfung: Handoff und Start-Prompt sind fertig (vorhanden, nicht leer, neu nach Start). Sonst Abbruch (Exit 2), neues Fenster zu, alte Session bleibt.
- e) Start-Prompt ins neue Fenster einfügen, Text und Enter getrennt.
Danach: Bildschirm des neuen Fensters prüfen, erst dann die alte Session beenden und das Fenster in `bau <N>` umbenennen.

## Aufruf (Aufseher) — nur über einen Ablöse-Subagenten
Agent-Tool mit `model: sonnet`, Prompt-Vorlage:
```
Schätzung: ~5k Token. Lese-Budget: 0.
Führe im Repo-Wurzelordner mit Bash und run_in_background aus (Lauf dauert bis 30 min):
python3 "${TO_SPAWN_HOME:-$HOME/.claude/skills/to-spawn}"/to_spawn.py respawn <S> <N>
Warte auf das Ende. Antwort = genau die eine Ergebniszeile des Befehls, sonst nichts.
```
Der Aufseher wertet nur Exit-Code und diese Zeile aus (E16).

## Exit-Codes
- 0 abgelöst: neue Session läuft im Fenster `bau <N>`.
- 1 neue Session nicht bewiesen: alte Session läuft weiter, Ursache prüfen (Fenster `bau <N> neu` ansehen).
- 2 Handoff oder Start-Prompt fehlt: alte Session anstupsen, später erneut.
- 3 Duplikat oder alte Session fehlt: `sessions <S>` prüfen, nichts doppelt starten.

## Abgrenzung
`to_spawn.py neustart --handoff/--beenden` bleibt für tote Sessions (Ticket „aus“); `wache.py --abloesen` löst den Aufseher selbst ab.
