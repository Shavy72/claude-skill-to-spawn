# Abnahme L1 USB-Stick — Belege

Haltepunkt 22.11.2026 04:05 (Server-Uhr): Skill-main b4be778, auf dem Bau-Server installiert b4be778 (Install Exit 0),
volle Suite unter Linux 2779 passed, 66 skipped, 18 xfailed, 0 rot (`~/systemcheck-1119/main_voll_b4be778.txt`).
Der Bau ruht; die M-Urteile unten sind nicht neu bewertet, die Belege darunter stammen vom 21.11.
Offen am Haltepunkt: 250 (Issue 149 Rest, Fix-Runde 3), 233 und 265 (Linux-Lauf + Merge), 255 (im Review),
Issue 214 Teil B (Review-Hook: Teil A gemergt, #237).

Älterer Stand: 21.11.2026 21:25 (Server-Uhr), Skill-main aaaab24 (über c2f4c07: Doku + Mess-Skripte `messung/`), installiert
auf dem Bau-Server c2f4c07 (21.11. 21:06). Belegdateien liegen auf dem Bau-Server unter
`~/systemcheck-1119/` (Abnahme-Läufe in `~/systemcheck-1119/abnahme_l1/`).

Urteile: **belegt** · **teilweise** · **abweichend** (Soll anders erfüllt, mit Grund) · **offen** (mit Issue).

## Etappe 1 „Fertig“ (Messlatte: Schnitt Etappe 1, Abschnitt 4)

| # | Soll | Befehl | Beleg | Urteil |
|---|---|---|---|---|
| F1 | Prüf-Test „keine festen Namen“ grün | `python -m pytest tests/test_feste_namen_l1.py -q` | PR 78: rot 7 failed (`rot_l1_p6b.txt`), grün 18 passed (`gruen_l1_p6b.txt`), grün auf c2f4c07 (installiert). Auf main aaaab24 **rot**: 1 failed, 3 passed, EXIT 1 — `test_jeder_code_ordner_wird_gescannt`: `['messung'] == []` (Ordner `messung/*.py` aus PR 82 nicht im Scan). Dateiname weicht vom Schnitt ab (`test_feste_namen_l1.py`). | belegt für c2f4c07; main rot → #113 |
| F2 | Ganze Skill-Suite grün | `python -m pytest tests -q -n 4` auf c2f4c07 | Bisher nur Auswahl-Lauf: 956 passed, 10 skipped, 4 xfailed, EXIT 0, 362 s (`main_pruef_c2f4c07.txt`). Voll-Lauf 2026-11-21 auf af86b29: 2346 passed, 48 skipped, 17 xfailed, 0 failed, EXIT 0, 378 s (`~/systemcheck-1119/main_voll_af86b29.txt`). | erfüllt |
| F3 | Frischer DuoPlus-Klon: `einrichten` legt `stand.json` an, `--pruefen` Exit 0 „alles da und aktuell“, `git status` leer | `git clone --shared --no-tags ~/duoplus-leit ~/wt/probe-klon-l1`; `to_spawn.py einrichten`; `… einrichten --pruefen; echo $?`; `git status --short` | `einrichten` EXIT 0, `stand.json` angelegt (nur `!! .to-spawn/stand.json` ignoriert, `git status` leer). `--pruefen` **EXIT 3**: `weiterleitungen: grosse_luecke — veraltet (getrackt): scripts/_to_spawn_weiterleitung.py, scripts/capo.py`. Ursache: App-Fassung hat `lade_skript` (App 9764fe348, #583, Zeile 58–68, genutzt `scripts/wirkungskreis.py:581`), die Skill-Vorlage nicht; `capo.py` nur Docstring Zeile 1. Gegenprobe mit Vorlagen-Dateien: `--pruefen` EXIT 0 „alles da und aktuell“ (`einrichten_pruefen_gegenprobe.txt`). Label `checkpoint:human` nur mit GitHub-origin prüfbar (Klon von lokalem Pfad: Lücke; nach `remote set-url` da_aktuell). | offen → #111 |
| F4 | Zweiter `einrichten`-Lauf: alles `da_aktuell`, `stand.json` unverändert | `stat -c %Y .to-spawn/stand.json` vor/nach zweitem Lauf | Lauf 2 EXIT 0, mtime 1795292102 → 1795292102, sha256 gleich; mit GitHub-origin erneut: 1795292169 → 1795292169. Einzige Nicht-`da_aktuell`-Zeile: Weiterleitungen (F3). `stand.json.skill_stand` ist leer (installierte Kopie ohne `.git`, `einrichten.py:_skill_stand`). | belegt (skill_stand leer → #89) |
| F5 | Baustein-Version erhöht → nächster `spawn --dry-run` zieht nach | `stand.json` `gh: 1 → 0` (simuliert älteren Stand); `to_spawn.py spawn 969 --ziel local --dry-run`; danach `to_spawn.py einrichten` | Dry-run EXIT 0, meldet `gh: veraltet`, schreibt nichts (sha256 gleich). `einrichten` EXIT 0: `gh: nachgezogen`, nur dieser Baustein. Der echte `spawn` ruft genau dieses `einrichten` zuerst (`to_spawn.py:688-692`), der Probelauf nur prüfend (`to_spawn.py:685-687`). | abweichend: dry-run zeigt nur, Nachziehen beim echten spawn |
| F6 | Wegwerf-Repo ohne `scripts/` und `.to-spawn/`: `spawn <S> --ziel local\|srv --dry-run` bricht nicht wegen fehlender Weiterleitungen ab; Fern-Befehl nennt Skill-`spawn_srv.sh` | `~/wt/probe-leer-l1` (nur Manifest `spec-969.json`); `spawn 969 --ziel local --dry-run`; `… --ziel srv --dry-run` | local: EXIT 0, Befehl `bash ~/.claude/skills/to-spawn/skripte/spawn_srv.sh 969 --dry-run`, nichts geschrieben. srv: EXIT 2 `Ziel Server braucht server_repo in .to-spawn/config.json` (Konfig-Pflicht, keine Weiterleitung). Mit nur `{"server_repo": …}`: EXIT 0, Fern-Befehl `… else cd … && TO_SPAWN_REPO="$PWD" bash ~/.claude/skills/to-spawn/skripte/spawn_srv.sh 969 --dry-run`. Im DuoPlus-Klon `--ziel srv --dry-run` auf dem Bau-Server EXIT 3: App-`scripts/hooks/skill_stand_wache.py` fragt `ssh bau-server` (#44, #89). PC (`spawn_local.ps1 -DryRun`, `install.ps1`) nicht prüfbar vom Server. | teilweise (PC offen → #68; srv im App-Klon → #89) |
| F7 | `aufpasser --trocken` und `aufraeumen --trocken` = Entscheidungen von 7c1500c | Vorher/nachher-Läufe beim Install c2f4c07 | Install 0cafd14 → c2f4c07: 0 geänderte Entscheidungen (`install_c2f4c07_diff_aufraeumen.txt`, nur Zeit/Zahlen; aufpasser nur Umgebungs-Unterschiede). Gegen 7c1500c nicht anwendbar: #1536 ändert Hausmeister-Entscheidungen bewusst. Lücke: der echte Cron-Lauf 21:15 hatte `68 Fehler`, die der Trockenlauf nicht zeigt — Ursache war der Probe-Eintrag aus F3 in `~/.config/to-spawn/maschine.json` (`_ticket_repo()` mit Eintrag `None`, ohne `Shavy72/duoplus-management`); Eintrag entfernt. | abweichend → #112 |

## Windows (Issue 68)

| # | Soll | Beleg | Urteil |
|---|---|---|---|
| W1 | Suite sammelbar | `gruen_l1_p7a.txt` (Linux-Simulation), P7b 497 passed | belegt (Linux-Simulation) |
| W2 | Windows-Lauf am PC | PC-Befehl im Kommentar an #68 | offen → #68 |

## Abnahme M1–M15

| # | Soll | Beleg | Urteil |
|---|---|---|---|
| M1 | Probe-Repo auf Bau-Server bis Staging, ohne Eingriff | – | noch nicht geprüft |
| M2 | dasselbe am Windows-PC (`--ziel local`) | – | noch nicht geprüft |
| M3 | zweite Spec: Einrichter „alles da“ | Vorstufe F4 | noch nicht geprüft |
| M4 | Soll-Liste +1 Baustein → Spawn zieht nach | Vorstufe F5 | noch nicht geprüft |
| M5 | Bau-Session + Aufseher beendet → Ablösung | – | noch nicht geprüft |
| M6 | keine Reste nach Abschluss | – | noch nicht geprüft |
| M7 | Abschluss-Paket, null Rückfragen | – | noch nicht geprüft |
| M8 | DuoPlus echte Spec + roter App-Zweig hält Skill nicht auf | – | noch nicht geprüft |
| M9 | Prüftor-Zeit < 5 min; DuoPlus vorher/nachher | – | noch nicht geprüft |
| M10 | `pruefen` in Python- und Node-Probe, Server + Windows | – | noch nicht geprüft |
| M11 | Kleinste Prüfung, drei Fälle | – | noch nicht geprüft |
| M12 | ≥ 20 absichtliche Fehler, 0 übersehen (DuoPlus ggf. „nicht anwendbar, weil keine Auswahl läuft“, siehe Plan-Stand) | – | noch nicht geprüft |
| M13 | `PRUEFTOR_VERGLEICH.md`, Tempo gemessen | – | noch nicht geprüft |
| M14 | Aufräumen ohne den Menschen (L4): Reste im Probe-Lauf, Hausmeister räumt, kein Lösch-Auftrag an den Menschen | – | offen |
| M15 | Kaltstart-Probe (L5) | Kaltstart-Probe 21.11.2026 auf dem Bau-Server: frische Sonnet-Session nur mit `docs/WIEDEREINSTIEG.md`, Urteil BESTANDEN, 0 verhindernde Lücken; Windows-Seite offen | belegt (Bau-Server), Windows offen |

## Offene Punkte

- Haltepunkt 22.11.: Windows-Seite (W2, M15 Windows) hängt an 233 und 265 (Linux-Lauf + Merge); die Prüf-Abnahmen
  (M9–M13) hängen an 250 (Fix-Runde 3) und der Prüf-Reihe danach; Review-Hook-Rest an Issue 214 Teil B.
- #113 main rot: `messung/` nicht im Scan der festen Namen → F1.
- #111 Weiterleitungs-Vorlage hinter App-Fassung → F3.
- #112 Probe-Eintrag in der Maschinen-Liste legt Hausmeister still, Trockenlauf zeigt es nicht → F7.
- #89 Skill-Stand-Stempel (`skill_stand` leer, `skill_stand_wache` per ssh) → F4, F6 srv.
- #68 Windows-Lauf am PC → F6, W2.
- F2 Voll-Lauf (Leit-Session).
- Probe-Ordner bleiben stehen: `~/wt/probe-klon-l1`, `~/wt/probe-leer-l1`.
