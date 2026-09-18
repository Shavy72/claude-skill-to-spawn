---
name: to-spawn
description: "Alle Ticket-Sessions einer Spec auf einmal starten — lokal (Windows-Terminal-Tabs) oder auf dem Bau-Server (`ssh bau-server`, tmux-Sitzung `spec-<S>`, Fenster je `bau <N>` + `wache <S>`, Laptop darf aus), Warten außerhalb von Claude (0 Token), Kontrolle per `ssh bau-server sessions <S>`. Trigger: /to-spawn, „spawn alle Terminals“, „starte alle Tickets“, nach /to-tickets. Einstiege: `/to-spawn <S>` fragt lokal (1) oder Server (2) · `/to-spawn-local <S>` (Windows-Terminal-Tabs auf diesem PC) · `/to-spawn-remote <S>` (Bau-Server, ersetzt `srv`); `/meta-exec` = alter Name."
disable-model-invocation: false
---

# /to-spawn — Bau-Sessions einer Spec starten

Quelle: `github.com/Shavy72/claude-skill-to-spawn` — Änderungen nur dort, dann installieren: Linux `bash install.sh [--repo <Pfad>]`, Windows `pwsh -File install.ps1 [-Repo <Pfad>]` (kopiert Skill + Alias-Skills nach `~/.claude/skills/`, alter Stand wandert nach `~/.claude/skills/_alt/`).

Stand 2026-09-17 (David: „richtig geil … merk dir das richtig gut“). Voraussetzung: `/to-tickets` ist durch, `docs/agents/manifests/spec-<S>.json` liegt, Tickets haben native `blocked_by`-Kanten.

## Aufruf

