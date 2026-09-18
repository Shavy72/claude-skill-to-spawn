---
name: to-spawn-local
description: "Alias auf /to-spawn mit Ziel lokal: alle Ticket-Sessions einer Spec auf DIESEM PC starten (Windows-Terminal-Fenster, Tab je `bau <N>` + Tab `wache <S>`). Trigger: /to-spawn-local, „lokal spawnen“, „Konsolen hier auf dem PC“."
disable-model-invocation: false
---

# /to-spawn-local — Alias auf den Skill `to-spawn` (Ziel: dieser PC)

Keine eigene Logik. Skill `to-spawn` lesen (Abschnitte „Kontrolle“ und „Harte Regeln“), dann im Repo-Wurzelordner:

`python ~/.claude/skills/to-spawn/to_spawn.py spawn <S> --ziel local [--tickets a,b,c]`

Prüft erst die Regularien, startet dann `spawn_local.ps1` (ein Windows-Terminal-Fenster, Tab `wache <S>` + je Ticket Tab `bau <N>`). Ergebnis melden: `sessions <S>`-Tabelle + Hinweis auf `checkpoint:human`-Tickets.
