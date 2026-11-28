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
| M1 | Probe-Repo auf Bau-Server bis Staging, ohne Eingriff | Probe-Lauf 3 am 25.11. (siehe Abschnitt „Probe-Lauf 3“ unten): `-mit` Bau fertig 01:39 ohne Eingriff, aber 2 System-Anomalien; `-ohne` Bau fertig 03:00, 3 Anomalien, 2 Eingriffe der Leit-Session. Abnahme durch den Nutzer 25.11. 04:17 ohne Wartezeit im Minuten-Takt. | teilweise (Messlatte „null Anomalien“ nicht bestanden → 624, 641, 650) |
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
| M12 | ≥ 20 absichtliche Fehler, 0 übersehen (DuoPlus ggf. „nicht anwendbar, weil keine Auswahl läuft“, siehe Plan-Stand) | **Probe-Repo (Pflicht-Nachweis, 3b-2b #104, PR #955 / `f99a79e`):** zweite, vorher unbekannte Fehlerreihe (`messung/fehler_reihe_2.py`, 26 Fehler: 16 Python + 10 Node, alle 4 Pflicht-Klassen Daten-Datei, Template, dyn. Import, `mock.patch`-String) gegen eingefrorene Regeln `regeln@fd7e49f` → `26/26` voll rot ⇒ `26/26` `to_spawn.pruef.pruefen` rot, **0 übersehen** (Gegenprobe Auswahl roh: `11/26` übersehen; `docs/PRUEFTOR_VERGLEICH.md` Abschnitt 4.2, `tests/test_fehler_reihe_2_104.py`). **DuoPlus (3b-5 #105):** **nicht anwendbar, weil keine Auswahl läuft** — Mess-Vorstufe 3b-0 (#98, Commit `cf2561a`) hat über Abbruch-Kriterium (b) (`179/302 = 59,3 % >= 50 %` R1/R3-Voll-Quote) und (c) (`3` unbefangene Fehlgriffe `1ed5b52`, `61e877f`, `0cba2e3`) den Wechsel auf Variante 3 allein (schnellerer Voll-Lauf, keine Test-Auswahl) entschieden; laut Leit-Entscheidung 21.11. 21:05 (`docs/PLAN_L1_usb_stick.md`) entfällt der Fehler-Einbau im App-Repo (0 eingebaute Fehler, kein Schein-Nachweis). Siehe Abschnitt „Etappe 3b-5 (#105): M12 DuoPlus & Bremse bei 2 gleichzeitigen Gates“ unten. | belegt (Probe-Repo erfüllt; DuoPlus nicht anwendbar, weil keine Auswahl läuft) |
| M13 | `PRUEFTOR_VERGLEICH.md`, Tempo gemessen | **`PRUEFTOR_VERGLEICH.md` + Probe-Repo (3b-6 #106):** Vergleich liegt vor; Vorprüfung (Stufe 0–3, `to_spawn/pruef/vorpruefung.py`, CLI `to_spawn.py pruefen <basis> <ziel> --vorpruefung`) + Gate-Platz-Pool (2. Platz nur bei Bremse `<= 1,5` aus #105 mit RAM-/Last-/Session-Schutz) im Skill-Repo gebaut und geprüft (`tests/test_vorpruefung_schlange_106.py`). Realer 10-Ticket-Parallellauf im isolierten Probe-Repo (`messung/m13_vorpruefung_messung.py`, Last `0,79 < 12`): Rot-Beweis `2/2` Syntax-Fehler-Tickets in Stufe 1 abgewiesen (`1,37–1,38 s`) **ohne Gate-Platz**, `2/2` Nur-Doku ohne Gate, `6` gültige Tickets über max. `2` parallele Gate-Plätze (`Median 1,73 s`, `P90 2,42 s`, max. Wartezeit `0,311 s`, `MemAvailable min 8176 MiB`, Swap-Delta `+0 MiB`, kein RAM-Riss). **DuoPlus-Schlange (ehrlich):** Mit der real gemessenen 3b-5-Bremse (`1,29×–1,34× <= 1,5×`) und 5-s-Vorprüfung (50–80 % Fangquote) sinken Wartezeiten `> 15 min` im Fahrtenbuch von `9,5 %` auf `1,6–2,0 %` und Gate-Minuten um bis zu `31 %` (`8714 → 6006`), aber **Median `121–147 s` (`> 112 s`)**, **P90 `2063–2259 s` (`> 1213 s`)** und **10-Ticket-Probe `84–94 %` Runden `> 15 min` Warten** bleiben durch den Voll-Lauf (`T_voll` Median `1838 s`) über den M13-Zielen — **Erreichbarkeit im App-Repo bleibt unbelegt** (Einbindung in App-`scripts/deploy_schlange*` erst Staging, App-Live bleibt Entscheidung des Nutzers). Siehe Abschnitt „Etappe 3b-6 (#106)“ unten. | teilweise (Probe-Repo + Rot-Beweis + Bremse `<= 1,5` belegt; M13 DuoPlus-Schwellen unbelegt / nicht erreicht, App-Staging offen) |
| M14 | Aufräumen ohne den Menschen (L4): Reste im Probe-Lauf, Hausmeister räumt, kein Lösch-Auftrag an den Menschen | Beleg #136 + #177: `tests/test_temp_ordner_136.py` und `tests/test_temp_ordner_177.py` (Bau, Aufseher und Prüf-Tür legen je Ticket/Spec markierte Temp-Ordner `t<ticket>-…` über `temp_ordner.anlegen()` an; Hausmeister räumt echte markierte Temp-Reste geschlossener Tickets aller drei Aufrufer, schützt offene/frische/laufende/ungesicherte/kopierte/Hardlink-/Symlink-/TOCTOU-Reste, Prüf-Test „kein festes `/tmp`“ grün, Schutz-Hooks blocken `aufraeumen --alles --loeschen` nicht, A24-Übergabe-Regel in `to_spawn/anleitung.py`); Altbestand `/tmp`/`/tmp/claude-*` per `tmpfs`-Neustart entschieden (#178). Live-Beleg Probe-Lauf 5 (Bau-Server, 26.11.2026, Skill-Stand `f305a07`, `~/systemcheck-1119/probe_lauf_5.md:11`): alle Probe-5-Sitzungen (`-mit` Spec `#41`, `-ohne` Spec `#61`) sowie ein eigenständiger Prüf-Tür-Lauf (`to_spawn.py pruefen origin/main HEAD` in `wt-62`, Exit 0) legten markierte Ordner unter `~/tmp-bau` statt `/tmp` an (`von="wache"`, `von="bau"`, `von="pruef"`). Nach Altern (> 24 h) der geschlossenen Tickets `#42` (`t42-kb4bw2bg`, `t42-qi0g8eeq`) und `#62` (`t62-v2dud8mp`, `t62-mz9044p2`) löschte `to_spawn.py aufraeumen --alles --loeschen` alle 4 Ordner ohne Hook-Block (`~/.claude/to-spawn/hausmeister.jsonl:176417-176420`), während offene/frische Ordner (`t41-xycdlwzi`, `t43-bqms15yp`, `t61-nz419rxc`, `t71-v1kfx1u2`) erhalten blieben. Rest-Befund „Exit 1 wegen 4 fremder Alt-Worktree-Fehler (`skill-1433/1449/1505`, `duoplus-566`)“ in #136 behoben (`cli_befehle.py`: `aufraeumen --alles` meldet Einzelfehler auf stderr/Log, endet bei sauberem Rundgang mit Exit 0, belegt in `tests/test_temp_ordner_136.py`). | belegt |
| M15 | Kaltstart-Probe (L5) | Lauf 1 (21.11.2026 Bau-Server, frische Sonnet-Session): BESTANDEN, 0 verhindernde Lücken. Lauf 3 (25.11.2026 Bau-Server, frischer Prüfer-Subagent ohne Vorwissen nur mit `docs/WIEDEREINSTIEG.md` auf Stand `f3c85b1`): Ziel, Stand, offene Pull-Requests (659, 662, 250), nächste Schritte (624, 641, 68) und Einstieg auf frischer Maschine ohne privaten Tresor ohne Abweichung wiedergegeben, 0 Geheimnisse im Dokument; Detail-Hinweise (L10-Verweis, Glossar-Begriffe, Voraussetzungen) direkt eingearbeitet. Maschinen-Pfade Bau-Server (25.11.2026, #755): `~/.config/to-spawn/maschine.json` geprüft (`sicherung.dateien` auf Leit-Übergabe + Entscheidungs-Log, `sicherung.ziel` Vorgabe Notiz-Tresor `~/obsidian/to-spawn/sicherung/<Rechnername>/`, privat; `to_spawn.py sichern --pruefen` und `--trocken` Exit 0, aktive Sicherungen vorhanden). Maschinen-Pfade Windows-PC (25.11.2026, #756): `~/.config/to-spawn/maschine.json` angelegt (`rolle: "pc"`, Repo-Pfade, `sicherung.dateien` auf Ober-Aufsicht-Übergabe + Entscheidungs-Log, `sicherung.ziel` auf privaten Notiz-Tresor `~/<Notiz-Tresor>/to-spawn/sicherung/<Rechnername>/`, privat); `to_spawn.py sichern --pruefen` (mit und ohne `OBSIDIAN_VAULT`), `--trocken` sowie zwei echte Sicherungs-Läufe (idempotent: 68 Dateien / 0 Dateien, 0 Zweige) Exit 0, aktive Sicherungen vorhanden. Lauf 4 Windows-Kaltstart-Probe (25.11.2026 Windows-PC, #756, frischer Prüfer-Subagent ohne Vorwissen/Chat/Übergabe/Tresor nur mit `docs/WIEDEREINSTIEG.md` auf Stand `7005f9c`): BESTANDEN, 0 verhindernde Lücken, 0 Geheimnisse; Ziel, Fundorte, Stand, Prioritäten und Windows-Einstieg ohne Abweichung wiedergegeben; Detail-Hinweise (dauerhafter Klon-Pfad statt `$env:TEMP`, `$HOME` für gequotetes `~`, `Get-Content -Tail 200`, `skill_aktuell: weicht_ab`, `~`-Auflösung für CLI-Argumente in `to_spawn.py sichern`) direkt eingearbeitet. Lauf 5 Abschluss-Kaltstart-Probe „vor fertig“ (28.11.2026 Bau-Server, #755, frischer Prüfer-Subagent ohne Vorwissen/Chat/Übergabe/Tresor nur mit `docs/WIEDEREINSTIEG.md` auf Stand `4d1cd96`): BESTANDEN, 0 verhindernde Lücken, 0 Geheimnisse, 0 Personennamen/feste Benutzerpfade; Ziel, Fundorte, realer Stand (`4d1cd96`, offener PR 956, alle 10 offenen Issues, Abnahme-Stand M1–M15), nächste Schritte sowie Kaltstart auf frischer Linux-/Windows-Maschine ohne Abweichung wiedergegeben; Detail-Hinweise (Status-Formulierung `#755`/M15, geschlossene Sammel-Issues 49–53, `python3`-/Bash-Rückfall-Hinweis, Mitinstallation `~/.claude/review/`, Abgrenzung Eigentümer-Vorbehalte in Abschnitt 5) direkt eingearbeitet. `to_spawn.py sichern --pruefen` am 28.11.2026 Exit 0 (0 Frische-Hinweise, 2 Muster / 5 Quellen) und `TO_SPAWN_PRUEFE_STAND_FRISCHE=1 pytest tests/test_uebergabe_sichern_145.py` grün. | belegt |

## Probe-Lauf 3 (24./25.11.2026, Bau-Server)

Quelle: `~/systemcheck-1119/probe_leihliste_srv_3.md` (Server-Uhr). Probe-Inhalt auf Leihlisten-Niveau, zwei Varianten:
`-mit` (Repo `~/to-spawn-probe-3-mit`, Spec 1, Tickets 2–7, Abnahme 8) und `-ohne` (Repo `~/to-spawn-probe-3-ohne`, Spec 31,
Tickets 32–37, Abnahme 38). Installierter Skill war `5fb4af9` (nicht Skill-main `168176f`), die Fixes 616/619/620 waren
im Lauf also noch nicht wirksam. Messlatte (Leitziel des Nutzers, `LASTENHEFT.md` L10, A65–A69): bestanden = null System-Anomalien;
falsches Warten (Ticket wartet auf einen schon fertigen Vorgänger) zählt als Anomalie.

**Für M1 belegt**
- `-mit`: Start-Befehl 00:39:10, Tickets 2–7 zu bis 01:09:09, Bau fertig 01:39 (60 min ab Befehl). Keine Eingriffe der Leit-Session.
- `-ohne`: Start-Befehl 01:49:21, Bau fertig 03:00 (71 min, davon ca. 36 min System-Anomalien). Zwei Eingriffe der Leit-Session (02:50:45, 02:53:22).
- Wartezeiten durch Ticket-Abhängigkeit (2 → 3/4, 5 → 6 → 7, 36 zu → 38 arbeitet < 1 min) waren zulässig.
- Messlatte nicht bestanden: `-mit` 2 Anomalien, `-ohne` 3 Anomalien.

**Anomalien**
1. `-mit`, ca. 01:08–01:14 (Skill-Issue 624, Folge 460): `bau 21` (6 Wartezyklen à 60 s) und `bau 25` (4 Zyklen) warteten auf einen Platz.
   Gesamtgrenze 18 Claude-Sessions voll, weil fertige stille Fenster `bau 3/4/5` erst vom Cron-Aufpasser 01:15 geschlossen wurden; RAM war frei.
   Kein Eingriff, löste sich selbst (ca. 6 + 4 min Leerlauf).
2. `-ohne`-Start 01:44–01:46 gesperrt („schon 18 Claude-Sessions insgesamt“, RAM 5,9 GB frei), gleiche Ursache wie 1 (624), frei nach dem Cron-Aufpasser 01:45.
3. `-ohne`, Skill-Issue 641: `bau 35` startete nicht (`bau.py 35` 02:11:04 Exit 9, „WEIGERUNG (#499)“: `~/wt/wt-35` ist schon Worktree eines anderen Repos).
   Gleiche Ticket-Nummer in zwei Repos, Worktree-Ordner hängt nur an der Nummer. Der Aufseher heilte es selbst im Tick 02:24
   (`worktree_basis` lokal gesetzt, `bau 35` 02:26 neu gestartet). Verlust 02:11:04 → 02:26 = 15 min Stillstand.
4. `-ohne`, Skill-Issue 624 + Plätze: `bau 36` und `bau 37` warteten nach „35 zu“ (02:35:23) 16–19 min auf einen Platz, obwohl der Vorgänger fertig war;
   das fertige stille Fenster `bau 35` hielt selbst einen Platz. Eingriff 1 (02:50:45): `bau 35` per `/exit` beendet. Eingriff 2 (02:53:22):
   `speicher.max_sessions: 8` lokal gesetzt (Rechner-Grenze 12 / Gesamtgrenze 24, nicht committet). Fixes: 625 gemergt (nicht installiert), 650 (Plätze 12/24) in Prüfung, 641 offen.

**Abnahme durch den Nutzer, 25.11. 04:17** (gemessen von `leit 220`; Quelle: tmux `window_activity` im 10-s-Raster + `.to-spawn/wache_1.log`).
Kommentar „abgenommen“: Issue 8 (`-mit`) 04:17:44, Issue 38 (`-ohne`) 04:17:45.

| Lauf | Fenster | erste Regung nach Kommentar | Einordnung |
|---|---|---|---|
| `-mit` | `bau 8` | +24 s | in Ordnung |
| `-mit` | `wache 1` (Aufseher) | +45 s (Wecker-Log 04:18:29) | in Ordnung |
| `-ohne` | `wache 31` | +66 s | in Ordnung (≈ 1 min) |
| `-ohne` | `bau 38` | +66 s | Issue 38 war schon 04:13 zu (Aufseher-Annahme nach 70 min) |

- `-mit`: Aufseher trug 04:18:42 „Abnahme #8 bestätigt“ ein (PR 43) und wartete korrekt auf das Live-Ja des Nutzers (Kommentar im Ticket zählt dafür nicht).
  Live-Ja des Nutzers 04:22:14 (`entscheidung-8.json`, `von: nutzer`), Wecker an `bau 8` 04:22:35 (+21 s), Issue 8 geschlossen 04:23:36
  „abgenommen und live“ mit Live-Beleg (+82 s nach Ja).
- Auffällig: zweiter Wecker „Abnahme-Issue #8: neuer Kommentar des Menschen“ an `bau 8` erst 04:20:32 (+168 s). Die Regung im Fenster `bau 8` war schon bei +24 s;
  vermutlich doppelter Weckruf, keine Wartezeit. Offen: prüfen, ob der Wecker je Empfänger getaktet ist (ca. 2 min Abstand).
- Ergebnis für diesen Schritt: keine Wartezeit im Minuten-Takt, kein Eingriff nötig.

## Etappe 3b-5 (#105): M12 DuoPlus & Bremse bei 2 gleichzeitigen Gates (`-n 4`)

Stand: 2026-11-28 (Bau-Server Linux, 8 Kerne, 16 GiB RAM, 16 GiB Swap, Last durchgehend `< 12`, alle Läufe mit `nice -n 15`). Reproduzierbares Mess- und Auswerteskript: `messung/fehler_reihe_duoplus.py` (Tests: `tests/test_fehler_reihe_duoplus_105.py`).

### 1. M12 DuoPlus — ehrlicher Beleg nach 3b-0-Entscheidung (#98)

- **3b-0-Entscheidung (`docs/PRUEFTOR_VERGLEICH.md` 4.3, `cf2561a`):** Kriterium (b) `179/302 = 59,3 % >= 50 %` (Rückblick `30/50 = 60,0 %`) und Kriterium (c) `3` nicht von R1–R3 gefangene Fehlgriffe (`1ed5b52`, `61e877f`, `0cba2e3`) lösen den Wechsel auf **Variante 3 allein** für DuoPlus aus (schnellerer Voll-Lauf, keine Auswahl).
- **M12 DuoPlus:** **nicht anwendbar, weil keine Auswahl läuft** (Leit-Entscheidung 21.11. 21:05, `docs/PLAN_L1_usb_stick.md`, 0 eingebaute Fehler im App-Repo).
- **Pflicht-Nachweis M12 im Probe-Repo (#104, PR #955):** erfüllt (`26/26` unbekannte Fehler in `messung/fehler_reihe_2.py` bei eingefrorenen Regeln `regeln@fd7e49f`, `0` übersehen; Gegenprobe Auswahl roh `11/26` übersehen).

### 2. Bremse bei 2 gleichzeitigen Gates (je `-n 4`, gemessen statt Annahme `3,0×`)

Gemessen gegen zwei getrennte, isolierte Sparse-Kopien des DuoPlus-App-Klons (Stand `b5b23086c`, Quell-Klon unverändert, 60 gleichmäßig ausgewählte reale Testdateien, 2 abwechselnde Runden nach Aufwärmlauf, `nice -n 15`, Start-Last `< 12`) sowie als Gegenprobe gegen zwei isolierte Probe-Suiten (`24` Module / `288` Tests):

| Messreihe (`-n 4`, `nice -n 15`, 2 Runden) | Einzel-Gate Median | 2 Gates parallel (Gate-Mittel / Max-Wand) | **Bremse-Faktor** (Spitze) | RSS 1 Gate (je Worker) | Summe-RSS 2 Gates | RAM frei min / Swap-Delta | Last-Spitze (1 min) | Urteil Schwelle `<= 1,5×` |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| DuoPlus-Klon (60 Testdateien, `--dist worksteal`) | **126,93 s** (128,86 / 125,00) | **169,68 s** / 178,30 s (R1 176,72/178,30; R2 161,59/162,12) | **1,34×** (1,41×) | 1535,2 MiB (~383,8 MiB) | 2963,3 MiB | 6493 MiB / +3 MiB | 11,51 | **<= 1,5× (erfüllt)** |
| DuoPlus-Klon (60 Testdateien, `--dist loadfile`) | **122,18 s** (118,84 / 125,52) | **157,72 s** / 161,02 s (R1 151,65/157,47; R2 160,76/161,02) | **1,29×** (1,32×) | 1523,2 MiB (~380,8 MiB) | 2987,2 MiB | 6504 MiB / +0 MiB | 11,38 | **<= 1,5× (erfüllt)** |
| Probe-Suite (24 Module / 288 Tests, `--dist worksteal`) | **2,90 s** (2,90 / 2,89) | **3,19 s** / 3,36 s (R1 3,36/3,36; R2 3,02/3,02) | **1,10×** (1,16×) | 397,0 MiB (~99,2 MiB) | 793,4 MiB | 8309 MiB / +0 MiB | 9,33 | **<= 1,5× (erfüllt)** |

- **Ergebnis für 3b-6:** Die gemessene Bremse liegt auf dem 8-Kern-Bau-Server bei **`1,29×–1,34×`** (statt der früheren Annahme `3,0×`) und damit unter der Schwelle `<= 1,5×` für einen zweiten Gate-Platz. Die reale RAM-Spitze im App-Klon liegt bei **`~381–384 MiB` je Worker** (bestätigt `JE_WORKER_MIB = 512` in `to_spawn/pruef/ausfuehren.py` als konservativen Deckel).
- **Ehrliche Vorbehalte:** (1) 2 parallele Gates (`8` Worker gesamt) belegen rund `3,0 GiB` RSS und heben die 1-Minuten-Last allein bereits auf `10,7–11,5` — ein 2. Gate-Platz in 3b-6 braucht daher Last-/Speicher-Freigabe (keine gleichzeitige Kern-Überbuchung mit 3 aktiven Bau-Sessions, vgl. #290). (2) Im Sparse-Ausschnitt ohne `docs/` endeten Einzel- und Parallel-Läufe im App-Klon konsistent mit Exit `1` (einzelne Tests lesen Dateien aus `docs/`); beide Vergleichsläufe fuhren exakt denselben Testsatz. Ein voller 30-Minuten-Parallel-Lauf über alle `1.920` Testdateien wurde wegen Laufzeit/Lastgrenze nicht gestartet.

## Etappe 3b-6 (#106): Vorprüfung vor der Schlange, 2. Gate-Platz & M13 DuoPlus

Stand: 2026-11-28 (Bau-Server Linux, 8 Kerne, 16 GiB RAM, 16 GiB Swap, Last `0,79 < 12`). Umsetzung im Skill-Repo: `to_spawn/pruef/vorpruefung.py` (CLI: `to_spawn.py pruefen <basis> <ziel> --vorpruefung`), Tests: `tests/test_vorpruefung_schlange_106.py`, Mess- und Auswerteskript: `messung/m13_vorpruefung_messung.py`.

### 1. Umsetzung im Skill-Repo (Vorprüfung Stufe 0–3 + Gate-Platz-Schutz)

- **Vorprüfung (`to_spawn/pruef/vorpruefung.py`):** Führt vor Eintritt in die Schlange genau Stufe 0 (Cache), Stufe 1 (Sofort-Prüfung `ruff`/ESLint + `ast.parse`-Syntax-Netz bei fehlendem `ruff` + Beleg-Schlüssel-Sperre), Stufe 2 (`nur Doku`) und Stufe 3 (betroffene Tests) aus — **nie** Stufe 4 (voller Lauf). Fremde Stände (`ziel != HEAD`) werden in einem losgelösten Temp-Worktree geprüft und garantiert wieder abgeräumt. Rückfälle (R1–R3, LF, AB) übergeben sauber an das Gate (`weiter_an_gate=True`), statt voll zu laufen.
- **2. Gate-Platz (`GatePool` / `erlaubte_gate_plaetze`):** Nur aktiv, wenn die gemessene Bremse `<= 1,5×` vorliegt (in #105 mit `1,29×–1,34×` belegt) **und** `MemAvailable >= 4096 MiB`, 1-Minuten-Last `< 6,0` sowie höchstens `2` aktive Bau-Sessions laufen. Fällt eine Schutzbedingung weg, drosselt der Pool selbsttätig auf `1` Platz (bzw. `0` Plätze bei `< 2048 MiB` frei oder Last `>= 12,0`). Bündel bleiben auf höchstens `2` begrenzt, keine Spuren nach Ordner.
- **Rot-Beweis (`tests/test_vorpruefung_schlange_106.py`):** Ein Ticket mit Python-Syntax-Fehler wird in Stufe 1 abgewiesen (`Exit 1`, `urteil: "rot"`, `zugelassen: False`, `gate_platz_belegt: False`), sowohl mit `ruff` als auch ohne `ruff` im `PATH`, und belegt **0 Gate-Plätze** (auch bei bereits belegtem Platz 1 sofortige Abweisung ohne Wartezeit).

### 2. Reale 10-Ticket-Messung (isoliertes Probe-Repo, Last `< 12`)

Ausgeführt mit `python messung/m13_vorpruefung_messung.py` (10 gleichzeitige Tickets: 2× Syntax-Fehler, 2× Nur-Doku, 4× kleine Code-Änderung, 2× R3 `requirements.txt`, Bremse-Vorgabe `1,34× <= 1,5×` aus #105):

| Kennzahl (10 Tickets parallel, Probe-Repo) | Messwert | Schwelle M13 | Urteil |
|---|---:|---:|---|
| Rot-Beweis Syntax-Fehler abgewiesen ohne Gate-Platz | **2/2** (`0` Plätze, `1,37–1,38 s`) | abgewiesen ohne Platz | **erfüllt** |
| Nur-Doku ohne Gate-Belegung | **2/2** (`1,34–1,36 s`) | kein Test-/Gate-Lauf | **erfüllt** |
| Gate-Belegungen / Spitze parallel | `6` / **max. 2** | Deckel `2` | **erfüllt** |
| Push → Urteil Median / P90 / Max | **1,73 s** / **2,42 s** / 2,42 s | `< 120 s` (Probe) | **erfüllt** |
| Längste Wartezeit auf Gate-Platz | **0,311 s** | `<= 900 s` (15 min) | **erfüllt** |
| `MemAvailable min` / Swap-Delta / RAM-Riss | **8176 MiB** / **+0 MiB** / **nein** | kein RAM-Riss | **erfüllt** |
| 1-Minuten-Last vorher → Spitze | **0,79 → 0,79** | `< 12,0` | **erfüllt** |

### 3. M13 DuoPlus — Auswertung mit gemessener 3b-5-Bremse (`1,29×–1,34×`) und ehrlicher Stand

Reproduzierbare Fahrtenbuch-Auswertung (`messung/m13_vorpruefung_messung.py --schlange <schlange>`, gleicher 14-Tage-Zeitraum bis `2026-11-21 20:20`, `672` Tickets, `T_voll` Median `1838 s`, Rüstzeit `73 s`, Seed `2026`):

| Szenario (DuoPlus-Fahrtenbuch) | Push→Urteil Median (`<= 112 s`) | P90 (`<= 1213 s`) | Warte `> 15 min` | 10-Ticket-Probe `> 15 min` | Gate-Min | M13-Urteil |
|---|---:|---:|---:|---:|---:|---|
| Vorher [VZ] / S0 seriell Kontrolle [SIM] | 223 / 209 s | 2426 / 2611 s | 9,5 % | 99 % | 8714 | Ausgangslage |
| S0 + Vorprüfung `5 s` (Fangquote `50 %`) | 146 s | 2262 s | 7,5 % | 99 % | 6874 | nicht erreicht |
| 2 Gate-Plätze, Bremse `1,34×` (#105 `worksteal`) | 209 s | 2584 s | 2,7 % | 98 % | 9602 | nicht erreicht |
| 2 Gate-Plätze `1,34×` + Vorprüfung `5 s` (`50 %`) | 147 s | 2259 s | 2,0 % | 94 % | 7244 | nicht erreicht |
| 2 Gate-Plätze `1,34×` + Vorprüfung `5 s` (`80 %`) | **121 s** | **2063 s** | **1,6 %** | 86 % | 6029 | nicht erreicht |
| 2 Gate-Plätze `1,29×` (#105 `loadfile`) + V `5 s` (`80 %`) | **121 s** | **2063 s** | **1,6 %** | 84 % | 6006 | nicht erreicht |
| Sensitivität (unbelegt): 2 Plätze `1,34×` + V `5 s` (`80 %`) + Voll `−45 %` | 101 s | 1125 s | 0,0 % | 82 % | 3495 | **unbelegt** (10-Ticket weiter `> 15 min`) |

- **Ehrliches Urteil (Erreichbarkeit unbelegt):** Vorprüfung + 2. Gate-Platz bei gemessener Bremse `1,29×–1,34×` drücken die Wartezeit `> 15 min` im Normalbetrieb deutlich (`9,5 % → 1,6 %`) und sparen `~31 %` Gate-Minuten. Die M13-Ziele für DuoPlus (`Median <= 112 s`, `P90 <= 1213 s`, `10 Tickets gleichzeitig ohne Wartezeit > 15 min`) werden mit dem heutigen Voll-Lauf (`T_voll = 1838 s`) jedoch **nicht erreicht** und bleiben **unbelegt**.
- **Grenze Skill-Repo / App-Repo:** In diesem Paket wurde ausschließlich das Skill-Repo samt Messung gebaut. Die Einbindung in die App-Schlange (`scripts/deploy_schlange*`) erfolgt getrennt im App-Repo (Staging zuerst; App-Live bleibt ausdrückliche Entscheidung des Nutzers).

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
