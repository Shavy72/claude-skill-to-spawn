# Bestandsaufnahme Regeltrennung (#584 · Beschlüsse E20, E21)

Stand: 2026-11-25 · Spec #557 · Querverweis App-Gegenstück: **App #1604** („Regeldateien schlank“).

Grundsatz (**E20**):
- **global** (`kette/global/*.md`, per `install.sh`/`install.ps1` nach `~/.claude/CLAUDE.md`): nur Regeln, die in jedem Repo gelten.
- **Skill** (`SKILL.md`, `aliase/*`, `kette/skills/*`, `review-dirigent/`, `to_spawn/anleitung.py`, `to_spawn/befugnis.py`, `repo-scripts/_default.json`): Ablaufbeschreibungen und feste Prompt-Quellen leben genau einmal im jeweiligen Skill.
- **Projekt** (Repo-`CLAUDE.md`, `.to-spawn/kette.md`, `start_text.zusatz_datei`, `.to-spawn/config.json`): alles, was nur ein einzelnes Repo betrifft — wird aus den globalen/Skill-Blöcken herausgelöst und als Befund für App #1604 gemeldet (App-Repo wird hier nicht geändert).

Grundsatz (**E21**):
- Wiederholter Agent-Fehler → zuerst Aufbau-Änderung, dann Lint-Regel oder Prüf-Skript vor Regeltext.

---

## Zählung vorher / nachher (Nachweis: keine Regel ersatzlos gelöscht)

