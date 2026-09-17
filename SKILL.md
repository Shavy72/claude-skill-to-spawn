---
name: to-spawn
description: "Alle Ticket-Sessions einer Spec auf einmal starten — ein Windows-Terminal-Fenster, Tab je `bau <N>` + Tab `wache <S>`, Warten außerhalb von Claude (0 Token), Kontrolle per `sessions <S>`. Trigger: /to-spawn, „spawn alle Terminals“, „starte alle Tickets“, nach /to-tickets. Varianten: `local` (Standard, dieser PC) · `srv` (geplant: tmux/cmux auf dem Server, PC darf aus)."
disable-model-invocation: false
---

# /to-spawn — Bau-Sessions einer Spec starten

Quelle/Installation: `github.com/Shavy72/claude-skill-to-spawn` (`install.ps1`; Repo-Hälfte in `repo-scripts/`). Änderungen hier → dort nachziehen.

Stand 2026-09-17 (David: „richtig geil … merk dir das richtig gut“). Voraussetzung: `/to-tickets` ist durch, `docs/agents/manifests/spec-<S>.json` liegt, Tickets haben native `blocked_by`-Kanten.

## Aufruf

- `/to-spawn <S>` oder `/to-spawn local <S>` — dieser PC (Standard).
- `/to-spawn local <S> --tickets 188,189,190` — nur diese Tabs (z. B. Neustart einzelner Wartetabs).
- `/to-spawn srv <S>` — **noch nicht gebaut** (siehe unten), heute nur Fehlermeldung + Hinweis.

## Ablauf `local` (deterministisch, Skript statt Prosa)

1. Im Repo-Wurzelordner: `pwsh -File ~/.claude/skills/to-spawn/spawn_local.ps1 -Spec <S> [-Tickets a,b,c] [-OhneWache] [-Window 1] [-DryRun]`
   - liest die Ticket-Nummern aus dem Manifest,
   - überspringt Tickets, für die schon ein `bau`/`claude`-Prozess läuft (nie Duplikate),
   - öffnet **ein** Windows-Terminal-Fenster: zuerst `wache <S>`, dann `bau <N>` in Nummernfolge (Tab-Titel = Befehl),
   - prüft nach 10 s per `scripts/sessions_stand.py <S>` und zeigt die Tabelle.
2. Ergebnis melden: die `sessions`-Tabelle (aus / wartet / läuft seit / VERWAIST) + Hinweis, wo Davids einziger Handgriff liegt (`checkpoint:human`-Tickets).
3. Danach nichts mehr anfassen. Warten macht `bau.py` selbst (GitHub-Poll alle 10 min, 0 Token); geblockte Tickets dürfen sofort auf.

## Kontrolle jederzeit

- `sessions <S>` (PowerShell-Profil → `scripts/sessions_stand.py`): je Ticket **aus / wartet / läuft seit HH:MM / VERWAIST**, mit Prozess- und Claude-PID — unabhängig davon, ob eine Session offen ist.
- Symbole am Tab: ✳ grün = arbeitet · ◐ = denkt/wartet auf Ergebnis · 🔔 = Zug fertig (Glocke, `preferredNotifChannel = terminal_bell` in `~/.claude.json`) · ⊗ = Prozess beendet.

## Harte Regeln (aus Vorfällen 17.09.2026)

- **Nie `bau.py` beenden ohne vorher `sessions <S>`.** Nur Zustand `wartet` darf gekillt werden. Ein gekilltes `bau.py` hinterlässt sein Claude-Kind verwaist; ein Neustart erzeugt ein Duplikat im selben Worktree (#187).
- **Keine Umgebungs-Variablen und kein `;` im Tab-Befehl** — `wt` liest `;` als Tab-Trenner, es entstehen kaputte Tabs in einem zweiten Fenster. Env (Transkript-Persistenz `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1`) setzen `bau.py`/`wache.py` selbst (seit 2d3bb5351).
- Nach dem Spawn immer per Prozessliste prüfen, nie „läuft“ behaupten.
- ⊗-Tabs erst schließen, wenn `sessions` keine VERWAIST-Zeile mehr zeigt.
- Tabs schließen kann `wt` nicht per Befehl; ganze Fenster schließt David.

## `srv` — geplant (noch nicht gebaut)

Ziel: dieselben Sessions als tmux/cmux-Fenster auf einem Bau-Server, damit der PC aus sein darf. Offene Punkte (grillen, bevor gebaut wird — siehe Memory `project_meta_exec_rem_bau_server_vision_2026_09_16`):
- Wo läuft Claude Code (Bau-VPS, Login/Token, `bau.py` portieren: `wt` → `tmux new-window -n "bau <N>"`).
- Worktrees `C:/dev/wt-<N>` → `/opt/wt-<N>`; Deploy-Skript vom Server aus; Git-Zugang.
- Kontrolle vom Handy: `sessions` als Web-Seite oder Telegram-Meldung; Glocke → Push.
- Browser-Beweise (claude-in-chrome) gibt es auf dem Server nicht → Playwright-Weg Pflicht.
Bis dahin antwortet `/to-spawn srv` mit genau diesem Hinweis und startet nichts.

## Anhang: manueller Einzeiler (falls das Skript nicht geht)

```
$repo=(Get-Location).Path; $cmds=@("wache 182")+(183..190|%{"bau $_"})
$a=@(); foreach($c in $cmds){ $a+=@("new-tab","--title","`"$c`"","-d","`"$repo`"","pwsh","-NoExit","-Command","`"$c`"",";") }
$a=$a[0..($a.Count-2)]; Start-Process wt -ArgumentList ($a -join " ")
```
