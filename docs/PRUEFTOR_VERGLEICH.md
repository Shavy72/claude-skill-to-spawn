# Prüftor-Vergleich (Etappe 3a, L3)

Stand: 2026-11-21. Bezug: `docs/LASTENHEFT.md` L3 (A15–A21, N7–N9), `docs/PLAN_L1_usb_stick.md`
(Design „Prüf-Werkzeugkasten“, Etappe 3a/3b, Abnahme M10–M13). Issue #81.

Dieses Dokument baut nichts. Es vergleicht Ansätze, legt Messwerte vor und empfiehlt den Bau für 3b.

## 1. Zweck und Stand

- **Zweck:** A19 verlangt vor dem Bau mindestens zwei grundverschiedene Ansätze mit Quellen und
  eigenen Messwerten. M13 verlangt, dass dieses Dokument vorliegt.
- **Gemessen:** (a) Vorher-Zeiten der DuoPlus-Schlange aus dem Fahrtenbuch, (b) Schlangen-Strategien
  im Simulator am echten Fahrtenbuch (14 Tage), (c) Test-Auswahl mit 34 eingebauten Fehlern in zwei
  Probe-Repos (Python, Node), (d) Test-Auswahl am DuoPlus-Klon (Zwischenstand, 2 Rückblick-Läufe).
- **Maschine:** Bau-Server Linux, 8 Kerne. Last während der Messungen 10–13 (Probe) bzw. 11–19
  (DuoPlus); alle Läufe mit `nice`. Zeiten sind darum verrauscht, je Wert meist ein Lauf.
- **Windows:** nichts gemessen (siehe Abschnitt 5; Messung zugeordnet Paket 3b-2, Abschnitt 6.4).
- **Vorbehalt „0 von 34“:** Die Rückfall-Regeln R1–R4 sind aus genau den 34 eingebauten Fehlern
  abgeleitet, an denen danach „0 übersehen“ gemessen wurde [PROBE]. Der Wert zeigt nur: die Regeln
  decken die bekannten Fehlgriffe. Er ist **kein Nachweis für M12**; dafür verlangt 3b-2 eine zweite,
  vorher unbekannte Fehlerreihe (Abschnitt 6.4).
- **Ergänzt nach Gegenlesen (2026-11-21):** dritte Variante „schneller Voll-Lauf“ (6.2), Mess-Vorstufe
  3b-0 vor den Bau-Paketen (6.4), R1-Quote in DuoPlus als Lücke (5).

### Quellen (Kürzel in allen Tabellen)

Die Mess- und Recherche-Berichte liegen im Arbeitsordner der Ober-Aufsicht (`~/systemcheck-1119/`),
nicht im Repo. Die Skripte, die sie erzeugen, liegen unter `messung/` (Abschnitt 7).

| Kürzel | Datei | Inhalt |
|---|---|---|
| [VZ] | `vorher_zeiten_duoplus_2026-11-21.md` | Vorher-Werte aus `fahrtenbuch.jsonl` (`messung/vorher_zeiten.py`) |
| [SIM] | `prueftor_messung_schlange_2026-11-21.md` | Simulator Schlangen-Strategien (`messung/schlange_sim.py`) |
| [PROBE] | `prueftor_messung_probe_2026-11-21.md` | Probe-Repos, 34 eingebaute Fehler (`messung/fehler_reihe.py`) |
| [DP] | `prueftor_messung_duoplus_2026-11-21.md` | DuoPlus-Klon, Zwischenstand (`messung/rueckblick_auswahl_duoplus.py`) |
| [RA] | `prueftor_recherche_A_2026-11-21.md` | Recherche Test-Auswahl, Ausführung, Sicherheitsnetz |
| [RB] | `prueftor_recherche_B_2026-11-21.md` | Recherche Merge-Queue-Strategien, agentisches Prüfen, Wackel-Tests |

## 2. Vorher-Werte DuoPlus

Zeitraum letzte 14 Tage ab 2026-11-07 19:21, 1469 Fahrtenbuch-Zeilen, alle Zeiten in Sekunden [VZ].

| Größe | Anzahl | Median | P90 | Max | Quelle |
|---|---|---|---|---|---|
| Push bis Urteil (Näherung Committer-Zeit) | 613 | **223** | **2426** | 13722 | [VZ] b) |
| Push bis Staging (Näherung) | 483 | 547 | 13177 | 68427 | [VZ] c) |
| Gate-Zeit `ticket-gruen` | 364 | 154 | 1997 | 4225 | [VZ] a) |
| Gate-Zeit `ticket-rot` | 134 | 1782 | 2771 | 3676 | [VZ] a) |
| Gate-Zeit `ticket-gate-fehler` (Abbruchzeit) | 72 | 10 | 1306 | 3073 | [VZ] a) |
| Gate-Zeit grün, Gate-Art VOLL | 100 | 1839 | – | – | [SIM] Annahme 4 |

Anteile der 829 Urteile: ticket-rot 16 %, rot 1 %, ticket-gate-fehler 9 %, ticket-konflikt 9 % [VZ] d).
Letzte 3 Tage: Push bis Urteil Median 171 / P90 2207 [VZ].

Testumfang: `pytest --collect-only` zählt **32.325 Test-Items in 1.920 Dateien**, Sammeln allein 91,7 s
(Wand 118,6 s unter Last) [DP]. Langsamste Testdatei 381 s (`test_persona_pflicht_1043_weg.py`) [VZ] e).

**Widerspruch Testzahl:** [RA] nennt „~10.700 Tests“, [DP] zählt 32.325 Items. Ich folge [DP], weil
gemessen; der Unterschied ist vermutlich Parametrisierung (Items statt Testfunktionen, [DP] Hinweis).

