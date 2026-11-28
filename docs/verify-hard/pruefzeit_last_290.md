# Prüfzeit unter Last auf dem Linux-Bau-Server — Messreihe und Befund (#290, L7-Vorstufe)

- **Bezug:** Issue #290 (`system:linux`, nur Bau-Server; Windows-Zweige nicht angefasst), Lastenheft L7 (`A42` „Erst messen“, `N17` „Geräte-Logbuch bleibt lokal“).
- **Messdatum:** 2026-11-28 (Bau-Server Linux, 8 vCPUs AMD EPYC 9645, 15.996 MiB RAM, 16.383 MiB Swap, Python 3.13.5, pytest 9.1.1, pytest-xdist 3.8.0).
- **Messskript:** `messung/pruefzeit_last_290.py` (reproduzierbar, Abbruch-Grenze 1-Minuten-Last `< 12,0`, keine fremden Prozesse angefasst, kein App-Live berührt).
- **Datenschutz & L7 N17:** Keine Personennamen, keine Benutzerpfade, keine Geheimnisse; die rohe JSON-Messdatei (Geräte-Logbuch) bleibt lokal und wird nicht ins öffentliche Repo committet.

---

## 1. Kontrollierte Messreihe auf dem Bau-Server (Abnahme 1)

### Messaufbau und ehrlicher Hintergrund

- **Prüflauf:** Isolierte, reproduzierbare pytest-Suite (`24` Module, `288` Tests inkl. `2` langsamer Nachzügler-Module mit 4-facher Arbeitslast und `48 MiB` Modul-Working-Set je Worker, um SQLite-/Fixture-Speicher und schiefe Dateilaufzeiten abzubilden).
- **Nachbildung gleichzeitiger Bau-Sessions (`S = 0, 1, 2, 3`):**
  Aus einer Live-Lesung (`/proc/<pid>/status`) von 10 realen Claude-Sessions auf diesem Bau-Server ergab sich je Session ein typischer Bedarf von `255–521 MiB` RSS plus `0–157 MiB` Swap (`VmHWM` Spitze `602–767 MiB`) zuzüglich `~55 MiB` für den `context-mode`-Node-Helfer. Jede kontrollierte Bau-Session belegte daher `450 MiB` aktiv berührten Arbeitsspeicher (`~469 MiB` gemessene Prozess-RSS) und erzeugte reale Python-/AST-/JSON-/Temp-I/O-Arbeit (~1 aktiver Kern).
- **Grundlast der Maschine:** Auf dem Bau-Server liefen während der Messung reguläre Hintergrunddienste und wartende/parallele Sitzungen (`MemAvailable` zu Beginn `5.661 MiB`, `SwapUsed` `8.977 MiB`, 1-Minuten-Last `7,58–10,44`, durchgehend `< 12,0`). Fremde Prozesse wurden nicht angefasst; die Grundlast erklärt leichtes Rauschen bei `S = 0` (`W = 1..4`). Alle 24 Läufe endeten mit Exit `0` ohne Absturz oder OOM.

### Ergebnis-Tabelle (Dauer und Speicher-Spitze je Bau-Sessions × Arbeiter-Zahl)