Drei Einstiege (#205), alle landen in `python ~/.claude/skills/to-spawn/to_spawn.py spawn <S> [--ziel local|srv] [--tickets a,b]` (prüft erst die Regularien):
- `/to-spawn <S>` — **du (das Modell) fragst David** „lokal (1) oder Server (2)?“ (AskUserQuestion, Vorschlag = `ziel_default` aus `.to-spawn/config.json`) und rufst dann mit `--ziel local` bzw. `--ziel srv` auf. Nie ohne `--ziel` aus dem Bash-Tool starten: dort ist die Eingabe leer, die Skript-Frage nimmt still die Vorgabe.
- `/to-spawn-local <S>` — Alias-Skill, `--ziel local`: Konsolen auf diesem PC (Windows-Terminal-Tabs).
- `/to-spawn-remote <S>` — Alias-Skill, `--ziel srv`: Bau-Server (tmux `spec-<S>`, Laptop darf aus). Ersetzt `/to-spawn srv <S>`. `--ziel srv` braucht `pwsh` (Windows-Laptop); **auf dem Bau-Server selbst** stattdessen `bash scripts/spawn_srv.sh <S> [--tickets a,b]` im Repo-Wurzelordner.
- `… --tickets 188,189,190` — nur diese Tickets (z. B. Neustart einzelner Wartefenster); gilt für alle drei.
- `/meta-exec` — alter Name, Alias auf `/to-spawn`.

## Umzug #205 — Skripte im Skill, Repo nur Weiterleitungen

- Logik liegt in `~/.claude/skills/to-spawn/skripte/` (`bau.py`, `wache.py`, `sessions_stand.py`, `spec_stand.py`, `spawn_srv.sh`), direkt startbar im Repo-Wurzelordner. Repo = `TO_SPAWN_REPO`, sonst Git-Wurzel des aktuellen Ordners — nie der Skill-Ordner.
- Das Repo trägt nur dünne Weiterleitungen gleichen Namens unter `scripts/` (+ Helfer `scripts/_to_spawn_weiterleitung.py`, Vorlage in `repo-scripts/`). Sie setzen `TO_SPAWN_REPO` = Ordner über `scripts/` und springen in den Skill (`$TO_SPAWN_HOME`, Vorgabe `~/.claude/skills/to-spawn`); fehlt der Skill → Meldung + Exit 3. `bau <N>`/`wache <S>`/`sessions <S>` bleiben unverändert.
- Repo-Konfig `.to-spawn/config.json` (legt `bau.py`/`wache.py`/`to_spawn.py pruefen|spawn` beim ersten Start mit Vorgaben an, überschreibt nie): `terminal`, `ziel_default`, `ssh_ziel`, `runner`, `modelle` + `effort` je Rolle (`ticket`, `ticket_leicht`, `waechter`), `staffel`, `mail.ziel`, `regularien.checkpoint_label`, `staging_start`, `deploy_befehl`. Verdrahtet sind heute: `ziel_default`, `ssh_ziel`, `modelle.ticket` (`bau`, Vorrang `--model` > Konfig > `_default.json`), `modelle.waechter` (`wache`), `regularien`, `staffel`. Die übrigen Felder sind angelegt, werden aber erst von den Folge-Tickets der Spec #202 gelesen — eine Änderung dort wirkt noch nicht. `--dry-run` legt nichts an. `.gitignore`: `.to-spawn/*` + `!.to-spawn/config.json`.
- Der Staffel-Hook bleibt im Repo (`scripts/hooks/staffel_stop.py`).

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

## `srv` — Bau-Server (gebaut 2026-09-17)

Dieselben Sessions als tmux-Fenster auf dem netcup-Bau-Server (`ssh bau-server`, Nutzer `bau`, Repo `~/duoplus-management`, Doku `docs/BAU_SERVER.md`), damit der PC aus sein darf. Worktrees liegen dort unter `~/wt/wt-<N>` (`$BAU_WT_DIR`, Default `~/wt`).

1. Vom Laptop: `pwsh -File ~/.claude/skills/to-spawn/spawn_srv.ps1 -Spec <S> [-Tickets "a,b,c"] [-OhneWache] [-DryRun] [-Zielserver bau-server]`
   - ruft `scripts/spawn_srv.sh <S> ...` per SSH auf dem Server auf (kein SSH-Login nötig, Key liegt bereit),
   - Server macht `git fetch && git merge --ff-only origin/master` (Abbruch bei Konflikt, nie rebase/stash), liest das Manifest, überspringt laufende Tickets,
   - legt tmux-Session `spec-<S>` an: Fenster `wache <S>` + je Ticket ein Fenster `bau <N>`,
   - gibt nach 10 s die `sessions <S>`-Tabelle aus.
2. Kontrolle: `ssh bau-server sessions <S>` (Tabelle) · Live reinschauen `ssh -t bau-server tmux attach -t spec-<S>` (Fenster wechseln `Strg+B n`, raus ohne zu beenden `Strg+B d`).
3. Harte Regeln gelten gleich (nie `wartet` überspringen, kein Duplikat, kein Kill ohne `sessions`-Check).
4. Browser-Beweise auf dem Server: **kein** `claude-in-chrome` (kein Desktop) — Playwright-Weg (`scripts/beweis_*.py`-Vorlagen), siehe Memory `reference_cmo_browser_beweis_playwright`.
5. SSH-Falle Windows: `Bad permissions` auf `.ssh/config`/Key-Datei → `icacls <Datei> /inheritance:r; icacls <Datei> /grant:r "$env:USERNAME:(R)"` einmalig fixen.

## Anhang: manueller Einzeiler (falls das Skript nicht geht)

```
$repo=(Get-Location).Path; $cmds=@("wache 182")+(183..190|%{"bau $_"})
$a=@(); foreach($c in $cmds){ $a+=@("new-tab","--title","`"$c`"","-d","`"$repo`"","pwsh","-NoExit","-Command","`"$c`"",";") }
$a=$a[0..($a.Count-2)]; Start-Process wt -ArgumentList ($a -join " ")
```

## Version 2 (vorläufig, 2026-09-18) — Kern aus Grill/Spec #202

Python-Kern `to_spawn.py` (Paket `to_spawn/`, Tests `tests/`, Doku `to_spawn/README.md`). Befehle:
- `python ~/.claude/skills/to-spawn/to_spawn.py pruefen <S> [--ohne-github]` — Regularien, Exit 0 = frei, Exit 3 = Weigerung (nichts startet), jede Fehlerzeile mit Abhilfe. Weigerung bei: Pflichtfeld (`schaetzung_k`/`umfang`) fehlt · Schätzung ≥ 200 · Ticket-Schlüssel doppelt im Manifest · offenes Ticket doppelt belegt (auch in anderer `spec-*.json`) · „## Blocked by #X" aus der Spec ohne native Kante (Meldung nennt den `gh api`-Befehl) · kein Ticket trägt `checkpoint:human` (Name aus `regularien.checkpoint_label`) · GitHub nicht abfragbar (außer `--ohne-github`). Nur Warnung: Blocker-Nummer außerhalb der Spec · kein Ticket hat eine Kante · offenes Ticket hat schon einen Assignee.
- `… spawn <S> [--ziel local|srv]` — fragt „lokal (1) oder Server (2)?", prüft erst Regularien, delegiert dann an `spawn_local.ps1` / `spawn_srv.ps1`. Die beiden prüfen selbst noch einmal vor dem Start (`spawn_srv.sh` weigert sich zusätzlich, wenn der Skill fehlt).
- `… log <S>` — Gesamt-Tabelle aus `docs/agents/bau_log/<N>.jsonl` (Token, Dauer, Staffel-Zähler) · `… lernstoff` — Zeilen für /to-tickets: Schätzung → Ist, Sessions, Faktor je Ticket + Faustregeln (mittlerer Faktor, Sessions je Umfang-Art, Tickets mit Staffel > 1).
- `… hook-stop` / `… hook-subagent-stop` — Stop-/SubagentStop-Hooks (JSON auf stdin) schreiben `session_ende`/`subagent_ende` mit Token-Summen aus dem Transkript.
- Staffel (200k): Hook kann eine Session nicht beenden (Doku: `continue:false` endet nur die Runde) → Hook setzt Marker `.to-spawn/stop-<N>`, `to_spawn/bau_loop.py` beendet das Kind und startet die Folge-Session mit Handoff als Startkontext (max 3 Staffeln). Verdrahtung in `bau.py` = Ticket #203/#204.
- Drei Einstiege (gebaut #205, siehe „Aufruf“) · `/to-spawn-of` = Umzug (#212, offen).
- Entscheidungen + Ticket-Kette (#203–#214): `docs/GRILL_2026-09-18_to_spawn_final.md` im DuoPlus-Repo, Spec #202.
