---
name: to-spawn-of
description: "Umzug auf den Bau-Server: eine laufende Bau-Session (bau <N>) oder den ganzen Bau einer Spec (im Wächter) vom lokalen PC auf den Bau-Server verschieben — Handoff, Push, Neustart dort, lokale Session endet sauber. Trigger: /to-spawn-of, „auf den Server umziehen“, „Session verschieben“, „Laptop zuklappen“."
disable-model-invocation: false
---

# /to-spawn-of — Umzug auf den Bau-Server (Skill `to-spawn`, #212)
 Python-Aufruf: Linux/macOS `python3`, Windows `python`.
Die Logik liegt im Skill `to-spawn` (`to_spawn/umzug.py`). Welche Variante gilt, entscheidet die Umgebung — zuerst prüfen: `echo "$BAU_TICKET|$TO_SPAWN_WACHE_SPEC"`.

## 1. Bau-Session (`BAU_TICKET` gesetzt)

1. Handoff `docs/handoffs/HANDOFF_<heute>_$BAU_TICKET.md` im Ticket-Worktree schreiben: Stand, nächste Schritte, offene Punkte. **Pflichtzeile `Umzug: server`** (auch als Überschrift `## Umzug: server` gültig), **nie** die Zeile `Staffel: weiter` (sonst startet die lokale Staffel neu).
2. Eigene Arbeit mit Pathspec committen (`git status --short` vorher, nur eigene Dateien — auch neue Dateien, sonst Weigerung). **Den Handoff dabei NICHT committen** — das macht das Skript in Schritt 3 mit dem richtigen Betreff.
3. Im Worktree ausführen:
   `python3 "${TO_SPAWN_HOME:-$HOME/.claude/skills/to-spawn}"/to_spawn.py umzug $BAU_TICKET --handoff docs/handoffs/HANDOFF_<heute>_$BAU_TICKET.md`
4. **Exit 0 → nichts mehr tun.** Die Session läuft schon auf dem Server; `bau.py` beendet diese lokale Session gleich selbst (kein Doppel-Lauf).
   Exit 3 = Weigerung (Meldung nennt die Abhilfe, beheben, erneut) · Exit 1 = Server-Start nicht bewiesen oder die Session läuft dort schon — die lokale Session arbeitet normal weiter.

Das Skript committet nur den Handoff (Betreff endet mit `(#<N>) [skip ci]`), pusht den Branch, startet auf dem Server `bash scripts/spawn_srv.sh <S> --tickets <N> --ohne-wache --umzug <branch>@<sha>:<pfad>` und beendet die lokale Session erst, wenn tmux-Fenster `bau <N>` und `sessions <S>` den Lauf zeigen.

## 2. Wächter (`TO_SPAWN_WACHE_SPEC` gesetzt)

1. AskUserQuestion: „Ganzen Bau auf den Bau-Server verschieben?“ — Antworten **Ja** / **Nein**. Nein → nichts tun.
2. Ja → im Repo-Wurzelordner **im Hintergrund** starten (Bash-Tool mit `run_in_background: true` — der Lauf wartet bis zu 30 min je Session und würde sonst am Tool-Zeitlimit abbrechen):
   `python3 "${TO_SPAWN_HOME:-$HOME/.claude/skills/to-spawn}"/to_spawn.py umzug-alle $TO_SPAWN_WACHE_SPEC`
3. Auf die Fertig-Meldung warten, dann Ergebniszeilen je Ticket melden (umgezogen / übersprungen / Stopp + Grund).

Ablauf streng nacheinander, nie zwei Sessions halb: Zustand je Ticket wird direkt vor der Aktion neu gelesen; wartende Sessions starten erst auf dem Server (bewiesen), dann endet das lokale `bau.py` — scheitert der Server, bleibt lokal alles unangetastet; laufende Sessions bekommen über ihren Stop-Hook die Anweisung aus Abschnitt 1 (Anfrage-Datei `.to-spawn/umzug-anfrage-<N>`). `VERWAIST`, Zeitüberschreitung oder ein gemeldeter Fehlschlag der Session → Stopp, der Rest bleibt lokal. Die Anfrage-Datei wird auch bei Abbruch aufgeräumt. Zum Schluss startet der Wächter auf dem Server, diese Wächter-Session endet dann selbst.

## 3. Weder noch

Keine der beiden Variablen gesetzt → erklären: `/to-spawn-of` geht nur in einer Session, die per `bau <N>` oder `wache <S>` gestartet wurde. Neue Sessions direkt auf dem Server: `/to-spawn-remote <S>`.