| Bau-Sessions | Arbeiter | Verteilung | Dauer (s) | Faktor vs. 0 Sess. | Prüf-RSS Spitze (MiB) | Bau-RSS (MiB) | RAM frei min (MiB) | Swap vorher→Spitze (MiB) | Last vorher→Spitze | Exit |
|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 1 | `seriell` | 8,25 | 1,00× | 88,6 | 0,0 | 5637 | 8977→8977 (+0) | 8,01→8,01 | 0 |
| 0 | 2 | `loadfile` | 5,84 | 1,00× | 216,8 | 0,0 | 5586 | 8977→8977 (+0) | 7,63→7,63 | 0 |
| 0 | 4 | `loadfile` | 3,84 | 1,00× | 393,0 | 0,0 | 5473 | 8977→8977 (+0) | 7,58→7,93 | 0 |
| 0 | 6 | `loadfile` | 3,03 | 1,00× | 569,7 | 0,0 | 5279 | 8977→8977 (+0) | 7,93→8,34 | 0 |
| 0 | 8 | `loadfile` | 2,64 | 1,00× | 745,8 | 0,0 | 5204 | 8977→8977 (+0) | 8,34→8,34 | 0 |
| 0 | 4 | `worksteal` | 3,17 | 1,00× | 393,3 | 0,0 | 5468 | 8977→8977 (+0) | 8,23→8,23 | 0 |
| 0 | 8 | `worksteal` | 2,28 | 1,00× | 746,3 | 0,0 | 5170 | 8977→8977 (+0) | 7,97→7,97 | 0 |
| 1 | 1 | `seriell` | 7,03 | 0,85× | 88,4 | 469,3 | 5252 | 8977→8977 (+0) | 7,89→7,90 | 0 |
| 1 | 2 | `loadfile` | 4,32 | 0,74× | 216,7 | 469,3 | 5195 | 8977→8977 (+0) | 7,90→7,90 | 0 |
| 1 | 4 | `loadfile` | 2,84 | 0,74× | 393,4 | 469,3 | 5035 | 8977→8977 (+0) | 8,25→8,25 | 0 |
| 1 | 6 | `loadfile` | 3,89 | 1,28× | 569,6 | 469,3 | 4780 | 8977→8977 (+0) | 8,25→9,35 | 0 |
| 1 | 8 | `loadfile` | 3,86 | 1,46× | 746,2 | 469,3 | 4582 | 8977→8977 (+0) | 9,35→10,44 | 0 |
| 2 | 1 | `seriell` | 8,05 | 0,98× | 88,4 | 938,8 | 4612 | 8977→8977 (+0) | 10,00→10,24 | 0 |
| 2 | 2 | `loadfile` | 4,63 | 0,79× | 216,7 | 938,8 | 4554 | 8976→8976 (+0) | 10,14→10,21 | 0 |
| 2 | 4 | `loadfile` | 2,87 | 0,75× | 393,0 | 938,8 | 4433 | 8976→8976 (+0) | 10,21→10,21 | 0 |
| 2 | 6 | `loadfile` | 2,83 | 0,93× | 569,7 | 938,8 | 4316 | 8976→8976 (+0) | 9,88→9,88 | 0 |
| 2 | 8 | `loadfile` | 2,80 | 1,06× | 745,4 | 938,7 | 4159 | 8976→8976 (+0) | 9,88→10,29 | 0 |
| 3 | 1 | `seriell` | 7,63 | 0,92× | 88,5 | 1407,6 | 4009 | 8976→8976 (+0) | 10,02→10,18 | 0 |
| 3 | 2 | `loadfile` | 4,64 | 0,79× | 216,7 | 1407,6 | 4034 | 8976→8976 (+0) | 9,85→9,85 | 0 |
| 3 | 4 | `loadfile` | 3,23 | 0,84× | 393,1 | 1407,6 | 4019 | 8976→8991 (+15) | 9,62→9,62 | 0 |
| 3 | 6 | `loadfile` | 3,44 | 1,14× | 569,2 | 1407,6 | 3902 | 8991→9107 (+116) | 9,49→9,49 | 0 |
| 3 | 8 | `loadfile` | 4,18 | 1,58× | 745,7 | 1407,6 | 3870 | 9107→9167 (+60) | 9,49→9,69 | 0 |
| 3 | 4 | `worksteal` | 3,78 | 1,19× | 393,2 | 1407,6 | 4098 | 9167→9167 (+0) | 9,69→9,69 | 0 |
| 3 | 8 | `worksteal` | 3,75 | 1,64× | 746,4 | 1407,6 | 3848 | 9167→9167 (+0) | 9,55→9,55 | 0 |

### Kernaussagen der Messreihe