| Bereich | Vorher | Nachher | Ersatzlos gelöscht | Neu / Erweitert | Projekt-Reste ausgelagert (Befund App #1604) |
|---|---:|---:|---:|---:|---:|
| `kette/global/beschluss-protokoll.md` | 11 | 11 | 0 | 0 | 0 |
| `kette/global/strategisches-design.md` | 13 | 13 | 0 | 1 (SD-11 erweitert um E21) | 1 (Flowbase-Pfad im Kopf) |
| `kette/skills/grill-with-docs/SKILL.md` | 3 | 3 | 0 | 0 | 0 |
| `kette/skills/to-spec/SKILL.md` | 6 | 6 | 0 | 0 | 0 |
| `kette/skills/to-tickets/SKILL.md` | 9 | 9 | 0 | 0 | 0 |
| `SKILL.md` + `aliase/*/SKILL.md` | 16 | 17 | 0 | 1 (SP-17 Regel-Prüfung #584) | 6 (Server-Repo/Hoster, Playwright-Memory, DuoPlus-Staffel, `safe_deploy_vps.sh`, Vorlagen-Name, DuoPlus-Spec-Verweis) |
| `to_spawn/anleitung.py`, `befugnis.py`, `skripte/wache.py`, `waechter_takt.py`, `_default.json` | 8 | 8 | 0 | 0 | 1 (`safe_deploy_vps.sh` in `wache.py`) |
| `review-dirigent/SKILL.md` | 1 | 1 | 0 | 0 | 0 |
| **Gesamt** | **67** | **68** | **0** | **+1 neu (+1 erweitert auf E21)** | **8 Projekt-Verweise als Befund gemeldet** |

---

## Tabelle Regel → Ort (jede Regel genau einmal)

| ID | Quelle | Regel (Kurzfassung) | Ort | Maßnahme |
|---|---|---|---|---|
| BP-01 | `kette/global/beschluss-protokoll.md` | Grundsatz: Grillen schreibt ins Protokoll, Spec liest nur daraus | global | bleibt unverändert |
| BP-02 | `kette/global/beschluss-protokoll.md` | Protokoll-Pfad `docs/GRILL_<JJJJ-MM-TT>_<thema>.md` beim ersten Beschluss anlegen und fortschreiben | global | bleibt unverändert |
| BP-03 | `kette/global/beschluss-protokoll.md` | Beschluss-Schwelle: nur ausdrückliche Bestätigung des Menschen ist ein Beschluss | global | bleibt unverändert |
| BP-04 | `kette/global/beschluss-protokoll.md` | Beschluss-Format: nur Endstände (`### E<n> — <Titel> ✅`, Beschluss, Herleitung, Zitat) | global | bleibt unverändert |
| BP-05 | `kette/global/beschluss-protokoll.md` | Pflicht-Abschnitte im Protokoll (`Ziel`, `Befunde`, `Verworfen ❌`, `Offen 💭`) | global | bleibt unverändert |
| BP-06 | `kette/global/beschluss-protokoll.md` | Geänderte Beschlüsse durchstreichen (`~~E3~~ überholt durch E9`), nie löschen | global | bleibt unverändert |
| BP-07 | `kette/global/beschluss-protokoll.md` | Abschluss-Status erst nach Bestätigung der E-Liste; ADRs/Glossar bleiben zusätzlich | global | bleibt unverändert |
| BP-08 | `kette/global/beschluss-protokoll.md` | Spec-Quelle ist allein das Protokoll, nie der Chat | global | bleibt unverändert |
| BP-09 | `kette/global/beschluss-protokoll.md` | Fehlendes Protokoll erst nachholen und bestätigen lassen | global | bleibt unverändert |
| BP-10 | `kette/global/beschluss-protokoll.md` | Spec-Zuordnung: nur ✅ mit `(E<n>)`, ❌ nach Out of Scope, 💭 nach Offene Fragen | global | bleibt unverändert |
| BP-11 | `kette/global/beschluss-protokoll.md` | Gegenprüfung per frischem Subagent (0/0/0) + Kopfzeile `Quelle: docs/GRILL_…` | global | bleibt unverändert |
| SD-01 | `kette/global/strategisches-design.md` | Grundsatz: jede Session plant zusätzlich strategisch statt nur taktisch | global | bleibt unverändert (Flowbase-Pfad im Kopf entfernt) |
| SD-02 | `kette/global/strategisches-design.md` | Design-Schritt (~10–20 %) mit Abschnitt „Design“ in jeder Planung | global | bleibt unverändert |
| SD-03 | `kette/global/strategisches-design.md` | Tiefe Module, schmale Schnittstellen; Komplexität nach unten ziehen | global | bleibt unverändert |
| SD-04 | `kette/global/strategisches-design.md` | Wissen verstecken: jede Entscheidung lebt in genau einem Modul | global | bleibt unverändert |
| SD-05 | `kette/global/strategisches-design.md` | Zweimal entwerfen: mind. zwei Varianten vor neuer Schnittstelle vergleichen | global | bleibt unverändert |
| SD-06 | `kette/global/strategisches-design.md` | Erst suchen, dann bauen; keine unbegründeten Zusatz-Parameter | global | bleibt unverändert |
| SD-07 | `kette/global/strategisches-design.md` | Fehler wegdesignen statt bei jedem Aufrufer abfangen | global | bleibt unverändert |
| SD-08 | `kette/global/strategisches-design.md` | Rückwirkend investieren, Null-Toleranz; außerhalb des Auftrags als Befund melden | global | bleibt unverändert |
| SD-09 | `kette/global/strategisches-design.md` | Drei Symptome aktiv prüfen (Änderungs-Lawine, Kopf-Last, unbekannte Unbekannte) | global | bleibt unverändert |
| SD-10 | `kette/global/strategisches-design.md` | Offensichtliche, einheitliche Namen + Schnittstellen-Kommentar (Was/Warum) | global | bleibt unverändert |
| SD-11 | `kette/global/strategisches-design.md` | **E21:** Wiederholter Agent-Fehler → Aufbau, Lint-Regel oder Prüf-Skript vor Regeltext | global | erweitert um E21-Reihenfolge (alte Regel enthalten) |
| SD-12 | `kette/global/strategisches-design.md` | Review-Abschluss (Pflichtfragen vor „fertig“) | global | bleibt unverändert |
| SD-13 | `kette/global/strategisches-design.md` | Design-Entscheidung an den Menschen in Alltagsworten (Kästen und Türen) | global | bleibt unverändert |
| GD-01 | `kette/skills/grill-with-docs/SKILL.md` | Grundablauf: Skills `grilling` und `domain-modeling` aufrufen | Skill (`grill-with-docs`) | bleibt unverändert |
| GD-02 | `kette/skills/grill-with-docs/SKILL.md` | Zusatz UX-Runde bei sichtbaren/hörbaren/fühlbaren Anteilen | Skill (`grill-with-docs`) | bleibt unverändert |
| GD-03 | `kette/skills/grill-with-docs/SKILL.md` | Zusatz Architektur-Runde bei mittleren/größeren Aufträgen als E-Beschlüsse | Skill (`grill-with-docs`) | bleibt unverändert |
| TS-01 | `kette/skills/to-spec/SKILL.md` | Pflicht-Abschnitt Beschluss-Protokoll (7 Schritte vor Projekt-Zusatz) | Skill (`to-spec`) | bleibt unverändert |
| TS-02 | `kette/skills/to-spec/SKILL.md` | Projekt-Vorrang (`.to-spawn/kette.md`) und Übergang (`.claude/skills/to-spec/SKILL.md`) | Skill (`to-spec`) | bleibt unverändert |
| TS-03 | `kette/skills/to-spec/SKILL.md` | Repo-Erkundung mit Glossar (`GLOSSARY.md`/`GLOSSARY-MAP.md`) und ADRs | Skill (`to-spec`) | bleibt unverändert |
| TS-04 | `kette/skills/to-spec/SKILL.md` | Schritt Repo-Zuordnung (`Skill-Repo` / `App-Repo` / `beide`, #579) | Skill (`to-spec`) | bleibt unverändert |
| TS-05 | `kette/skills/to-spec/SKILL.md` | Test-Seams auf höchster Naht entwerfen und mit dem Nutzer abstimmen | Skill (`to-spec`) | bleibt unverändert |
| TS-06 | `kette/skills/to-spec/SKILL.md` | Spec-Vorlage (`Quelle`, `Repo-Zuordnung`, `(E<n>)`, `Out of Scope`, `Offene Fragen`) + `ready-for-agent` | Skill (`to-spec`) | bleibt unverändert |
| TT-01 | `kette/skills/to-tickets/SKILL.md` | Pflicht-Abschnitt Beschluss-Protokoll (`spec-pruefen`, nur ✅, Modus unbeaufsichtigt) | Skill (`to-tickets`) | bleibt unverändert |
| TT-02 | `kette/skills/to-tickets/SKILL.md` | Projekt-Vorrang (`.to-spawn/kette.md`) und Übergang (`.claude/skills/to-tickets/SKILL.md`) | Skill (`to-tickets`) | bleibt unverändert |
| TT-03 | `kette/skills/to-tickets/SKILL.md` | Schritt 0: Lernstoff lesen (`to_spawn.py lernstoff`, `staffel.grenze_k`) | Skill (`to-tickets`) | bleibt unverändert |
| TT-04 | `kette/skills/to-tickets/SKILL.md` | Schritte 1–2: Spec lesen, Glossar/ADRs beachten, Kontext-Paket (`Pfad:Start-Ende`) | Skill (`to-tickets`) | bleibt unverändert |
| TT-05 | `kette/skills/to-tickets/SKILL.md` | Schritt Repo-Zuordnung vor dem Schnitt (`Skill-Repo` / `App-Repo` / `beide`, #579) | Skill (`to-tickets`) | bleibt unverändert |
| TT-06 | `kette/skills/to-tickets/SKILL.md` | Schritt 3: Vertical Slices, Blocker-Kanten, Abnahme-Ticket (`checkpoint:human`), Expand–Contract | Skill (`to-tickets`) | bleibt unverändert |
| TT-07 | `kette/skills/to-tickets/SKILL.md` | Schritt 4: Schnitt mit dem Nutzer abstimmen (im Modus unbeaufsichtigt automatisch) | Skill (`to-tickets`) | bleibt unverändert |
| TT-08 | `kette/skills/to-tickets/SKILL.md` | Schritt 5: Veröffentlichen (`einrichten`, `anhaengen` als einzige Tür für Kanten/Manifest) | Skill (`to-tickets`) | bleibt unverändert |
| TT-09 | `kette/skills/to-tickets/SKILL.md` | Schritt 6: Schluss-Prüfung (`to_spawn.py regularien <S>`) und Übergabe an `/to-spawn <S>` | Skill (`to-tickets`) | bleibt unverändert |
| SP-01 | `SKILL.md` | Aufruf-Einstiege (`/to-spawn`, `/to-spawn-local`, `/to-spawn-remote`, `/meta-exec`, `--tickets`) | Skill (`to-spawn`) | bleibt im Skill |
| SP-02 | `SKILL.md` | Skript-Verteilung, `.to-spawn/config.json`, Staffel-Hook, Aufsicht, Aufpasser, Aufseher-Anleitung | Skill (`to-spawn`) | App-Namen (`duoplus-management`, `safe_deploy_vps.sh`) neutralisiert |
| SP-03 | `SKILL.md` | Startklar-Prüfung vor jedem Aufseher-Start (venv, Schlüssel, Werkzeug-Hooks) | Skill (`to-spawn`) | bleibt im Skill |
| SP-04 | `SKILL.md` | Setup (`/to-spawn setup`: Terminal, Modell, Effort, Flaggschiff-Aufseher) | Skill (`to-spawn`) | bleibt im Skill |
| SP-05 | `SKILL.md` | Probesitz (7-Punkte-Abnahme des Setups) | Skill (`to-spawn`) | bleibt im Skill |
| SP-06 | `SKILL.md` | Ablösen (`/respawn`, `sichern`) und Eingriffs-Leiter (`leiter`, Stufen 0–5, Mindest-Ruhe) | Skill (`to-spawn`) | bleibt im Skill |
| SP-07 | `SKILL.md` | Umzug (`/to-spawn-of`: Bau-Session und Aufseher auf Bau-Server) | Skill (`to-spawn`) | bleibt im Skill |
| SP-08 | `SKILL.md` | Ablauf `local` (Windows-Terminal-Tabs, `sessions_stand.py`, keine Duplikate) | Skill (`to-spawn`) | bleibt im Skill |
| SP-09 | `SKILL.md` | Kontrolle jederzeit (`sessions <S>`, Tab-Symbole) | Skill (`to-spawn`) | bleibt im Skill |
| SP-10 | `SKILL.md` | Harte Regeln (nie `bau.py` ohne `sessions`, kein `;`/Env im Tab, Fix am Datei-Inhalt, kein Force-Push, nie außerhalb `bau.py`) | Skill (`to-spawn`) | bleibt im Skill |
| SP-11 | `SKILL.md` | Ablauf `srv` (Bau-Server, tmux, kopfloses Playwright, SSH-/crontab-Fallen) | Skill (`to-spawn`) | App-Reste (`netcup`, `~/duoplus-management`, `docs/BAU_SERVER.md`, `reference_cmo_…`) entfernt |
| SP-12 | `SKILL.md` | Speicher-Obergrenzen (6 je Spec / 12 je 16 GB / 24 gesamt / 2 GB frei / 20 s Staffel) | Skill (`to-spawn`) | bleibt im Skill |
| SP-13 | `SKILL.md` | Version-2-Kern (Regularien, Bau-Log, Deploy-Status, Warten, Wecker, Staffel, Aufseher, Abschluss-Paket, Thermo, Rückblick, Nacht-Checkpoints, Lernschleife) | Skill (`to-spawn`) | App-Reste (`safe_deploy_vps.sh`, `DuoPlus: aus`, DuoPlus-Spec-Verweis) entfernt |
| SP-14 | `SKILL.md` | Setup Teil 2: Werkzeug-Inventur (12 Kategorien, Abwahl-Liste) | Skill (`to-spawn`) | bleibt im Skill |
| SP-15 | `SKILL.md` | Bau-Leitstand (Live-Seite je Spec: `seed`, `vorbereiten`, `bestaetigen`) | Skill (`to-spawn`) | bleibt im Skill |
| SP-16 | `aliase/*/SKILL.md` | Alias-Skills (`meta-exec`, `to-spawn-local`, `to-spawn-remote`, `to-spawn-of`, `respawn`) ohne doppelte Kernlogik | Skill (`to-spawn` / Aliase) | bleiben dünne Verweise / eigene Teilabläufe |
| SP-17 | `SKILL.md` | Regel-Prüfung (`to_spawn.py regeln-pruefen` / `to_spawn.regel_pruefung`) gegen doppelte Ablauf-Texte außerhalb des Skills | Skill (`to-spawn`) | neu (#584, E20/E21) |
| AN-01 | `to_spawn/anleitung.py` | `MINDSET`, `STOSS`, `STOSS_SCHLUSS` (einzige Quelle für Prompts und Anstupser) | Skill (`to_spawn/anleitung.py`) | bleibt einzige Quelle |
| AN-02 | `to_spawn/anleitung.py` | `entscheidungs_linie()` (8 feste Punkte für Bau-Session und Aufseher) | Skill (`to_spawn/anleitung.py`) | bleibt einzige Quelle |
| AN-03 | `to_spawn/anleitung.py` | `ABLOESE_SOP` (5 Schritte für Bau-Session und Aufseher, Linux und Windows) | Skill (`to_spawn/anleitung.py`) | bleibt einzige Quelle |
| AN-04 | `to_spawn/anleitung.py` | `handoff_auftrag()`, `WEITER_AUFTRAG`, `REMOTE_CONTROL`, `EXIT` | Skill (`to_spawn/anleitung.py`) | bleibt einzige Quelle |
| AN-05 | `to_spawn/anleitung.py` | `ABSCHLUSS_ANWEISUNG` (Rundschau, Selbstprüfung, `live-bereit`, `ablegen`, Thermo, Rückblick, `nachsehen`, `live`) | Skill (`to_spawn/anleitung.py`) | bleibt einzige Quelle |
| AN-06 | `to_spawn/befugnis.py` | Befugnis-Klassen 1/2/3 (`start_text`, `aufseher_text`, `klasse_2_erkennung`) | Skill (`to_spawn/befugnis.py`) | bleibt einzige Quelle |
| AN-07 | `repo-scripts/_default.json` | Bau-Prompt-Vorlage (`prompt_template`) + getrennter Platzhalter `{REPO_ZUSATZ}` | Skill (`repo-scripts/_default.json`) | bleibt einzige Quelle (Projektregeln getrennt über `start_text.zusatz_datei`) |
| AN-08 | `skripte/wache.py` · `to_spawn/waechter_takt.py` | Aufseher-Prompt & Aufseher-Takt (Hänger, Rückfragen-Fenster, Checkpoints, Tick-Ablauf, Befehle A–E) | Skill (`wache.py` / `waechter_takt.py`) | `safe_deploy_vps.sh` in `wache.py` zu „Live-Deploy-Skript“ neutralisiert |
| RD-01 | `review-dirigent/SKILL.md` | Review-Dirigent (Route, parallele Prüfer + Rot-Probe, Richter, Beleg, Fix-Schleife) | Skill (`review-dirigent`) | bleibt unverändert (Projektregeln kommen dynamisch) |

---

## Befunde für das App-Repo (App #1604 — im App-Repo noch umzusetzen)

Diese projektspezifischen Inhalte wurden im Skill-Repo aus den allgemeinen Blöcken herausgelöst bzw. als Projekt-Thema eingeordnet (das App-Repo wird in diesem Ticket nicht verändert):

1. **Projekt-Pfade und Hoster-Angaben** (`~/duoplus-management`, `docs/BAU_SERVER.md`, `netcup-Bau-Server`, `docs/GRILL_2026-09-18_to_spawn_final.md`): gehören in die Projekt-Doku bzw. `.to-spawn/config.json` des App-Repos, nicht in `SKILL.md`.
2. **Projekt-Deploy-Skript `safe_deploy_vps.sh`**: gehört in `.to-spawn/config.json` (`deploy.skripte`) und die Projekt-Regeln des App-Repos (im Skill-Code bleibt die Übergangs-Vorgabe allein in `to_spawn/vorgaben.py`).
3. **Projekt-Vorgabe `staffel.aktiv: false` („DuoPlus: aus“)**: lebt in `.to-spawn/config.json` des App-Repos, nicht im Skill-Text.
4. **Playwright-Memory-Verweis `reference_cmo_browser_beweis_playwright`**: App-spezifischer Memory-Name, gehört in die Projekt-Regeln des App-Repos.
5. **Fremd-Repo-Pfad `Flowbase client/public/ebooks/tiefe-module.html`**: aus `kette/global/strategisches-design.md` entfernt (gehört nur ins Flowbase-Repo, nicht in globale Regeln).
6. **Anschluss an das neue Prüf-Skript (App #1604)**: Sobald das App-Repo seine Regeldateien (`CLAUDE.md`, `AGENTS.md`, `.claude/rules/*.md`, `.to-spawn/kette.md`, `docs/agents/*.md`) entschlackt, kann dort direkt `python ~/.claude/skills/to-spawn/to_spawn.py regeln-pruefen --wurzel .` (bzw. `python -m to_spawn.regel_pruefung --wurzel .`) in die CI eingebunden werden, um doppelte `to-spawn`-Ablauf-Texte dauerhaft zu verhindern.
7. **Marker-Kompatibilität (`to_spawn/globale_regeln.py`)**: `start_marker()` behält vorerst die byte-gleiche Signatur für das App-Skript `scripts/install_global_claude_blocks.py` (`test_marker_vertraeglich_mit_app_skript`); gemeinsamer Rückbau erfolgt koordiniert mit App #1604.
