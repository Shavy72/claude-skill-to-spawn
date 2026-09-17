---
name: to-spawn-local
description: "Alle Ticket-Sessions einer Spec auf DIESEM PC starten — ein Windows-Terminal-Fenster, Tab je `bau <N>` + Tab `wache <S>`. Gegenstück zu /to-spawn (Standard = Bau-Server). Trigger: /to-spawn-local, „lokal spawnen“, „Konsolen hier auf dem PC“."
disable-model-invocation: false
---

# /to-spawn-local — Bau-Sessions einer Spec auf diesem PC

Gleiche Regeln wie `/to-spawn` (Skill `to-spawn`, dort Abschnitte „Kontrolle“ und „Harte Regeln“ lesen), nur das Ziel ist dieser PC statt der Bau-Server.

## Aufruf

- `/to-spawn-local <S>` — Windows-Terminal-Fenster, Tab `wache <S>` + je Ticket Tab `bau <N>`.
- `/to-spawn-local <S> --tickets 188,189,190` — nur diese Tabs (z. B. Neustart einzelner Wartetabs).

## Ablauf (deterministisch)

1. Im Repo-Wurzelordner: `pwsh -File ~/.claude/skills/to-spawn/spawn_local.ps1 -Spec <S> [-Tickets a,b,c] [-OhneWache] [-Window 1] [-DryRun]`
2. Ergebnis melden: die `sessions <S>`-Tabelle + Hinweis auf `checkpoint:human`-Tickets.
3. Danach nichts mehr anfassen; Warten macht `bau.py` (0 Token).

## Kontrolle

`sessions <S>` im PowerShell-Profil. Tabs beenden nur im Zustand `wartet`, nie `bau.py` killen ohne `sessions <S>` (#187).