1. **Speicher wächst strikt linear mit der Arbeiter-Zahl:** `~40 MiB` Controller plus `~88 MiB` je Worker (`88,6 MiB` bei `n=1` → `393 MiB` bei `n=4` → `746 MiB` bei `n=8`). Bei einer großen App-Suite (Flask + SQLite + Browser-Fixtures, Richtwert `JE_WORKER_MIB = 512` in `to_spawn/pruef/ausfuehren.py`) entspricht der Sprung von `n=4` auf `n=8` real rund `+1,5 bis +2,0 GiB` zusätzlichem Spitzen-RAM.
2. **Kipp-Punkt bei 3 Bau-Sessions und `n >= 6` (Überbuchung + Swap-Einsatz):**
   - Bei `S = 0` verkürzt sich die Dauer monoton bis `n = 8` (`8,25 s` → `2,64 s` mit `loadfile`, `2,28 s` mit `worksteal`).
   - Bei `S = 3` (zusätzlich `1.408 MiB` aktive Bau-RSS + 3 aktive Kerne) **kehrt sich die Kurve um**: `n = 4` ist mit **`3,23 s`** am schnellsten (`+15 MiB` Swap), während `n = 6` (`3,44 s`, `+116 MiB` Swap) und `n = 8` (**`4,18 s`**, `+60 MiB` weiterer Swap, kumulativ **`+191 MiB`** Swap-Zuwachs bei `MemAvailable < 4.000 MiB`) deutlich **langsamer** sind als `n = 4` (`+29 %` Dauer bei fast doppeltem Prüf-RAM) und **`1,58×`** langsamer als `S = 0, n = 8`.
3. **`--dist worksteal` gegen `--dist loadfile`:** Bei ruhiger Maschine (`S = 0`) senkt `worksteal` die Laufzeit durch Aufbrechen der Nachzügler-Dateien um **14–17 %** (`3,17 s` vs. `3,84 s` bei `n = 4`; `2,28 s` vs. `2,64 s` bei `n = 8`) bei identischem RAM-Bedarf. Unter starker Überbuchung (`S = 3, n = 8`) bremst auch `worksteal` um **`1,64×`** aus.

---

## 2. Ursache der 1346 s gegen 388 s (Abnahme 2, mit Beleg)

Die historische Auswertung der Schlangen-Protokolle (`fahrtenbuch.jsonl`) und die exakte Nachrechnung der Test-Auswahl (`gate_auswahl.waehle()`) auf den Git-Diffs der Probe-1330-Läufe (22./23.11.2026) belegen **zwei zusammenwirkende, klar trennbare Ursachen**:

### Hauptursache (~75–85 % des Unterschieds): Test-Auswahl-Umfang durch zentrale Knoten-Module (Reihenfolge/Diff)

| App-Ticket | Zeit (22./23.11.) | Ergebnis | Gate-Art | Gate-Dauer | Geänderte Dateien | Ausgewählte Testdateien | Zentrale Treiber im Diff | Parallele Server-Bau-Sessions |
|---|---:|---|---|---:|---:|---:|---|---:|
| 1477 (Gegenprobe) | 16:57 | `ticket-gruen` | `AUSWAHL` | **1294,2 s** | 14 | **842** | `account_db.py` (746), `web/app.py` (323) | 0 |
| 1478 | 19:01 | `ticket-gruen` | `AUSWAHL` | **640,8 s** | 23 | **366** | `web/app.py` (323) | 1 |
| 1480 | 23:08 | `ticket-gruen` | `AUSWAHL` | **387,7 s** | 20 | **107** | `web/services/supervisor_zuweisung.py` (74) | 3 |
| 1482 (Erstlauf) | 23:35 | `ticket-gruen` | `AUSWAHL` | **1346,2 s** | 23 | **826** | `account_db.py` (746), `web/app.py` (323) | 3 |
| 1482 (Nachschub) | 00:49 | `ticket-gruen` | `AUSWAHL` | **633,3 s** | 10 | **350** | `web/app.py` (323) | 2 |
| 1481 | 01:00 | `ticket-gruen` | `AUSWAHL` | **305,7 s** | 31 | **107** | `web/services/supervisor_zuweisung.py` (74) | 1–2 |