**Widerspruch Push bis Urteil:** [VZ] 223 / 2426, [SIM] „Ist“ 249 / 2430. Beide aus demselben Fahrtenbuch,
aber [SIM] zählt je sha (672 Tickets, Zeitraum bis 20:20) und kappt die Push-Zeit auf den ersten
Gate-Start. Als Vorher-Wert für M13 gilt [VZ] (eigenes Skript für genau diese Größe); innerhalb des
Simulators wird gegen [SIM]-Ist verglichen.

## 3. Verglichene Ansätze

### 3.1 Test-Auswahl

| Ansatz | Was es ist | Quelle | Messwert (eigen) | Stärke | Schwäche | Windows |
|---|---|---|---|---|---|---|
| **Voll-Lauf** | alle Tests | – | Probe Py 122 Tests ~1–2 s, Node 61 Tests ~2,5–3,6 s [PROBE]; DuoPlus grün VOLL Median 1839 s [SIM] | findet per Definition alles, was die Suite findet | DuoPlus 30 min, bremst jede kleine Änderung (N8) | ja |
| **Heutiger Eigenbau** `gate_auswahl.py` + `gate_cache.py` | Namen + Import-Graph, Urteil SKIP/VOLL/AUSWAHL, Hüllen-Heuristik (Pfad-Literale, `mock.patch`-Strings) | [RA] Ist-Zustand | Auswahl 0,2–0,6 s je Lauf; ce8a95f: 944 Tests, beide roten Dateien drin; conftest-Änderung → VOLL [DP] | schnell, deckt Nicht-Python-Bezüge per Dateinamen ab [DP] | Eigenbau (A19/N7), repo-spezifisch (A20), ~951 Zeilen Heuristik [RA] | unbelegt |
| **A: statischer Import-Graph** `pytest-impacted` 0.38.1 (Py), `vitest --changed` 5.1.1 (Node) | git-Diff + AST-Import-Graph transitiv; conftest wirkt auf alles darunter; Voll-Trigger für Abhängigkeits-/Prüf-Konfig eingebaut | https://github.com/promptromp/pytest-impacted · https://vitest.dev/config/forcereruntriggers | Probe roh: Py 9 von 22, Node 3 von 12 übersehen; mit R1+R2 **0/22 und 0/12** [PROBE]. DuoPlus: 74–113 s je Auswahl, ce8a95f 129 Tests, **`test_gate_bash_240.py` verpasst** [DP] | keine Datenbank, kein Kaltstart, worktree-neutral, Doku → 0 Tests, Abhängigkeit/Konfig → voll [PROBE] | sieht keine Daten-/Template-/Konfig-Dateien, keine dynamischen Importe, keine `mock.patch`-Strings [PROBE]; nur EIN Namespace-Modul, DuoPlus mit `--module .` langsam, `.venv` im Repo macht es unbrauchbar (> 3 min) [DP] | Metadaten „OS Independent“, braucht git.exe [PROBE]; nicht gemessen |
| **B: Laufzeit-Abdeckung** `pytest-testmon` 2.3.1 | Coverage je Test, Fingerprint der ausgeführten Blöcke, SQLite-DB `.testmondata` | https://www.testmon.org/ · Pfad-Issues https://github.com/tarpas/pytest-testmon/issues/251, /266 | Probe roh: Py **7 von 22** übersehen; mit R1+R3+R4 0/22 [PROBE]. Wählt feiner (Blatt-Fehler 5 statt 10 Tests) [PROBE]. Kopierte DB im 2. Worktree ging [PROBE]. DuoPlus: nicht gemessen [DP] | fängt dynamische Importe und String-Patches [PROBE] | `pytest.ini`- und reine `requirements.txt`-Textänderung → 0 Tests (falsch für M11) [PROBE]; Code beim Import (Re-Export in `__init__`) unsichtbar [PROBE]; DB je Worktree; DuoPlus-DB seit 09.10. defekt [RA]; kein Node-Gegenstück [PROBE] | Classifier Windows [PROBE]; nicht gemessen |
| Vereinigung A ∪ Eigenbau | beide Auswahlen, Rückfall VOLL | [DP] Aussage | unbelegt (nur 2 Rückblick-Läufe) | fängt im einen roten Fall beide Dateien | zwei Systeme pflegen | – |

