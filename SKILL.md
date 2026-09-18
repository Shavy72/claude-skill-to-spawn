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
- Repo-Konfig `.to-spawn/config.json` (legt `bau.py`/`wache.py`/`to_spawn.py pruefen|spawn` beim ersten Start mit Vorgaben an, überschreibt nie): `terminal`, `ziel_default`, `ssh_ziel`, `runner`, `modelle` + `effort` je Rolle (`ticket`, `ticket_leicht`, `waechter`), `staffel`, `mail.ziel`, `regularien.checkpoint_label`, `staging_start`, `deploy_befehl`. Verdrahtet sind heute: `ziel_default`, `ssh_ziel`, `modelle.ticket` (`bau`, Vorrang `--model` > Konfig > `_default.json`), `modelle.waechter` (`wache`), `regularien`, `staffel`. `terminal` ist ein Objekt je Plattform (`{"win32": "wt", "linux": "tmux", "darwin": "tmux"}`), weil die eingecheckte Konfig zwischen Windows und Server geteilt wird; ein alter Text-Wert wird beim nächsten Setup umgewandelt. `terminal`, `modelle.ticket_leicht` und `effort` setzt das Setup (#208), gelesen werden sie aber erst von den Folge-Tickets der Spec #202 — eine Änderung dort wirkt noch nicht. Die übrigen Felder sind angelegt, aber ebenfalls noch nicht verdrahtet. `--dry-run` legt nichts an. `.gitignore`: `.to-spawn/*` + `!.to-spawn/config.json`.
- Der Staffel-Hook bleibt im Repo (`scripts/hooks/staffel_stop.py`).

## Setup (`/to-spawn setup`, #208)

Wählt Terminal (nur für die Plattform, auf der es läuft), Modell und Effort je Rolle (`ticket`, `ticket_leicht`, `waechter`) und schreibt NUR `.to-spawn/config.json` (atomar, Rechte bleiben, alle übrigen Felder bleiben). Startet und beendet nichts — laufende Sessions bleiben unberührt, neue Starts lesen die Werte. Jederzeit wiederholbar.

Ablauf für dich (das Modell), wenn die Konfig fehlt oder der Nutzer „setup“ sagt:
1. `python ~/.claude/skills/to-spawn/to_spawn.py setup --zeigen` — Optionen je Plattform (mit Status bereit / nicht installiert / geplant), Modelle, Effort-Stufen, aktuelle Werte. Zeigt „In der Datei“ (Rohwerte) getrennt vom Standard. Schreibt nichts; mit Setz-Flags = Exit 2.
2. Per AskUserQuestion fragen: Terminal, Modell + Effort für Ticket, Modell + Effort für leichtes Ticket, Effort für Wächter. Standard (= aktueller Wert) zuerst mit „(Empfohlen)“. Geplante Terminals nur nennen, nicht anbieten.
3. `python ~/.claude/skills/to-spawn/to_spawn.py setup --terminal <t> --modell-ticket <id> --effort-ticket <e> --modell-leicht <id> --effort-leicht <e> --effort-waechter <e>` — nur genannte Werte ändern sich, ungültiger Wert = Exit 2, nichts geschrieben. `--standard` = alle Standards ohne Fragen, zusätzliche Wert-Flags gewinnen darüber.

- Wächter läuft immer auf Fable 5.1 (`claude-fable-5-1`); es gibt nur `--effort-waechter`, ein anderes Wächter-Modell wird beim Speichern überschrieben.
- Im eigenen Terminal reicht `python ~/.claude/skills/to-spawn/to_spawn.py setup` (Fragen nacheinander, Enter = Standard). Ohne echtes Terminal (Bash-Tool, Pipe) ohne Flags = Exit 2; `--dialog` erzwingt den Dialog über die Pipe.
- Erster `spawn` in einem Repo ohne Konfig: im echten Terminal läuft der Dialog von selbst, sonst werden die Vorgaben angelegt mit dem Hinweis auf `setup`.
- Unlesbare Konfig wird nie überschrieben (Exit 1 mit Meldung, auch bei `--zeigen`; der Dialog prüft das vor der ersten Frage).

## Umzug `/to-spawn-of` (#212) — laufende Session auf den Bau-Server verschieben

- Alias-Skill `/to-spawn-of` (`aliase/to-spawn-of/SKILL.md`), Logik `to_spawn/umzug.py`. In einer Bau-Session (`BAU_TICKET`): Handoff mit Zeile `Umzug: server` (auch als Überschrift `## Umzug: server`; nie `Staffel: weiter`), eigene Arbeit mit Pathspec committen (auch neue Dateien — ungetrackte außer dem Handoff = Weigerung, git-ignorierte zählen nicht), dann `python ~/.claude/skills/to-spawn/to_spawn.py umzug <N> --handoff <pfad> [--dry-run]`. Das Skript committet nur den Handoff, pusht den Branch (Beweis `ls-remote` = HEAD, `ls-remote`-Fehler = Exit 1), startet per `ssh <ssh_ziel>` im Server-Repo (`server_repo`, Vorgabe `~/<Repo-Ordner>`) `spawn_srv.sh <S> --tickets <N> --ohne-wache --umzug <branch>@<sha>:<pfad>` (läuft das Ticket dort schon: Exit 4 → Abbruch; bricht SSH ab, wird einmal geprüft, ob der Server trotzdem läuft) und beendet die lokale Session erst nach Beweis (tmux-Fenster `bau <N>` + `sessions <S>` nicht `aus`, bis 90 s) über `BAU_UMZUG_DATEI` → `bau.py` beendet das Claude-Kind (Windows: `taskkill /T` für den ganzen Baum, danach Prüfung; nicht beendet = laute Meldung, Exit 1), keine Staffel-Runde. Liegt `<BAU_UMZUG_ANFRAGE>.laeuft`, schreibt der Umzug sein Ergebnis hinein (`exit`, `grund`) für den Wächter. Exit 0 = umgezogen · 3 = Weigerung · 1 = Server nicht bewiesen, lokal läuft weiter.
- Im Wächter (`TO_SPAWN_WACHE_SPEC`): Frage „Ganzen Bau auf den Bau-Server verschieben?“, bei Ja `… umzug-alle <S> [--ohne-wache] [--warte-max s] [--dry-run]` im Hintergrund (`run_in_background`) — Tickets aufsteigend, streng nacheinander, Zustand je Ticket direkt vor der Aktion neu gelesen: `wartet` → erst auf dem Server starten + beweisen, dann lokal neu lesen (noch `wartet` → lokales `bau.py` beenden; inzwischen `läuft` → Server-Fenster schließen, Anfrage-Weg; Server scheitert → lokal unangetastet, Stopp); `läuft` → Anfrage-Datei `.to-spawn/umzug-anfrage-<N>`, der Stop-Hook `hook-umzug` gibt der Session die Umzug-Anweisung, meldet die Session einen Fehlschlag, stoppt der Wächter sofort; `VERWAIST` oder Zeitüberschreitung → Stopp, Rest bleibt lokal. Die Anfrage-Datei wird immer aufgeräumt (auch Strg+C/SIGTERM). Zum Schluss `spawn_srv.sh <S> --nur-wache` und die lokale Wächter-Session endet.
- Server-Seite: `bau.py --umzug <branch>@<sha>:<pfad>` (impliziert `--sofort`) holt den Branch (`git fetch`-Fehler = Exit 2), prüft, dass `<sha>` in `origin/<branch>` liegt, liest den Handoff genau aus diesem Commit (alte Form `<branch>:<pfad>` liest `origin/<branch>`) und setzt den Worktree auf diesen Branch; `spawn_srv.sh` kennt `--umzug`, `--ohne-regularien`, `--nur-wache`.

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
- `… deploy-status [--datei P] [--ticket N] [--still-min 60]` — liest nur `.deploy_status.jsonl` (schreibt `safe_deploy_vps.sh` je Phasenwechsel), hängt jede neue Phase als `deploy_phase` ans Bau-Log des Repos, in dem die Datei liegt (ohne Doppel; Ticket aus `--ticket`, `BAU_TICKET` oder `wt-<N>`), und meldet in ≤ 3 Zeilen Phase, Dauer, letzten Schritt. Exit 0 = grün · 1 = rot (Grund in der Ausgabe; auch Gate-Prozess laut PID tot ohne Ende-Zeile) · 3 = läuft · 4 = seit über 60 min keine neue Phase oder Zeitstempel unlesbar (Prozess prüfen) · 2 = keine Statusdatei (Gate läuft nicht, Start prüfen). Für die Gate-Wache im Loop alle 5 min statt Log-Volltexte (#207).
- `… hook-stop` / `… hook-subagent-stop` — Stop-/SubagentStop-Hooks (JSON auf stdin) schreiben `session_ende`/`subagent_ende` mit Token-Summen aus dem Transkript.
- Staffel (200k): Hook kann eine Session nicht beenden (Doku: `continue:false` endet nur die Runde) → Hook setzt Marker `.to-spawn/stop-<N>`, `to_spawn/bau_loop.py` beendet das Kind und startet die Folge-Session mit Handoff als Startkontext (max 3 Staffeln). Verdrahtung in `bau.py` = Ticket #203/#204.
- Wächter (#213): `wache <S>` startet mit `--remote-control "Wächter #<S>"` + `--fallback-model` und wechselt beim Nutzungs-Limit selbst auf `modelle.waechter_ausweich` (Aufsicht liest das Transkript, Bau-Log `waechter_modell`, Mail). Tick = `python scripts/capo.py <S> [--dry-run] [--uebersicht]`: Stand je Ticket, nur neue Bau-Log-Zeilen, Regeln commit_ohne_nummer · beweis_fehlt · test_ersetzt · vps_ungleich_origin (öffnet wieder, je Schließ-Ereignis einmal) und session_verwaist (Kommentar + Mail, je Tag einmal); Mail nur kritisch (Gate rot, Session tot, Live-Beweis blockiert, Ausweich-Modell) + Spec fertig über `mail.befehl` (leer = keine Mail, nur INFO-Zeile, kein Fehler). Karenz nach dem Schließen: `waechter.karenz_minuten` (Vorgabe 15, `0` = keine); Ausgangsstand = beim ersten Tick schon geschlossene Tickets. Sessions melden `… eintrag --typ blockiert --grund "…"` bzw. `--typ entscheidung --frage … --wahl … --grund …`.
- Drei Einstiege (gebaut #205, siehe „Aufruf“) · `/to-spawn-of` = Umzug (#212, siehe Abschnitt „Umzug“).
- Entscheidungen + Ticket-Kette (#203–#214): `docs/GRILL_2026-09-18_to_spawn_final.md` im DuoPlus-Repo, Spec #202.

## Setup Teil 2 — Werkzeug-Inventur (#209)

Zweck: aus Projekt, Session-Historie und Skill-Katalog eine großzügige Abwahl-Liste bauen. Standard = alles an, der Nutzer wählt ab statt an. 12 Kategorien in fester Reihenfolge: Bauen · Testen/Beweisen · Design/Frontend · Recherche · Medien · Deploy/Betrieb · Projekt-Spezial · Sicherheit/Secrets · Kontext/Gedächtnis · Steuerung/Meldung · Sehen/Verstehen · Zweitmeinung/Sonderfähigkeiten.

Ablauf (im Repo-Wurzelordner):
1. `python ~/.claude/skills/to-spawn/to_spawn.py inventur --json` laufen lassen (liest nur, legt nichts an).
2. Analyse-Agent mit `model: sonnet` starten, Anleitung `~/.claude/skills/to-spawn/docs/inventur-agent.md`, Eingabe = die JSON-Ausgabe. Er liefert höchstens 15 Zeilen Vorschläge und ändert nichts.
3. Dem Nutzer die Liste (`inventur` ohne `--json`) plus Vorschläge zeigen und fragen, was er abwählen will.
4. `… inventur --abwahl a,b --schreiben` → `.to-spawn/werkzeuge.json`. Ein späterer Lauf behält die Abwahl (auch für Werkzeuge, die gerade nicht installiert sind: „abgewählt, derzeit nicht gefunden“); `--anwahl a` nimmt sie zurück; unbekannter Name → Exit 2 mit gültigen Namen; kaputte `werkzeuge.json` → Exit 4, Datei bleibt unberührt. Ohne `--schreiben` endet die Liste mit „nicht gespeichert“.

Gut zu wissen: Setup-Zeilen erscheinen nur für FEHLENDE Unterbauten (codex, gemini, ffmpeg, adb, gh), jede mit Fehlgrund und Abhilfe; gh zählt erst mit bestandenem `gh auth status`, codex erst mit `~/.codex/auth.json` oder nicht leerem OPENAI_API_KEY. Aus `.env` liest die Inventur nur Schlüssel-Namen mit nicht leerem Wert, nie Werte. Gelesen werden `~/.claude` und die Projekt-Ordner `.claude/skills` + `.claude/agents` des Repos. Einordnung = Schlüsselwort-Tabelle `REGELN` in `to_spawn/inventur.py` (Name vor Beschreibung, erste Regel gewinnt); eingebaute Skills/Agenten haben eine feste Kategorie — Fehlgriffe meldet der Analyse-Agent. Tote Winkel: leere Kategorien, Skill-Ordner ohne SKILL.md, doppelte Kurznamen, fehlende Historie, genutzte, aber nicht installierte Skills/Befehle, `agents/`-Verweis ohne Agenten-Dateien.

werkzeuge.json liest heute noch niemand beim Start — die Übergabe an die Bau-Session (z. B. --disallowedTools) folgt im Nest-Ticket #210.