- **Befund:** Obwohl beide Läufe (`1480` und `1482`) im Fahrtenbuch als `AUSWAHL` markiert sind, zog **App-Ticket 1482 mit `826` Testdateien 7,7× so viele Testdateien** wie **App-Ticket 1480 (`107` Testdateien)**, weil #1482 die zentralen Knoten-Module `account_db.py` (`746` Testdateien) und `web/app.py` (`323` Testdateien) änderte.
- Die Gegenprobe **App-Ticket 1477** (16:57 Uhr, **0** aktive Server-Bau-Sessions, Swap 2 GB) brauchte bei praktisch demselben Knoten-Umfang (`842` Testdateien) bereits **1294,2 s**. Das Grundmodell „fester Sockel `~150–170 s` (Vorprüfungen + `pytest --collect-only` ~92 s + xdist-Start) plus `~1,4–1,5 s` je Testdatei“ bildet alle sechs Läufe konsistent ab.

### Verstärker (+6 % bis +27 % historisch, bis +58 % unter Überbuchung): Last, Swap und starre Arbeiter-Zahl `n = 8`

- **Paar-Vergleich bei identischem Umfang (`107` Testdateien):** App-Ticket **1480** lief unter 3 aktiven Bau-Sessions (7–8 Claude-Prozesse, RAM 8/15 GB, Swap 4 GB) in **387,7 s**; App-Ticket **1481** lief nach Abschluss von #1480 in **305,7 s** → **+26,8 %** längere Prüfzeit durch parallele Last.
- **Paar-Vergleich bei Knoten-Umfang (`~830` Testdateien):** App-Ticket **1482** (`826` Dateien, 3 Bau-Sessions, Swap 4 GB) lag bei **`1,63 s`/Datei** (`1346,2 s`) gegenüber **1477** (`842` Dateien, 0 Server-Bau-Sessions, Swap 2 GB) mit **`1,54 s`/Datei** (`1294,2 s`, **+6 % je Datei**).
- **Mechanik (durch Messreihe oben belegt):** Das Schlangen-Gate fuhr starr mit `DEPLOY_WORKERS=8` (`--dist loadfile`) auf einem 8-Kern-Server. Neben 3 aktiven Bau-Sessions überbuchen `8 + 3 = 11` aktive Prozesse die 8 Kerne und drücken `MemAvailable` unter die Swap-Schwelle (~4 GiB) — in der kontrollierten Messreihe verlor `n = 8` dadurch `+58 %` gegenüber `S = 0` und war langsamer als `n = 4`.

---

## 3. Ursache der drei 10-Sekunden-Fehler `ticket-gate-fehler` (Abnahme 3, mit Beleg)

Die drei `ticket-gate-fehler` an App-Ticket 1482 (23.11. `00:24:13`, `00:24:42`, `00:25:11`; `gate_s = 11,0 s / 9,9 s / 9,1 s`; `gate_rc = 5`; `versuch = 1, 2, 3`) sind **keine Last-, RAM- oder Swap-Fehler**, sondern **vollständig aufgeklärt**:

1. **Auslöser im Gate (Vorprüfung vor pytest):** In Gate 1b/2 meldete die statische Prüfung `scripts/rollen_sicht_check.py` nach ~3 s eine direkte Rollenprüfung in `scripts/abnahme_1482_staging_klickweg.py:220` (`assert p_.get("rolle") == "worker"`) und beendete das Gate mit **Exit 5** (`Rollen-Sicht-Gate rot`). `pytest` startete gar nicht.
2. **Parser-Lücke in der Schlange (`deploy_schlange.py::kennungen_aus_log`):** Die Auswertung extrahiert Befund-Kennungen für `ruff`, `css_kommentar_check`, `kosten_tuer_waechter` und `pytest` (`FAILED`/`ERROR`), **nicht aber für `rollen_sicht_check` oder `check_rollen_quelle`**.
3. **Wirkung (`ticket-gate-fehler` × 3 im 29-s-Takt):** Weil `kennungen_aus_log()` eine leere Liste `[]` lieferte, stuft die Schlange `rc = 5` ohne Kennungen nicht als `ticket-rot` ein, sondern als „Gate nicht lauffähig“ (`ticket-gate-fehler`) und wiederholt denselben unveränderten Stand automatisch `GATE_FEHLER_VERSUCHE = 3` Mal (`00:24:13`, `00:24:42`, `00:25:11`), bevor Versuch 3/3 auf `rot` schaltet.
4. **Gegenbeleg aus dem weiteren Verlauf:** Nach Hinzufügen einer Ausnahme (Commit `39e82ee57`) lief `rollen_sicht_check` durch, `pytest` startete normal (`712,3 s`) und meldete regulär `ticket-rot`; nach Entfernen des direkten Rollenvergleichs (Commit `a89cfa1c4`) lief das Gate in `633,3 s` grün. Dasselbe 3×-Muster mit `4–13 s` und `gate_rc = 5` trat in derselben Nacht auch bei #1476, #1479 und #1483 auf und erklärt den Median von `10 s` aller `ticket-gate-fehler` in `docs/PRUEFTOR_VERGLEICH.md`.
5. **Empfohlener Folge-Fix (im App-/Schlangen-Repo, außerhalb #290):** `kennungen_aus_log()` um die Marken `Rollen-Sicht-Gate rot` und `Rollen-Quelle-Gate rot` ergänzen.

---

## 4. Vorschlag für Schwellen — nur Vorschlag, Bau der Regeln ist L7 (Abnahme 4)

Ausgangslage Bau-Server: **8 Kerne, 16 GiB RAM, 16 GiB Swap** (skaliert in L7 dynamisch anhand `to_spawn/speicher.py` und Geräte-Logbuch; Rangfolge nach L7 `A41`: erst Stabilität, dann Tempo).

| Last-Stufe („Tankanzeige“) | Bedingung (1-Min-Last / `MemAvailable`) | Gleichzeitig aktiv bauende Tickets (max.) | Arbeiter je Prüflauf (`-n`) | Empfohlene Verteilung / Verhalten |
|---|---|---:|---:|---|
| **Grün (ruhig)** | Last `< 6,0` **und** `MemAvailable >= 5.500 MiB` | **3** | **6** (`8` nur bei 0 Bau-Sessions) | `--dist worksteal` (wo vom Repo unterstützt), sonst `loadfile` |
| **Gelb (normal / Last)** | Last `6,0–9,5` **oder** `MemAvailable 3.500–5.500 MiB` | **2–3** (ab 3 keine 4. neu starten) | **4** | `--dist worksteal`; in der Messreihe schnellster Punkt bei 3 Sessions (`3,23 s` vs. `4,18 s` bei `n=8`), halber Prüf-RAM, minimaler Swap |
| **Orange (eng)** | Last `9,5–11,5` **oder** `MemAvailable 2.000–3.500 MiB` | **1–2** (neue Starts warten, laufende bleiben aktiv) | **2** | Drosseln statt abbrechen (L7 `A39`); kein zweites Gate parallel |
| **Rot (Schutzgrenze)** | Last `>= 12,0` **oder** `MemAvailable < 2.000 MiB` | **0 neue Starts** (laufende auslaufen lassen) | **1** (oder warten bis `< 10,0` / `>= 2.500 MiB`) | Harter OOM-/Swap-Schutz (L7 `N15`) |

### Zusätzliche Hinweise für den L7-Regelbau

1. **Vermeidung der `n = 8`-Falle auf 8-Kern-Servern:** `DEPLOY_WORKERS=8` war ursprünglich auf einem größeren Entwickler-Rechner gemessen. Auf dem 8-Kern-Bau-Server sollte die Vorgabe neben Bau-Sessions bei **`n = 4`** (ruhig **`n = 6`**) liegen — das verhindert Kern-Überbuchung und den Swap-Sprung unterhalb `4.000 MiB` `MemAvailable`.
2. **Knoten-Module als eigener Last-Treiber (`A40`):** Änderungen an `account_db.py` oder `web/app.py` erzeugen ~350–840 Testdateien (10–22 min) unabhängig von der Session-Zahl. Für L7 empfiehlt sich, während eines großen Prüflaufs (`> 300` Testdateien) neue Bau-Sessions auf höchstens **2** zu deckeln und die Arbeiter-Zahl aus der obigen Tabelle dynamisch statt statisch festzulegen.
3. **Offen (außerhalb #290):** Gegenmessung unter Windows (separat, `system:windows`); tatsächlicher Umbau der dynamischen Drossel-Regeln und des lokalen Geräte-Logbuchs erfolgt in L7; Fix der Parser-Lücke für `rollen_sicht_check` / `check_rollen_quelle` liegt im App-/Schlangen-Repo.