Recherchiert, nicht gemessen, verworfen [RA]: tach (Modulgrenzen je Repo pflegen, A20), snob (seit
01/2025 kein Release), pytest-picked (wählt nur geänderte Testdateien), Pants (Windows nur über WSL,
https://www.pantsbuild.org/stable/docs/getting-started/prerequisites), Bazel/Buck2 (Repo-Umbau),
Nx/Turborepo (nur Projekt-Ebene, Monorepos), Warm-Daemon (veralteter Zustand, N9).
Jest `--changedSince`: gleiche Klasse wie vitest, nicht gemessen [RA].

Veröffentlichte Zahlen zum Sicherheitsnetz [RA]: Meta führt < 1/3 der Tests aus und erkennt > 99,9 % der
fehlerhaften Änderungen, nimmt also < 100 % hin und hat einen Voll-Lauf dahinter
(https://arxiv.org/abs/1810.05286). Google TAP: nur 1,23 % der Testläufe finden einen Bruch
(https://eecs481.org/readings/googletest.pdf). Folgerung für M12 („0 übersehen“): Auswahl allein reicht
nicht, es braucht Rückfall-Regeln und einen Voll-Lauf als Netz.

### 3.2 Ausführung

| Ansatz | Was es ist | Quelle | Messwert (eigen) | Stärke | Schwäche | Windows |
|---|---|---|---|---|---|---|
| xdist `--dist loadfile` (heute) | eine Datei je Worker | [RA] | DuoPlus Gate n=8, 3 Teile `GATE_TEIL` [RA]; keine getrennte Messung | Fixtures je Datei einmal | eine lange Datei (381 s [VZ]) bremst den Worker | ja |
| xdist `--dist worksteal` | gleichmäßig verteilen, freie Worker klauen Arbeit | https://pytest-xdist.readthedocs.io/en/stable/distribution.html | Probe `-n 2`: gleiche Urteile 22/22, im Mini-Repo langsamer (Worker-Start dominiert) [PROBE]; DuoPlus **nicht gemessen** [DP] | ausgeglichen bei ungleichen Laufzeiten | Fixture-Kosten je Worker; conftest-Plätze/Neustart-Hooks in DuoPlus vorher prüfen [DP] | Classifier ja [PROBE] |
| `pytest-split` `least_duration` | Teile nach gemessener Laufzeit statt Dateizahl | https://jerry-git.github.io/pytest-split/api_docs/ | nicht gemessen | ersetzt Dateizahl-Teilung | Laufzeit-Datei pflegen | unbelegt |
| Ergebnis-Cache grob | Schlüssel = git-Tree-SHA + Hash Lock/Konfig → „Stand schon grün“ | Prinzip Bazel/Turborepo [RA] | nicht gemessen | einfach, sicher (gleicher Stand) | trifft nur bei identischem Stand | ja (git) |
| Ergebnis-Cache fein (heute `gate_cache.py`) | Inhalts-Hash je Testdatei + Hüllen-Heuristik | [RA] | nicht gemessen | trifft öfter | Eigenbau, Heuristik kann Bezüge übersehen | unbelegt |
| uv | schneller Installer/venv | „10–100x faster than pip“ (Herstellerangabe, https://github.com/astral-sh/uv/blob/main/README.md) | Probe-venv fehlerfrei installiert [PROBE]; Tempo nicht gemessen | Linux + Windows, ein Werkzeug | – | ja (Hersteller) |
| Sammelzeit senken | Dateiliste übergeben statt `--deselect`, `testpaths` | https://docs.pytest.org/en/stable/reference/reference.html#confval-testpaths | Sammeln DuoPlus 91,7 s [DP] | trifft jede Stufe | – | ja |

RAM-Spitze [PROBE]: Py seriell ≤ 76 MB, Py `-n 2` ≤ 199 MB, Node vitest ≤ 615 MB. DuoPlus nicht gemessen.

### 3.3 Schlange

Simulator am Fahrtenbuch, 14 Tage, 672 Tickets, Seed 2026; S0 bildet das Ist ausreichend nach
(Median −16 %, P90 +7 %) [SIM]. Werte in Sekunden, Auswahl der Zeilen; volle Tabelle in [SIM].

| Strategie | Muster aus | Quelle | Push→Urteil Median / P90 | Warte > 15 min | Gate-Min | M13-Probe Runden > 15 min | Stärke | Schwäche | Windows |
|---|---|---|---|---|---|---|---|---|---|
| Ist (Fahrtenbuch) | – | [SIM] | 249 / 2430 | 5,8 % | 8096 | – | – | – | – |
| S0 seriell (Kontrolle) | heute | [SIM] | 209 / 2611 | 9,5 % | 8714 | 99 % | einfach | Stau bei Spitzen | ja |
| S0 + Vorprüfung V 80 % | TAP presubmit, Mergify zweistufig | https://xgwang.me/google-ci/ · https://mergify.com/product/merge-queue | 236 / 2186 | 5,4 % | 5826 (−33 %) | 93 % | kleinster Umbau, spart Maschinenzeit | jeder wartet 2 min Vorprüfung; Fangquote unbelegt | ja |
| S0 + V 50 % | dto. | [SIM] | 261 / 2377 | 7,5 % | 6874 | 99 % | – | – | ja |
| S1 Batch max 4 + Bisect, Dauer A | bors, Mergify | https://github.com/cockroachdb/cockroach/issues/22499 · https://mergify.com/blog/dynamic-merge-queue-batch-size | 350 / 4701 | 17,9 % | 9244 | 98 % | ein Lauf für viele | rotes Mitglied (Rot ~10× länger als Grün) schlägt den ganzen Batch | ja |
| S1 Batch max 4, Dauer B (voller Lauf) | dto. | [SIM] | 1792 / 49921 | 38,2 % | 11705 | 100 % | – | unbrauchbar | ja |
| S1 max 4 A, 2 Slots, Bremse 1,5 | Mergify Slots | [SIM] | 195 / 2471 | 0,9 % | 9559 | 74 % | Stau fast weg | braucht Bremse ≤ 1,5 | ja |
| S2 spekulativ k=3, Bremse 1,0 | Zuul, Trunk | https://zuul-ci.org/docs/zuul/latest/gating.html · https://docs.trunk.io/merge-queue/optimizations/predictive-testing | 201 / 2472 | 0,6 % | 9228 | 93 % | längste Wartezeit 1901 s | Arbeit verfällt bei Rot; RAM k-fach | ja |
| S2 k=2, Bremse 3,0 | dto. | [SIM] | 1876 / 16882 | 38,1 % | 16430 | 98 % | – | kippt bei hoher Bremse | ja |
| S2 k=3, Bremse 1,5 + V 80 % | dto. + V | [SIM] | 236 / 2190 | 1,2 % | 6236 | 74 % | bestes Gesamtbild | zwei Umbauten | ja |
| S3 Spuren nach Ordner | Aviator, Trunk, Uber | https://docs.aviator.co/mergequeue/concepts/affected-targets | 192 / 2529 (ohne docs) | 7,6 % | 8714 | 98 % | – | 94 % benachbarter Paare teilen einen obersten Ordner | ja |
| Untergrenze unbegrenzt parallel | – | [SIM] | 190 / 2230 | 0 % | 8714 | 0 % | zeigt: P90 ist Gate-Laufzeit | nicht baubar | – |

Fertige Merge-Queue-Dienste (GitHub Merge Queue, Mergify, Aviator, Trunk) hängen an GitHub-PRs und
Hosted-CI; unsere Gates laufen lokal. Zuul ist selbst betreibbar, aber schwer (Nodepool, Zookeeper).
Übernommen werden darum nur dokumentierte Muster, kein Dienst [RB] Abschnitt 2.

**Kernbefunde Schlange [SIM]:** (1) P90 ist Gate-Laufzeit, nicht Warten — keine Strategie drückt P90
unter ~2200 s. (2) Batch + Bisect schadet bei unserem Rot-Anteil. (3) Zwei bis drei parallele Gates
drücken das Warten stark, aber nur, wenn die Bremse ≤ 1,5 bleibt. (4) Spuren nach Ordner bringen fast
nichts. (5) M13 „10 Tickets, keiner > 15 min“ erreicht keine Strategie (bestes 74 % der Ziehungen mit
einem Ticket > 15 min).

### 3.4 Wackel-Tests (nur Recherche)

| Ansatz | Quelle | Zahl |
|---|---|---|
| Wiederholung bis 3×, Quarantäne über Schwelle | https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html | 1,5 % aller Läufe wackeln, 84 % der Grün→Rot-Wechsel durch Wackler [RB] |
| 3 gezielte Wiederholungen (gleicher Prozess / später / anderer Host) | https://github.blog/engineering/reducing-flaky-builds-by-18x/ | erkennt 90 % der Wackler (vorher 25 %), Wackel-Builds 18× weniger [RB] |
| Quarantäne: Test läuft weiter, blockiert nicht | https://docs.trunk.io/merge-queue/optimizations/anti-flake-protection | Ziel Wackelrate < 2 % [RB] |

Eigener Wackel-Anteil im DuoPlus-Rot: **unbelegt** (Simulator behandelt jedes Ergebnis als fest [SIM]).

## 4. Messwerte je Messung

### 4.1 Probe-Repos, Fälle M11 (Tests gelaufen · Zeit) [PROBE]

| Fall | Py VOLL | Py A impacted | Py B testmon | Node VOLL | Node A vitest |
|---|---|---|---|---|---|
| nur Doku | 122 · 1,9 s | **0** · 3,1 s ✔ | **0** · 2,2 s ✔ | 61 · 2,9 s | **0** · 1,1 s ✔ |
| Blatt-Modul | 122 · 2,1 s | 10 · 3,1 s ✔ | 5 · 1,3 s ✔ | 61 · 2,5 s | 21 · 2,0 s ✔ |
| Abhängigkeit | 122 · 2,2 s | 122 · 3,6 s ✔ | **0** · 1,4 s ✘ | 61 · 2,5 s | 61 · 2,8 s ✔ |
| Prüf-Konfig | 122 · 1,5 s | 122 · 2,5 s ✔ | **0** · 1,6 s ✘ | 61 · 3,6 s | 61 · 3,0 s ✔ |

Alle Urteile < 5 s, M13-Teilziel „kleine Änderung im Probe-Repo unter 2 min“ erfüllt [PROBE].
impacted ist im Mini-Repo langsamer als der Voll-Lauf (~1 s Analyse) [PROBE].

### 4.2 Probe-Repos, eingebaute Fehler M12 [PROBE]

| Repo | eingebaut | A roh übersehen | B roh übersehen | A + Regeln | B + Regeln |
|---|---|---|---|---|---|
| Python | 22 | **9** (Daten-JSON 2, Template 2, `.toml` 1, dyn. Import 2, `mock.patch`-String 2) | **7** (Daten-JSON 2, Template 2, `.toml` 1, `pytest.ini` 1, Re-Export `__init__` 1) | **0** (R1+R2) | **0** (R1+R3+R4) |
| Node | 12 | **3** (fs-gelesene JSON 1, dyn. `import()` 2) | kein Werkzeug | **0** (R1+R2) | – |
| Python `-n 2 --dist worksteal` | 22 | gleiche Urteile | gleiche Urteile | 0 | 0 |

Rückfall-Regeln (Vorschlag aus [PROBE]):
- **R1** Nicht-Code-Datei geändert (außer reiner Doku `docs/**`, `*.md`, Bilder) → voll.
- **R2** Code-Datei geändert, Auswahl wählt 0 Tests → voll („nicht bestimmbar“).
- **R3** Prüf-Konfig/Abhängigkeiten → voll (bei impacted und vitest eingebaut, bei testmon nötig).
- **R4** (nur testmon) Paket-`__init__.py`/Modul-Kopf geändert → voll.

Einschränkung: R1/R2 sind im Skript nachgerechnet, nicht als Werkzeug-Schalter gefahren [PROBE].

**Überanpassung:** R1–R4 sind nachträglich aus genau diesen übersehenen Fällen abgeleitet ([PROBE]
„Regeln, die die Messung braucht“) und an denselben Fällen gemessen. „0 von 34“ ist darum eine Messung
an den eigenen Übungsfällen: Nachweis „Regeln decken die bekannten Fehlgriffe“, **kein Nachweis für
M12**. R2 greift zudem nur, weil die Plugin-Module im Probe-Repo keinen statischen Importeur haben
(Bauart der Fälle); ein dynamischer Import neben einem statischen Nachbar-Import ist nicht geprüft.

### 4.3 DuoPlus-Klon (Zwischenstand, master `b5b23086c`) [DP]

| Messung | Wert |
|---|---|
| Sammeln ganze Suite | 32.325 Items, 1.920 Dateien, 91,7 s (Wand 118,6 s) |
| Auswahl Eigenbau `gate_auswahl.py` | 0,2–0,6 s je Lauf |
| Auswahl `impacted-tests --module .` | 74–113 s je Lauf (Graph über 3.907 `.py` inkl. `archive/` jedes Mal neu) |
| b5b2308 (conftest geändert) | Eigenbau VOLL 32.325 · impacted alle 32.325 |
| ce8a95f (ticket-rot, 7 Dateien, 2 rote Testdateien) | Eigenbau 944 Tests, beide roten drin · impacted 129 Tests, **`test_gate_bash_240.py` verpasst** |

Eingebaute Fehler in DuoPlus: **0** (vorbereitet, nicht gestartet). Rückblick: 2 von geplant 55 Läufen.
Der verpasste Fall ist eine Testdatei, die ein Shell-Skript als Text prüft (kein Python-Import) [DP].
**R1 hätte ihn gefangen (belegt):** `git show --stat ce8a95f` im Schlangen-Klon zeigt
`scripts/safe_deploy_vps.sh` (99 Zeilen geändert) neben `scripts/gate_nacht_voll.py` und zwei Testdateien.
Das Shell-Skript ist eine Nicht-Code-Datei im Sinn von R1 → voll. Gefangen ist der Fehlgriff damit aber
nur per Voll-Lauf, nicht durch eine Auswahl; der Fall zeigt auch, dass R1 in DuoPlus oft auf „voll“
fällt (Quote siehe Abschnitt 5).

### 4.4 Schlange

Siehe 3.3; Annahmen des Simulators (Rüstzeit gemessen 73 s, Bremse als Faktor, Vorprüfung 120 s mit
zufälliger Fangquote) in [SIM] „Annahmen“.

## 5. Lücken — was nicht gemessen ist

- **Windows:** keine Messung, nur PyPI-/npm-Metadaten [PROBE]. Offen: Pfadtrenner in R1-Globs, git.exe
  für impacted, Defender-Bremse für venv/SQLite [RA]. Zugeordnet: Paket 3b-2 (Abnahme M10/M11 am PC,
  Befehl in 6.4); M10 gilt erst danach voll.
- **R1-Quote in DuoPlus:** wie oft R1 auf „voll“ springt, ist nicht nachgemessen. Schnellzählung des
  Gegenlesers (nicht nachgemessen): 337 von 2088 Commits seit 07.11. = 16 % ändern eine Nicht-Code-,
  Nicht-Doku-Datei; je Ticket (mehrere Commits) liegt der Anteil höher. Folge: mindestens dieser Anteil
  läuft voll, der Tempo-Gewinn der Auswahl (M13, A16) gilt nur für den Rest. Messung in 3b-0.
- **Überanpassung der Regeln:** R1–R4 nur an den Fällen geprüft, aus denen sie abgeleitet sind (4.2).
  Zweite, unbekannte Fehlerreihe in 3b-2.
- **RAM-Spitze von impacted in DuoPlus:** nicht gemessen (nur Dauer 74–113 s [DP]).
- **DuoPlus M12:** keine eingebauten Fehler, nur 2 Rückblick-Läufe; testmon, worksteal, Voll-Läufe nicht
  gestartet (Last 11–19) [DP]. Ein echter Fehlgriff von impacted belegt [DP].
- **Tempo-Gewinn der Auswahl in DuoPlus:** unbelegt. Mini-Repo zeigt keinen Gewinn (Tests zu billig)
  [PROBE]; impacted kostet in DuoPlus 74–113 s allein für die Auswahl [DP]. Knoten-Module (`web/app.py`
  → 267 Testdateien) machen die Auswahl groß [RA].
- **Bremse bei parallelen Gates:** wichtigste offene Zahl. Faktor 3 nur aus einer Beobachtung
  („13 min ohne Last → 40 min bei Last 14–19“) [SIM] Annahme 6, nicht gemessen.
- **Bündel-Laufzeit:** keine eigene Fahrtenbuch-Zeile für den Bündel-Lauf, nur Spanne A/B [SIM].
- **Staging-/Live-/Nachtrag-Schritte:** nur Zeitpunkt, keine Dauer [SIM]; Push bis Staging darum nur Näherung [VZ].
- **Push-Zeit:** Committer-Zeit, nicht echter Push [VZ].
- **Vorprüfung:** Fangquote 50/80 % und Dauer 120 s angenommen, nicht gemessen [SIM].
- **Wackel-Anteil:** nicht gemessen [SIM].
- **Fehler-Einbau:** handgebaut (34), keine Zufalls-Mutanten (mutmut/Stryker) [PROBE].
- **testmon in DuoPlus:** Ursache des DB-Defekts seit 09.10. nicht untersucht [RA]; Dauerbetrieb mit
  10 Worktrees unbelegt [RA][PROBE].
- **Tempo-Zahlen der Werkzeuge** (testmon, impacted, vitest-Auswahl): keine Primärquelle [RA].

## 6. Empfehlung für 3b

### 6.1 Was die Messung festlegt (gilt für alle Varianten)

- Keine Auswahl allein besteht M12; jede braucht Rückfall-Regeln und den Voll-Lauf als Netz [PROBE][RA].
- Für die Schlange zählt zuerst die Gate-Dauer; Schlangen-Umbau allein erreicht M13 nicht [SIM].
- Batch ausbauen und Spuren nach Ordner: nicht bauen [SIM].

### 6.2 Drei Design-Varianten

Variante 1 und 2 unterscheiden sich nur im Auswahl-Kasten (grundverschieden sind die Auswahl-Ansätze
A und B, nicht die Designs). Variante 3 ist das echte Gegen-Design: keine Auswahl.

Alle haben dieselbe Tür: `to_spawn.py pruefen <basis> <ziel>` → Exit 0 grün, 1 rot, 2 Gate-Fehler
(trennt die 9 % Gate-Fehler vom echten Rot, Vorschlag [RB]) plus eine Prüf-Protokoll-Zeile (JSON).
Der Aufrufer sagt nur „von wo bis wo“.

**Variante 1 — zustandslos: statischer Graph + Regeln**

Kästen:
- **Erkennen** — versteckt: Sprache, Test-Werkzeug, Paket-Wurzeln. Adapter Python (pytest) und Node (vitest/jest).
- **Leiter** — versteckt: Stufen-Reihenfolge und Auslöser. Stufen: 0 Cache-Treffer (Tree-SHA + Hash
  Lock/Konfig) · 1 Sofort-Prüfung geänderter Dateien (ruff/ESLint) · 2 nur Doku → Rest ausgelassen ·
  3 betroffene Tests über `pytest-impacted` bzw. `vitest --changed` plus `--lf` · 4 voll bei R1–R3,
  conftest, „nicht bestimmbar“, Sammelpunkt vor Staging · 5 Weg-Test nur bei berührtem Nutzer-Ablauf.
- **Ausführen** — versteckt: xdist `worksteal`, Worker-Zahl unter RAM-Deckel, Dateiliste statt `--deselect`.
- **Wackler** — versteckt: einmal wiederholen nur die roten Tests (Plan sagt einmal; GitHub-Beleg nennt
  3 gezielte Wiederholungen für 90 % Erkennung [RB]), Ergebnis `wackler` getrennt melden, Liste mit Datum;
  Quarantäne-Tests laufen weiter, zählen nicht fürs Urteil [RB].
- **Protokoll** — versteckt: Format. Felder nach [RB]/[RA]: `basis, ziel, urteil, stufen[{name, lief|ausgelassen,
  grund, dauer_s, befehl}], auswahl{n, gesamt, sicher}, cache_treffer, fehler_kurz (≤ 20 Zeilen), wackler, gesamt_s, werkzeug@version`.
- Repo liefert nur Zusätze in seiner Konfig: Risiko-Pfade für Stufe 4 (z. B. `--impacted-invalidate-all`-Globs) und Zusatzbefehle (Plan).

Stärke: keine Datenbank, kein Kaltstart, jeder Worktree sofort bereit (A15/M10); beide Sprachen;
bewährte Werkzeuge (A19/N7). Schwäche: gröbere Auswahl; in DuoPlus 74–113 s Auswahlzeit [DP] →
Auswahl dort erst sinnvoll, wenn `archive/`/`.venv` aus dem Graph fallen (Pfad-Ausschluss) — unbelegt, ob das reicht.

**Variante 2 — zustandsbehaftet: Laufzeit-Karte als Kern**

Kästen wie Variante 1, aber **Auswahl** = `pytest-testmon` mit einer **Karten-Pflege** als eigenem Kasten
(Seed-Lauf auf master nach jedem Merge bzw. nachts, geschlossene DB in jeden Worktree kopieren,
Neuaufbau bei Paketwechsel). Statischer Graph nur als Zusatz für R4. Node fällt auf `vitest --changed` zurück.

Stärke: feinste Auswahl (Blatt-Fehler 5 statt 10 Tests [PROBE]), fängt dynamische Importe und String-Patches.
Schwäche: Zustand je Worktree (10 Kopien, Pfad-Issues #251/#266 [RA]), Seed-Läufe kosten Maschinenzeit,
DuoPlus-DB war schon einmal wochenlang defekt [RA], R1+R3+R4 nötig, zwei Auswahl-Mechaniken (Py/Node).

**Variante 3 — keine Auswahl: Voll-Lauf schneller machen**

Kästen: Erkennen und Protokoll wie Variante 1. **Leiter** nur mit Stufe 0 Cache (Tree-SHA + Hash
Lock/Konfig), 1 Sofort-Prüfung, 2 nur Doku → ausgelassen, 4 voll; keine Stufe 3. **Ausführen** ist der
Kern: xdist `worksteal` statt `loadfile`, Teile nach Laufzeit (`pytest-split least_duration`) statt
Dateizahl, Sammelzeit senken (Dateiliste, `testpaths`/`norecursedirs`), langsamste Testdateien zuerst
bzw. aufteilen, Rüstzeit je Gate-Lauf senken. **Wackler** mit Quarantäne wie Variante 1.

Vorhandene Messwerte für den Hebel:
- Voll-Lauf heute: grün VOLL Median 1839 s [SIM] Annahme 4; Gate grün P90 1997 s [VZ] a).
- Rüstzeit je Gate-Lauf 73 s (Median, n = 39) [SIM] „Annahmen“; Sammeln 91,7 s [DP] — zusammen ein
  fester Sockel von rund 165 s je Lauf (Summe, nicht als Ganzes gemessen).
- Langsamste Testdatei 381 s [VZ] e): unter `loadfile` hält sie einen Worker allein so lange fest [RA].
- Teures Setup: ~2,2 Worker-s je Test [RA] (aus `befund_gate.md`, nicht nachgemessen).
- Wackler: bei Google 84 % der Grün→Rot-Wechsel [RB]; eigener Anteil am DuoPlus-Rot (16 % [VZ] d)) unbelegt.
- **Nicht gemessen:** worksteal gegen loadfile in DuoPlus [DP], pytest-split, Treffer-Quote des groben
  Caches, Wirkung von Pfad-Ausschlüssen auf die Sammelzeit.

Stärke: kein Fehlgriff möglich, M12/N9 per Definition; kein Zustand außer dem Cache; jeder Gewinn wirkt
auch auf alle Voll-Läufe der anderen Varianten (R1–R3, conftest, Sammelpunkt vor Staging; R1 allein
≥ 16 % der Commits, Schnellzählung, Abschnitt 5); kein Eigenbau einer Auswahl (N7).
Schwäche: verletzt M11 Fall 2 („kleine Code-Änderung → nur betroffene Tests“) — allein nicht
abnahmefähig; Tempo-Gewinn unbelegt. Rechnung (nicht gemessen): weil P90 die Gate-Laufzeit ist [SIM],
verlangt M13 P90 ≤ 1213 s grob einen Voll-Lauf ≤ 1213 s, also mindestens 34 % kürzer als 1839 s.

**Vergleich**

| Frage | Variante 1 | Variante 2 | Variante 3 |
|---|---|---|---|
| Tür | gleich schmal | gleich schmal | gleich schmal |
| Zustand zu pflegen | keiner | DB je Worktree + Seed-Lauf | nur Cache (optional) |
| M10 „sofort bereit“ | ja (kein Kaltstart) [PROBE] | erst nach Seed-Lauf | ja |
| M11 drei Fälle | ja [PROBE] | Konfig-Fälle nur mit R3 [PROBE] | **nein** (Fall 2) |
| M12 Probe | 0/34 mit R1+R2 [PROBE], nur an den Übungsfällen (4.2), kein M12-Nachweis | 0/22 Py mit R1+R3+R4, Node über V1 [PROBE], ebenso | per Definition 0 |
| Auswahl-Feinheit | gröber | feiner | keine |
| Tempo DuoPlus | unbelegt; Auswahl allein 74–113 s [DP] | nicht gemessen | unbelegt; heute 1839 s voll [SIM] |
| Wirkt auf Voll-Läufe | nein | nein | ja |
| Windows | Metadaten ok, ungemessen | Metadaten ok, ungemessen; SQLite + Defender Risiko [RA] | xdist ja [PROBE], Rest ungemessen |
| Änderung an einer Regel | ein Kasten (Leiter) | Leiter + Karten-Pflege | ein Kasten (Leiter) |

**Empfehlung: Kombination aus Variante 1 und 3, Variante 2 nicht bauen.** Variante 1 ist nötig, weil
nur sie M11 ohne Zustand und Kaltstart erfüllt [PROBE]; Variante 3 allein fällt an M11 Fall 2 durch.
Der Ausführen-Kasten von Variante 3 kommt dazu, weil die Daten den Hebel dort zeigen: P90 ist
Gate-Laufzeit [SIM], R1 schickt mindestens rund 16 % der Commits voll (Schnellzählung), und die
Auswahl kostet in DuoPlus heute allein 74–113 s [DP] — beides ist Voll-Lauf-Zeit oder Sockel, den
nur Variante 3 senkt. Ob die Auswahl in DuoPlus überhaupt lohnt, entscheidet die Mess-Vorstufe 3b-0
mit festem Abbruch-Kriterium; reißt es, läuft DuoPlus nur mit Variante 3 und der Eigenbau
`gate_auswahl.py` bleibt bis Etappe 5 die Ausnahme (`befehle.pruefen`). testmon bleibt eine spätere,
optionale Verfeinerung hinter derselben Tür.

### 6.3 Schlange (nach dem Werkzeugkasten, nicht Teil der Tür)

Reihenfolge nach [SIM]: (1) Vorprüfung = `pruefen` Stufe 1–3 vor Eintritt in die Schlange;
(2) Bremse bei 2 gleichzeitigen Gates messen (je Gate `-n 4`), nur bei Bremse ≤ 1,5 zweiten Platz bauen;
(3) Bündel nicht ausbauen, eher max 1–2; (4) keine Spuren nach Ordner.

### 6.4 Bau-Pakete 3b und ihre Abnahme

| Paket | Inhalt | Abnahme an |
|---|---|---|
| 3b-0 | **Mess-Vorstufe, kein Bau** (billig, vor allem anderen): Rückblick 55 Läufe mit `impacted` und Pfad-Ausschluss `archive/`/`.venv`; Auswahl-Dauer und RAM-Spitze; R1-Quote je Ticket aus dem Fahrtenbuch (nachgemessen statt Schnellzählung); einmal voll `worksteal` gegen `loadfile` (nur bei Last ≤ 12) | Zahlen in Abschnitt 4.3/5 nachgetragen, Entscheidung nach Abbruch-Kriterium (unten) protokolliert |
| 3b-1 | Tür `pruefen`, Erkennen (Py/Node), Protokoll, Stufen 1 und 4 (voll), Exit 0/1/2 | **M10** Linux: beide frischen Probe-Repos liefern Urteil + Protokoll ohne Vorarbeit |
| 3b-2 | Stufen 2 und 3 (impacted / vitest `--changed`) + R1–R3, Grund im Protokoll; R1-Globs mit Windows-Pfaden (`\` → `/` vor dem Abgleich) + Prüf-Test mit `\`-Pfaden | **M11** (drei Fälle) + **M12** Probe: (a) alte Reihe 34 bleibt grün (Rückfall-Schutz, kein Nachweis); (b) **zweite, vorher unbekannte Fehlerreihe ≥ 20 Fehler**, erzeugt von einem frischen Helfer, der R1–R4 nicht kennt; Regeln vor der Messung eingefroren (Commit-SHA im Protokoll); übersieht die Auswahl einen → Regel ergänzen **und** erneut mit wieder neuer Reihe messen. **M10/M11 Windows** am PC durch die Ober-Aufsicht (Befehl unten); M10 gilt erst danach voll |
| 3b-3 | Ausführen (Variante 3): Stufe 0 Cache (Tree-SHA + Lock/Konfig), worksteal unter RAM-Deckel, `pytest-split` nach Laufzeit, Dateiliste statt deselect, `--lf` zuerst | **M13** Probe: kleine Änderung < 2 min (heute schon < 5 s [PROBE]) und zweiter Lauf desselben Stands = Cache-Treffer; Messpunkt DuoPlus: Voll-Lauf vorher/nachher am selben Stand |
| 3b-4 | Wackler: 1 Wiederholung der roten Tests, Ergebnis `wackler`, Liste | eingebauter Wackel-Test im Probe-Repo erscheint als `wackler`, nie still grün (Vorschlag, keine M-Zeile) |
| 3b-5 | DuoPlus: ≥ 20 eingebaute Fehler (Ziele in [DP] vorbereitet; Regel wie 3b-2 b: frischer Helfer, Regeln eingefroren, bei Fehlgriff neue Reihe), `pruefen` gegen Eigenbau gegen voll; Bremse 2 Gates messen | **M12** DuoPlus (0 übersehen) |
| 3b-6 | Vorprüfung vor der Schlange, ggf. zweiter Gate-Platz | **M13** DuoPlus: Median ≤ 112 s und P90 ≤ 1213 s (Hälfte von [VZ] 223/2426), 10 Tickets gleichzeitig ohne RAM-Riss und ohne Wartezeit > 15 min |

**Abbruch-Kriterium 3b-0** (Schwellen gesetzt aus M13 „mindestens halbiert“, nicht gemessen): Für
DuoPlus wird auf Variante 3 allein gewechselt (Auswahl dort aus, Eigenbau bleibt Ausnahme), wenn eines gilt:
(a) im Median kosten Auswahl + Lauf der Auswahl ≥ 50 % des Voll-Laufs am selben Stand;
(b) R1–R3 schicken ≥ 50 % der Tickets voll;
(c) der Rückblick zeigt einen Fehlgriff, den R1–R3 nicht fangen.
Sonst Kombination wie empfohlen. Wird gewechselt, gilt M12 DuoPlus nur formal (kein Auswahl-Fehlgriff
möglich) — das ist eine Plan-Frage an die Leit-Session, keine stille Abnahme.

**Befehl Windows-PC (3b-2, Ober-Aufsicht, PowerShell):** gilt erst, wenn 3b-2 gebaut ist;
`gen_py.py`/`gen_node.py` brauchen nur die Standardbibliothek.

```
git clone https://github.com/Shavy72/claude-skill-to-spawn C:/dev/wt-pruef-win
python C:/dev/wt-pruef-win/messung/gen_py.py --ziel C:/dev/probe-win
python C:/dev/wt-pruef-win/messung/gen_node.py --ziel C:/dev/probe-win
cd C:/dev/probe-win/py; git init -q; git add -A; git commit -qm base; git tag base
python C:/dev/wt-pruef-win/to_spawn.py pruefen base HEAD
```

Danach je ein Doku-, ein Code- und ein `requirements.txt`-Commit, je `pruefen base HEAD`; dasselbe in
`C:/dev/probe-win/node` (vorher `npm install`). Abnahme: Urteil + Protokoll-Zeile je Fall mit Grund.

Ehrlicher Ausblick zu 3b-6: Laut [SIM] erreicht keine Schlangen-Strategie allein M13; die Hälfte von P90
geht nur über kürzere Gates (3b-2/3b-3 in DuoPlus) und weniger Rote. Ob das reicht, ist **unbelegt**.

## 7. Messung wiederholen

Skripte unter `messung/`, Aufruf und Voraussetzungen in `messung/README.md`. Kurzform (Platzhalter
`<schlange>` = Ordner mit `fahrtenbuch.jsonl`, `klon/`, `laeufe/`; `<probe>` = Ordner der Probe-Repos):

```
python3 messung/vorher_zeiten.py --schlange <schlange> > vorher.md
python3 messung/schlange_sim.py --schlange <schlange> --bis 2026-11-21T20:20:00+01:00 > sim.md
python3 messung/gen_py.py --ziel <probe> && python3 messung/gen_node.py --ziel <probe>
# danach je Repo git init + Tag base, venv + npm install (messung/README.md)
<probe>/.venv/bin/python messung/fehler_reihe.py --ort <probe> faelle   # zuerst, legt seed_testmon/ an
<probe>/.venv/bin/python messung/fehler_reihe.py --ort <probe> py
<probe>/.venv/bin/python messung/fehler_reihe.py --ort <probe> py -n 2 --dist worksteal
<probe>/.venv/bin/python messung/fehler_reihe.py --ort <probe> node
nice -n 15 python3 messung/rueckblick_auswahl_duoplus.py --schlange <schlange> --repo <klon> --venv <venv> \
  --collect collect.txt --aus rueckblick.jsonl --merges 40 --rot 15 --max-s 1020
```

`schlange_sim.py` liefert bei gleichem `--bis` und Seed byte-gleiche Ausgabe [SIM]. Alle Läufe mit
`nice`, bei Last > 12 keine Voll-Läufe.
