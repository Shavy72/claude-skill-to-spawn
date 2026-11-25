# Befundseite #757 — Probe 1330: Nachstellung und Einordnung (Linux-Teil / Kern)

Ticket: Shavy72/claude-skill-to-spawn#757 (aufgeteilt aus #291; Linux-/Kern-Teil auf dem Bau-Server, keine Windows-Zweige angefasst).

## Übersicht aller 5 Punkte

| # | Beobachtung (Probe 1330) | System-Einordnung | Urteil | Beleg / Maßnahme |
|---|---|---|---|---|
| 1 | Force-Push auf `wip/`-Zweig (App-Ticket 1482) | `system:kern` | **Lücke belegt** (kein Bug in `erlaubt.py`, aber kein Pre-Push-/PreToolUse-Schutz für `wip/*`/`ticket/*`) | Nachgestellt in `tests/test_probe_1330_757.py` (`test_pre_push_master_sperre_laesst_wip_force_push_durch`, `test_erlaubt_sperrt_force_push_auch_auf_eigene_zweige`). Regel in `SKILL.md` ergänzt; separates Folge-Ticket (Git-/Hook-Schutz für Force-Push auf eigene Zweige) empfohlen (hier nicht angelegt, siehe Offen). |
| 2 | Aufseher-Kommentar „Mensch nötig — Review angefordert, aber kein Beleg geschrieben“ während Bau-Session lief | `system:kern` | **Auf `main` bereits behoben** (#530 / PR #563, #465 / #498) | Ursache nachgestellt (`review-hook/stop.py` schreibt `grund: "review_offen"`, vor #530 meldete `capo.py` sofort). `to_spawn/mensch_noetig.py` (`review_stand`, 15-min-Wartezeit + Beleg-Prüfung) + Rücknahme in `capo.py` + Repo-/Worktree-Filter sind auf `main`. Beleg: `tests/test_review_offen_530.py` + `tests/test_probe_1330_757.py`. Offen: Live-Nachweis im nächsten Probe-Lauf (vgl. #530). |
| 3 | Stop-Hook „Review fällig – Mensch nötig“ hält die Session nicht auf, Meldung bleibt folgenlos | `system:kern` | **Kein Fehler** beim Durchlassen (Endlos-Schutz nach `review/VERTRAG.md` §8); „folgenlos“ nur wegen fehlendem Aufseher in Probe 1330 | Nachgestellt per echtem Hook-Lauf (`test_stop_hook_hinweis_grenze_laesst_durch_und_schreibt_mensch_noetig`): 1. Stop liefert `decision: "block"`, 2. Stop lässt planmäßig durch, gibt `systemMessage: "Review fällig – Mensch nötig: …"` aus UND schreibt `review_offen` nach `mensch_noetig.jsonl`. Im Normalbetrieb liest `capo.py` diese Zeile (identischer Pfad zu Punkt 2); in Probe 1330 lief kein Aufseher/Aufpasser-Cron (vgl. #276). |
| 4 | `crontab <langer Pfad>` scheitert auf Linux (Pfad abgeschnitten); `crontab - < datei` geht | `system:linux` | **Live belegt + Code-Schutz & Doku ergänzt** | Auf diesem Linux-Server (`cron` / `/usr/bin/crontab`, Vixie-cron `MAX_FNAME = 100`) live nachgestellt: ein 130-Zeichen-Pfad `/tmp/aaa…a.cron` wird still auf 99 Zeichen abgeschnitten (`No such file or directory`). Alle bestehenden Aufrufer (`aufpasser.py`, `waechter_takt.py`, `wecker.py`) nutzen bereits `aufpasser._crontab("-", eingabe=...)`. Neu: `aufpasser._crontab` sperrt Dateipfad-Argumente mit `ValueError`; `SKILL.md` dokumentiert die Falle. |
| 5 | „Ist der Fix drin?“ per Commit-Abstammung geprüft (Schlange setzt Commits neu auf) | `system:kern` | **Nachgestellt + Lehre in `SKILL.md` ergänzt** | Per echtem Git-Lauf (`test_commit_abstammung_falsch_negativ_nach_rebase_inhalt_richtig`) belegt: Nach Cherry-Pick/Rebase liefert `git merge-base --is-ancestor <alt-sha> main` Exit 1 (falsch-negativ), während `git show main:<pfad>` den Fix am Datei-Inhalt nachweist. Code-Audit der eigenen `merge-base --is-ancestor`-Stellen (`capo.py:regel_vps`, `einreichen.py`, `bau.py`, `hausmeister.py`) ohne Fehlverhalten (Hausmeister prüft seit #253 Baum-/Patch-ID). Regel in `SKILL.md` ergänzt. |

## Detail-Belege

### 1. Force-Push auf einen Ticket-/WIP-Zweig (`system:kern`)
- **Projekt-Hook (`pre-push-master-sperre.sh`, eingerichtet über `to_spawn/nest.py`):** Zeile 98 prüft `[ "$fern_ref" = "refs/heads/master" ] || continue` — jeder Push (auch `--force`) auf `wip/*` oder `ticket/*` geht durch.
- **PreToolUse-Hooks (`skripte/bau.py`, `to_spawn/hooks.py`):** sperren `AskUserQuestion`, rohes `gh issue close <Spec>` (`hook-spec-sperre`) und `ja`-Aufrufe (`hook-ja-sperre`), aber kein `git push --force`.
- **Verlauf-Prüfung (`to_spawn/erlaubt.py`):** `_GIT_PUSH_FLAGS` enthält nur `-u`, `--set-upstream`, `-q`, `--quiet`, `-v`, `--verbose`, `--porcelain`, `-n`, `--dry-run`. Sowohl `--force`, `-f`, `--force-with-lease` als auch `+wip/...` werden als nicht harmlos gemeldet (→ Klasse 2 in `verlauf_pruefung.py`).
- **Folge-Ticket-Vorschlag (falls Force-Push aktiv geblockt werden soll):** `system:kern` — PreToolUse- oder Git-Pre-Push-Sperre für Force-Push auf `wip/*` und `ticket/*` (nach Rebase neuen kurzen Branch-Namen oder Fast-Forward-Weg vorschreiben).

### 2. Aufseher-Kommentar „Mensch nötig — Review angefordert, aber kein Beleg geschrieben“ (`system:kern`)
- **Mechanismus:** `review-hook/stop.py` (Z. 646–658) ruft im Hinweis-Modus beim zweiten Stopp ohne Beleg `mensch_noetig_merken(..., grund="review_offen")` auf.
- **Stand auf `main`:** Commit `23c67ec` (#530 / PR #563) führte `mensch_noetig.review_stand` (`REVIEW_WARTEZEIT_MIN = 15` und `beleg_da`) sowie die automatische Rücknahme in `to_spawn/capo.py` (Z. 2545–2561) ein. Commits `c180f16` / `92659cd` (#465 / #498) filterten Einträge fremder Repos/Worktrees.
- **Rest:** Die Frage „PC oder Server“ für die historischen Kommentare vom 22.11. (17:17, 18:08) im App-Repo ist ohne die damaligen Host-Logs nicht trennbar, für den Fix aber unerheblich, weil der Pfad auf beiden Systemen identisch im Kern liegt. Der Live-Beleg im nächsten Probe-Lauf ist bereits in #530 offen.

### 3. Stop-Hook „Review fällig – Mensch nötig“ (`system:kern`)
- **Vertrag:** `review/VERTRAG.md` §8 (Z. 212): Endlos-Schutz nach 1 Block (`hinweis`) bzw. `max_fix_runden` Blocks (`scharf`) → durchlassen, Meldung „Mensch nötig“.
- **Nachstellung:** Im echten Subprocess-Lauf gegen `review-hook/merker.py` + `review-hook/stop.py` blockiert Stop 1 (`decision: "block"`), Stop 2 lässt planmäßig durch (`"decision"` fehlt, `systemMessage` meldet `"Review fällig – Mensch nötig: …"`) und schreibt die JSON-Zeile `grund: "review_offen"` in `mensch_noetig.jsonl`.
- **Einordnung:** Kein Fehler im Stop-Hook. Die scheinbare „Folgenlosigkeit“ am 23.11. 00:03 kam daher, dass in Probe 1330 auf dem Server kein Aufseher/Aufpasser-Cron lief (bereits in #276 dokumentiert).

### 4. `crontab <langer Pfad>` unter Linux (`system:linux`)
- **Live-Beleg auf dem Bau-Server (`cron` / `/usr/bin/crontab`):**
  `crontab /tmp/probe757_nichtda_<100×a>.cron` scheitert mit Exit 1 und meldet einen auf 99 Zeichen abgeschnittenen Pfad (`No such file or directory`), weil Vixie-cron `MAX_FNAME = 100` (99 Zeichen + NUL) nutzt.
- **Bestands-Prüfung:** `to_spawn/aufpasser.py`, `to_spawn/waechter_takt.py` und `to_spawn/wecker.py` übergeben bereits `"-"` und den Inhalt über stdin (`eingabe=...`).
- **Härtung:** `to_spawn/aufpasser.py::_crontab` weist jetzt jedes Dateipfad-Argument (außer `"-"` und Schalter inkl. `-u <nutzer>`) mit `ValueError` ab. `SKILL.md` dokumentiert die Falle im Server-Abschnitt.

### 5. „Ist der Fix drin?“ per Commit-Abstammung (`system:kern`)
- **Nachstellung:** Nach `git cherry-pick` (wie beim Neu-Aufsetzen durch eine Deploy-Schlange oder Rebase-/Squash-Merge) liefert `git merge-base --is-ancestor <alt-sha> main` Exit 1, obwohl `git show main:modul.py` den neuen Inhalt enthält.
- **Code-Audit der eigenen `merge-base --is-ancestor`-Stellen:**
  - `to_spawn/capo.py:regel_vps`: prüft Commits aus `origin/<hauptzweig>` gegen den dort ausgecheckten VPS-/Staging-HEAD (gleiche Commit-Linie).
  - `to_spawn/einreichen.py:_ist_vorfahr`: prüft vor/nach eigenem `--merge`-PR.
  - `skripte/bau.py`: prüft den unveränderten Umzugs-Branch `origin/<branch>`.
  - `to_spawn/hausmeister.py`: erkennt neu aufgesetzte Commits seit #253 per Baum-/Patch-ID.
- **Maßnahme:** Lehre in `SKILL.md` unter „Harte Regeln“ aufgenommen.
