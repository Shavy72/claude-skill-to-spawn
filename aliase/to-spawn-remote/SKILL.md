---
name: to-spawn-remote
description: "Alias auf /to-spawn mit Ziel Bau-Server: alle Ticket-Sessions einer Spec per SSH als tmux-Fenster auf dem Bau-Server starten (Laptop darf aus). Ersetzt `/to-spawn srv`. Trigger: /to-spawn-remote, „auf dem Server spawnen“, „remote starten“."
disable-model-invocation: false
---

# /to-spawn-remote — Alias auf den Skill `to-spawn` (Ziel: Bau-Server)

Keine eigene Logik. Skill `to-spawn` lesen (Abschnitte „srv — Bau-Server“, „Kontrolle“, „Harte Regeln“), dann im Repo-Wurzelordner:

`python ~/.claude/skills/to-spawn/to_spawn.py spawn <S> --ziel srv [--tickets a,b,c]`

Prüft erst die Regularien, startet dann `spawn_srv.ps1` → `scripts/spawn_srv.sh` auf dem Server (SSH-Ziel aus `.to-spawn/config.json`, Feld `ssh_ziel`). Ergebnis melden: `sessions <S>`-Tabelle + Hinweis auf `checkpoint:human`-Tickets.

Auf dem Bau-Server selbst (kein `pwsh`): `bash scripts/spawn_srv.sh <S> [--tickets a,b,c]` im Repo-Wurzelordner.
