# Prüfpanel #257 — code-reviewer (Spec + Architektur)

Stand: 21.09.2026 · Worktree `/home/bau/wt/skill-257`, Zweig `ticket-257`, Basis `196aa73`, Diff uncommittet (24 geänderte + 7 neue Dateien). Nur gelesen, keine Tests gestartet.

## 1. Spec-Deckung (Tabelle B1–B10, Probesitz 7, .gitignore, Paket B 1–6)

| Befund | Code (Datei:Zeile) | Test | Stand |
|---|---|---|---|
| B1 `_default.json` repo-neutral | `repo-scripts/_default.json:6` (`{REPO}`, `{HAUPTZWEIG}`, `{CHECKPOINT_LABEL}`, deploy_befehl-Satz) · `skripte/bau.py:344-364` `build_prompt(konfig)` | `test_default_json_repo_neutral`, `test_build_prompt_ersetzt_repo_hauptzweig_checkpoint` | ✓ |
| B2 Staffel-Hook im Skill | `skripte/hooks/staffel_stop.py` (neu) · `bau.py:82-89` `staffel_hook_pfad` · `bau.py:390,657` · `probesitz.py:585-594,764-765` | `test_skill_hat_eigene_staffel_hook_kopie`, `test_staffel_hook_pfad_bevorzugt_repo_kopie`, `..._fallback_skill_kopie` | ✓ |
| B3 Checkpoint-Label | `to_spawn/gh.py:151-173` `label_sicherstellen` · `install.sh:76-89` · `install.ps1:83-84` · `to_spawn.py:178-183` (alle Schreibpfade von `setup`) · `manifest.py:365-368` Meldung | `test_label_sicherstellen_mit_gh_stub`, `..._ohne_gh` | ✓ Code+Test · Doku fehlt (s. 4) |
| B4 spawn ohne pwsh | `to_spawn/spawn.py:57-94` `_befehl_unix` (local → `spawn_srv.sh`, srv → `ssh <ssh_ziel> cd <server_repo> && bash scripts/spawn_srv.sh`), `:116-132` Skript-Check + `TO_SPAWN_REPO` | `test_baue_befehl_lokal_ohne_pwsh`, `..._tickets_und_dry_run`, `..._srv_linux`, `..._windows_bleibt_pwsh` | ✓ |
| B5 Hauptzweig | `gh.py:129-148` `hauptzweig` (Konfig → origin/HEAD → master/main → master) · `bau.py:219,227,235` · `capo.py:136-141` `haupt_ref` · `spawn_srv.sh:66-74` · `to_spawn.py:366,442-445` Unterbefehl · `config.py:79` DEFAULTS | 5× `test_hauptzweig_*`, `test_spawn_srv_kein_hartes_origin_master`, `test_spawn_srv_dry_run_zeigt_origin_main` | ✓ — aber `nest.py:158,200` sucht weiter selbst (Befund 3) |
| B6 Fenster ohne `~/.bashrc` | `spawn_srv.sh:120-146` (`$PY bau.py/wache.py`, `KURZ[]` für Fensternamen) · `nest/nest_server.sh` bashrc-Block wird ersetzt | `test_spawn_srv_fenster_ohne_shell_funktionen`, `test_spawn_srv_dry_run_zeigt_origin_main` · bashrc-Ersetzen: **kein Test** | ◐ |
| B7 Vertrauens-Dialog | `to_spawn/vertrauen.py` (neu, atomar, fremde Schlüssel bleiben) · `bau.py:832-834` (Worktree + Repo) · `wache.py:131-132` (Repo) | `test_vertrauen_setzt_flag_und_bewahrt_fremde_schluessel`, `test_vertrauen_legt_fehlende_datei_an` | ✓ |
| B8 `worktree_basis` | `config.py:74-77,137-152,155-160,213-233` · `capo.worktree_ordner` über `config.worktree_pfad` | `test_defaults_worktree_basis_und_hauptzweig_leer`, `test_worktree_pfad_mit_worktree_basis`, `..._ohne_feld_altes_verhalten`, `test_sicherstellen_frisch_setzt_repo_namen_in_worktree_basis`, `..._vorhandene_datei_bleibt_unangetastet_defaults_beim_lesen` | ✓ |
| B9 Bau-Log-Rückfall | `bau_log.py:64-77` `log_rueckfall` (Variante: `TO_SPAWN_LOG_RUECKFALL` statt `TO_SPAWN_REPO`, Begründung #205 im Kommentar) · `:80-104` `log_repo(versioniert=)` · `:176-192` `eintrag_schreiben(hauptbaum=)` · `:216-225` `lese(hauptbaum=)` · `:311-315` `zusammenfassung(hauptbaum=)` · Leser: `sessions_stand.py:256`, `probesitz.py:748`, `to_spawn.py:207` · Prompt-Satz „Worktree nie löschen" | `test_log_repo_faellt_auf_to_spawn_repo_zurueck` (nur Schreibziel) · **Leser mit `hauptbaum` ungetestet** (Spec: „Leser findet sie") | ◐ |
| B10 Rückfrage-Hänger | `_default.json` Satz „Unbeaufsichtigt: keine Rückfragen …" | `test_default_json_repo_neutral` (`"keine Rückfragen"`) | ✓ |
| Probesitz Punkt 7 | `probesitz.py:202-225` `punkt_nicht_konfiguriert`/`offene_punkte(ohne_nicht_konfiguriert=)` · `:238-241` `alle_gruen` bleibt streng · `:810-817` Betreff „ohne Staging" | `test_offene_punkte_staging_nicht_konfiguriert_sperrt_nicht`, `..._mit_anderem_grund_sperrt` · Betreff ungetestet (niedrig) | ✓ |
| Fremd-Repo `.gitignore` | `config.py:163-193` `GITIGNORE_BLOCK`/`gitignore_ergaenzen`, Aufruf `:224` | **kein Test** (Spec: „zweimal aufrufen = ein Block") | ✗ Test |
| Paket B 1 Nest 5b | `nest/nest_server.sh` Abschnitt 5b (Swap ≥ 8 GiB, fstab, `exit-empty off`, `tmux-bau.service` OOMScoreAdjust=-900, transient-Erkennung) | `test_nest_server_bash_syntax_ok`, `..._nennt_swap_und_dienst`, `..._trockener_plan_nennt_swap_und_dienst` | ✓ |
| Paket B 2 `speicher.py` | `to_spawn/speicher.py` (neu) · `config.py:102-110` DEFAULTS · `to_spawn.py:411-418,446-448` CLI Exit 0/5 | 11 Tests `test_speicher_257.py:35-160` (Attrappen `/proc`, CLI-Exits) | ✓ |
| Paket B 3 Staffel spawn_srv/Aufpasser | `spawn_srv.sh:164-169,177-192` · `aufpasser.py:682-685,1551-1571,1659` | `test_spawn_srv_prueft_speicher_und_staffelt` = Text-Grep (`"speicher" in text`), `test_aufpasser_ruft_platz_frei` = Text-Grep; Spec verlangt „dry-run zeigt Staffel", „Aufpasser trocken lässt Fenster aus" | ◐ |
| Paket B 4 wache/bau nur bei Speicher | `wache.py:125-129` · `bau.py:819-824` Exit 5 | `test_wache_ruft_platz_frei`, `test_bau_ruft_platz_frei` = Text-Grep, kein Exit-5-Lauf | ◐ |
| Paket B 5 Tests | `tests/test_speicher_257.py`, `tests/conftest.py` Attrappe „immer frei" | — | ✓ (Inhalt s. o.) |
| Paket B 6 Doku | `SKILL.md:95-97`, `README.md:71-79`, `to_spawn/README.md:25,191,196-208` · DuoPlus `docs/BAU_SERVER.md` hier nicht prüfbar | — | ✓ |

**Zählung: 12/17 Punkte voll gedeckt, 4 teilweise (B6, B9, Paket B 3, Paket B 4 — Test schwächer als Spec), 1 ohne Test (.gitignore).** Spec-Schritt 3 (Doku für B1–B10) fehlt komplett (Befund 6).

## 2. Befunde

| # | Schwere | Datei:Zeile | Befund | Fixvorschlag |
|---|---|---|---|---|
| 1 | **mittel** | `skripte/bau.py:190`, `skripte/wache.py:68` | Fallback `repo_aus_origin("Shavy72/duoplus-management")` — ein Fremd-Repo ohne GitHub-Origin (oder mit ssh-Alias, den der Parser nicht erkennt) bekommt DuoPlus-Slug im Prompt (`{REPO}`), in `gh api repos/{REPO}/issues/…` und in `blocker_offen` — genau der B1-Fehler auf neuem Weg. Plug-and-Play-Grundsatz verletzt. | Fallback `""`; leer → `log.error("kein GitHub-Origin …")` + Exit 2 vor dem Claude-Start (bau.py) bzw. Abbruch (wache.py). Test: Repo ohne origin → Exit ≠ 0, kein `duoplus` im Prompt. |
| 2 | **mittel** | `skripte/spec_stand.py:26,56,64,67,82,100` | Wird vom Installer nach `~/.claude/skills/to-spawn/skripte/` kopiert und per `repo-scripts/spec_stand.py` weitergeleitet, enthält aber `REPO = "Shavy72/duoplus-management"`, `--wt-basis C:/dev`, `ssh clawy-vps … /opt/duoplus-management`, `origin/master` (5×). Im Fremd-Repo liefert das Skript falsche Zahlen bzw. bricht. | `REPO` aus `repo_aus_origin`/`TO_SPAWN_REPO`, Hauptzweig über `gh.hauptzweig`, VPS-Zeile nur wenn Konfig-Feld (z. B. `deploy_host`) gesetzt, `--wt-basis` aus `config.worktree_pfad`. Alternativ: DuoPlus-Skript aus dem Skill nehmen (repo-scripts-Vorlage bleibt). |
| 3 | **mittel** | `to_spawn/nest.py:158,200` | Eigene Suche `origin/main`, `origin/master` statt `gh.hauptzweig(repo)` — Spec B5 verlangt „ein Helfer". Konfig-Feld `hauptzweig` und `origin/HEAD` werden dort ignoriert; bei einem Repo mit Hauptzweig `develop` legt das Nest den Worktree von einem falschen Zweig an. | `for kandidat in (f"origin/{gh.hauptzweig(hauptrepo)}",)` bzw. `capo.haupt_ref(hauptrepo)` nutzen; Fehlermeldung Z200 entsprechend. |
| 4 | **mittel** | `tests/test_fremdrepo_257.py` (fehlend) | Zwei Spec-Tests fehlen: (a) `config.gitignore_ergaenzen` idempotent („zweimal aufrufen = ein Block", Datei fehlt → anlegen); (b) B9-Leser: Laufzeile im Hauptbaum → `bau_log.lese(worktree, hauptbaum=…)`, `eintrag_schreiben(hauptbaum=…)`, `probesitz.werte_bau_log` finden sie. Ohne (b) ist das Ticket-Kriterium „Bau-Log-Zeile bleibt erhalten" nur zur Hälfte belegt. | Zwei Tests ergänzen (tmp_path, keine Attrappen nötig). |
| 5 | **mittel** | `tests/test_speicher_257.py:165-193` | Starter-Tests prüfen nur Quelltext (`"speicher.platz_frei" in text`), kein Verhalten. Spec Paket B 5: „`spawn_srv.sh --dry-run` zeigt die Staffel, Aufpasser trocken lässt Fenster aus", plus bau/wache Exit 5. Ein Umbenennen der Funktion oder ein toter Aufruf hinter `return` bliebe grün. | Verhaltens-Tests: `spawn_srv.sh --dry-run` im Test-Repo (wie `test_spawn_srv_dry_run_zeigt_origin_main`) → Ausgabe enthält „Staffel: 20 s"; Aufpasser `--trocken` mit `TO_SPAWN_SPEICHER_MEMINFO` knapp → Zeile „nicht gestartet"; `wache.py`/`bau.py --dry-run`-nahe Prüfung oder `platz_frei`-Stub → Exit 5. |
| 6 | **mittel** | `SKILL.md:18,26,27`, `to_spawn/README.md:161`, `docs/kontext-manifest.md`, `README.md` | Doku-Schritt 3 der Spec fehlt für B1–B10: kein Wort zu `hauptzweig`, `worktree_basis`, Absatz „Vor dem ersten spawn" (Label), Vertrauens-Dialog, Platzhalter `{REPO}`/`{HAUPTZWEIG}`/`{CHECKPOINT_LABEL}`, Linux-`spawn` ohne pwsh. Widersprüche: `SKILL.md:27` „Der Staffel-Hook bleibt im Repo" (Spec: „Z27 anpassen"), `SKILL.md:18` „`--ziel srv` braucht `pwsh`", `to_spawn/README.md:161` „braucht `scripts/hooks/staffel_stop.py` im Repo". Nur Paket B 6 (Speicher) ist dokumentiert. | Konfig-Liste `SKILL.md:26` + `to_spawn/README.md:188` um beide Felder ergänzen; Absatz „Vor dem ersten spawn" (Label, Vertrauens-Dialog, .gitignore committen); Z18/Z27/Z161 korrigieren; Platzhalter-Tabelle in `docs/kontext-manifest.md`. |
| 7 | niedrig | `to_spawn/hooks.py:243-247,480`, `deploy_status.py:97`, `bau_loop.py:73`, `to_spawn.py:235`, `bau_log.py:383,448,529` | `lese`/`zusammenfassung` ohne `hauptbaum`. Folge im B9-Fall (Worktree gelöscht): Stop-Hook liest den Hauptbaum, findet die `session_start`-Zeile aus dem Worktree nicht und schreibt eine zweite (`hooks.py:320`). Kennzahlen bleiben richtig (`_juengste_je_session`), nur `starts` zählt doppelt. | `_hat_zeile` und `umrechnen` mit `hauptbaum=bau_log.log_rueckfall()` aufrufen. |
| 8 | niedrig | `to_spawn/inventur.py:96`, `to_spawn/aufpasser.py:132` | Plug-and-Play-Rest: `PROJEKT_WOERTER = ("duoplus", "postproxy", "n8n", "adb", "airtable")` und `DEPLOY_MUSTER`-Vorgabe mit DuoPlus-Skriptnamen. Beides harmlos (Heuristik bzw. Konfig-Ergänzung), aber projektbezogen im Skill-Kern. | Beide Listen als Konfig-Felder mit leerer/neutraler Vorgabe (`inventur.projekt_woerter`, `deploy_muster`). |
| 9 | niedrig | `repo-scripts/_default.json:6` vs. `config.py` `staffel.grenze_k = 200` | Prompt sagt jetzt „Ziel 100–150k, bei 150k Handoff", Staffel-Regel des Skills greift bei 200k — zwei Schwellen für dieselbe Sache (vor #257: beide 200). | Eine Zahl: Prompt aus `staffel.grenze_k` füllen (`{GRENZE_K}`) oder Prompt zurück auf 200k. |
| 10 | niedrig | `to_spawn/vertrauen.py:49-77` | `~/.claude.json` wird als Ganzes gelesen und ersetzt; Claude Code schreibt dieselbe Datei aus laufenden Sessions. Fenster Lesen→`os.replace` ist klein, Verlust einer fremden Änderung aber möglich. Nur beim ersten Mal je Pfad (danach byte-gleich, kein Schreiben) — in-Spec, Restrisiko. | Optional: Retry-Vergleich (mtime vor/nach Lesen) oder nur bei fehlendem Eintrag schreiben (schon so). Kein Fix nötig, im Issue nennen. |
| 11 | niedrig | `skripte/spawn_srv.sh:67` | `hauptzweig` läuft mit `python3`, `speicher` mit `$PY` (`.venv` bevorzugt, erst Z121 definiert). Bei Repo ohne System-`python3` fällt der Hauptzweig still auf `master` zurück. | `PY`-Zuweisung vor Schritt 1 ziehen und beide Aufrufe darüber. |
| 12 | niedrig | `to_spawn/config.py:224` (Aufrufer `bau.py:664`, `wache.py:96`) | `sicherstellen` ändert bei jedem `bau`/`wache`-Start die Repo-`.gitignore` (einmalig, dann idempotent). Im Fremd-Repo entsteht so eine uncommittete Änderung im Hauptbaum, die Ticket-Worktrees (von origin) nicht sehen → dort liegt `.to-spawn/bau_log/` untracked. | In Doku „Vor dem ersten spawn": `.gitignore` + `scripts/` committen; oder `gitignore_ergaenzen` nur in `install.sh --repo`/`setup`, nicht in `bau`/`wache`. |
| 13 | niedrig | `to_spawn/capo.py:391-403`, `config.py:73,77` | Zwei Felder für dieselbe Basis: `wt_basis` (#213, nur Capo-Regel verwaist) und `worktree_basis` (#257). Sind beide gesetzt und verschieden, prüft der Wächter einen anderen Ordner, als `bau.py` anlegt. `worktree_ordner` ruft `regel(ticket)` ohne `repo` (liest cwd/`TO_SPAWN_REPO`). | `wt_basis` als Alias auf `worktree_basis` deklarieren (leer → `worktree_basis`), Doku-Zeile. |
| 14 | niedrig | `to_spawn/nest.py:167,931`, `skripte/sessions_stand.py:253` | `worktree_pfad(ticket)` ohne `repo` — korrekt nur, wenn cwd oder `TO_SPAWN_REPO` das Repo ist (sessions_stand: bewusst, Kommentar; nest: ohne Kommentar). | Kommentar oder `repo` durchreichen. |

Nicht bemängelt (geprüft, in Ordnung): `hauptzweig`-Reihenfolge = Spec; `label_sicherstellen` fail-open mit Warnung = Spec; `alle_gruen` bleibt streng (Exit 1 bei Staging rot) = Spec; `spawn()` setzt `TO_SPAWN_REPO` + cwd; `install.sh` `cp -a` und `install.ps1` `Copy-Item -Recurse` nehmen `skripte/hooks/` mit; `to_spawn.py setup` legt das Label auf allen Schreibpfaden an (`--standard`, Flags, Dialog); `speicher.platz_frei` fail-open ohne Linux/`/proc`; `aufpasser` zählt Starts nur echt, Pause nicht im Trockenlauf; `waechter_lauf._log_repo(versioniert=True)` und `_eintrag` weichen nie in den Hauptbaum aus; DuoPlus-`.gitignore` hat den Block schon (kein Dirty-Tree dort).

## 3. Plug-and-Play-Grep (ohne tests/, docs/)

| Treffer | Bewertung |
|---|---|
| `skripte/bau.py:190`, `skripte/wache.py:68` Fallback-Slug | Befund 1 (mittel) |
| `skripte/spec_stand.py:26,56,64` | Befund 2 (mittel) |
| `to_spawn/inventur.py:96`, `to_spawn/aufpasser.py:132,787` | Befund 8 (niedrig) |
| `to_spawn/capo.py:401`, `to_spawn/config.py:72,75,138,151,158,160` `C:/dev` | Windows-Vorgabe der Worktree-Basis, laut Spec B8 bewusst („DuoPlus unverändert") — ok |
| `repo-scripts/_to_spawn_weiterleitung.py:31`, `repo-scripts/spawn_srv.sh:9`, `skripte/spawn_srv.sh:85` | Quelle des Skills selbst (`Shavy72/claude-skill-to-spawn`) — ok |
| `to_spawn/context_mode.py:1`, `deploy_status.py:1`, `capo.py:1` | Docstring-Verweise auf Ticketnummern — ok |

`master`-Grep: `gh.py` (Helfer selbst), `capo.py:846` (Fehlertext), `umzug.py:67` (Schutzmenge master+main, ok), `nest.py:158,200` (Befund 3), `spec_stand.py` (Befund 2). `*.ps1`: keine Treffer.

## 4. Aufrufer-Konsistenz

- `config.worktree_pfad(ticket, repo)`: `bau.py:341` und `probesitz.py:733` mit `repo`; `sessions_stand.py:253` bewusst ohne (Alt-Tests stubben 1-arg, Kommentar); `nest.py:167,931` ohne (Befund 14); `capo.py:399` ohne.
- `bau_log.lese/zusammenfassung(hauptbaum=…)`: `sessions_stand.py:256`, `probesitz.py:748`, `to_spawn.py:207` (`eintrag`) — die drei Leser der Spec. Ohne: `hooks.py:246,480`, `deploy_status.py:97`, `bau_loop.py:73`, `to_spawn.py:235`, `bau_log.py:383,448,529` (Befund 7).
- `bau_log.log_repo(versioniert=True)`: `to_spawn.py:192` (`eintrag`), `waechter_lauf.py:189` — beide versionierten Schreiber. Hooks (`hooks.py:303,390`) und `umrechnen` (`to_spawn.py:222`) ohne → Rückfall in den Hauptbaum, wie gewollt.

## 5. Tests — nur ergänzt?

`git diff 196aa73 -- tests/`: `conftest.py` (+2 autouse-Fixtures: `TO_SPAWN_*` löschen, Speicher-Attrappe „immer frei"; `_waechter_sofort` hängt davon ab — Reihenfolge korrekt), `test_bau_log_204.py`, `test_setup_208.py`. Keine Datei gelöscht, `test_umzug_205.py` unverändert (Nachtrag: Fix lag im Code, nicht im Test — richtig herum).

| Änderung | Urteil |
|---|---|
| `test_bau_log_204::test_prompt_repo_kopie_ist_identisch` → `test_prompt_repo_kopie_platzhalter_teilmenge_skill_bleibt_neutral` | Berechtigt durch Nachtrag 18:40 (Skill-Vorlage repo-neutral, DuoPlus-Kopie behält Deploy-Sätze). Neue Invariante ist schwächer (Teilmenge + kein DuoPlus-Wort statt Identität), aber die einzig mögliche, wenn beide Dateien absichtlich auseinanderlaufen. Docstring nennt #257. Übrige Diff-Zeilen in der Datei sind reine Formatierung (Ruff-Reflow, kein Inhalt). |
| `test_setup_208::test_erster_start_ohne_tty_legt_vorgaben_an_mit_hinweis` | Berechtigt durch B8 (`worktree_basis` beim ersten Anlegen); Test prüft zusätzlich `endswith("/" + repo.name)` — strenger als vorher. |
| `test_fremdrepo_257::test_sicherstellen_vorhandene_datei_bleibt_unangetastet_defaults_beim_lesen` | Umdrehung laut Nachtrag (Datei byte-gleich, `lade()` füllt aus DEFAULTS) — deckt sich mit B8-Text „vorhandene Datei bleibt unangetastet". |

Neue Tests: 30 (`test_fremdrepo_257.py`) + 20 (`test_speicher_257.py`). Lücken: Befund 4 und 5.

## 6. Offen für David (aus Spec, nicht Code)

- Windows `install.ps1` gespiegelt (Label-Anlage, `Copy-Item -Recurse` nimmt `hooks/` mit), aber nicht durchgespielt — Ticket-Kriterium „einmal auf Davids PC".
- Befund 10 (Race auf `~/.claude.json`) ist in-Spec; ob das Restrisiko akzeptiert wird.
