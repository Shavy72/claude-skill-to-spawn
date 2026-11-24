# Probe-Spec (erweitert) — wiederholbarer Probe-Lauf

Quelle: Skill-Issue #426 (Auftrag + Kommentar „Reihenfolge und Messung“ vom 24.11.2026),
Schnittplan 425/426 Teil F. Dieser Teil baut nur die Vorlage; ein Lauf startet erst auf das
ausdrückliche Wort des Auftraggebers (#426 „Grenzen“).

Der Probe-Lauf prüft den **ganzen** Standard-Ablauf, in **beiden** Repo-Arten:

> „alles durchbauen bis staging dann alles in Rundaschu auch die entscheiungen die beim bau für
> mich getroffen wurden dann von mir abnahem oder kritik die nochmal oteriert wird dann wenns
> passt live schalten in die live app“

## 1 Vorlagen

| Pfad | Inhalt |
|---|---|
| `probe/gemeinsam/` | Probe-App (`app/`, nur Standardbibliothek), App-Tests (`tests/`), `skripte/pruefen_komplett.py`, `skripte/ausrollen.py`, absichtlich roter Werkzeug-Test `.to-spawn/werkzeug/test_werkzeug_rot.py`, README |
| `probe/mit-staging/` | `.to-spawn/config.json` mit Staging-Adresse, `ports.json` mit Staging-Port |
| `probe/ohne-staging/` | `.to-spawn/config.json` mit `staging.ohne: true`, `ports.json` ohne Staging-Port |
| `probe/gemeinsam/docs/GRILL_probe.md` | Beschluss-Protokoll der Probe-Spec (Leihliste, Abschnitt 3); im Lauf-Repo als `docs/GRILL_<heute>_probe.md` |
| `probe/anlegen.sh` | legt daraus ein Lauf-Repo an (`git init` + erster Commit; mit `--github <owner/name>` Repo anlegen und `main` pushen) |

`anlegen.sh` kopiert `gemeinsam/` und legt die Variante darüber. Die App gibt es damit nur einmal;
eine Änderung an ihr trifft beide Varianten gleich. Die Vorlagen-Tests sammelt das Skill-pytest
nie ein (`probe/conftest.py`), sie laufen nur im angelegten Lauf-Repo.

Die App: `GET /` liefert `Probe-App <Version> (<Umgebung>)`, z. B. `Probe-App 1.0.0 (live)`.

| Zweck | Befehl im Lauf-Repo | Erwartung |
|---|---|---|
| Schnelle Prüfung | `python -m unittest discover -s tests -t .` | grün in Sekunden (gemessen 0,6 s) |
| Komplett-Prüfung | `python skripte/pruefen_komplett.py` | App-Tests + Rauchtest (Start + 1 Abruf) |
| Staging-Schritt (nur mit Staging) | `python skripte/ausrollen.py staging` | Zeile `Beleg: http://127.0.0.1:<port>/ -> Probe-App … (staging)` |
| Live-Schritt (Übungs-Live-App) | `python skripte/ausrollen.py live` | Zeile `Beleg: … (live)` |
| Aufräumen | `python skripte/ausrollen.py live --aufraeumen` (bzw. `staging`) | Port zu, Ordner `live/` bzw. `staging/` weg |

Staging und Übungs-Live laufen als Kopie von `app/` in den Ordnern `staging/` und `live/` des
Lauf-Repos (nicht eingecheckt), je mit eigenem Port. Ohne Staging lehnt der Staging-Schritt mit
Exit 2 ab.

### Konfig-Schlüssel und Teil A

| Schlüssel | Wirkt heute? |
|---|---|
| `staging.url`, `staging_start`, `staging.ohne` | ja (`config.hat_staging`, `config.staging_bewusst_ohne`, #410) |
| `pruefung.schnell`, `pruefung.komplett` | ja (`to_spawn/pruefplan.py`, #425): `komplett` läuft einmal je Spec über `to_spawn/komplett_lauf.py` — im capo-Takt, sobald alle Tickets auf Staging sind, sonst per `to_spawn.py komplett <S> --moment vor-live` |
| `live.befehl`, `live.url` | **erst mit Teil A**; bis dahin führt die Lauf-Session den Live-Schritt nach diesem Ablauf aus |
| `live.aufraeumen` | Vorschlag dieser Vorlage, nicht im Schnittplan; Teil A entscheidet, ob er bleibt |

## 2 Ablauf eines Laufs

Ein Lauf = beide Varianten nacheinander auf derselben Maschine, erst Bau-Server, nach Bestehen am
Windows-PC (#426 Reihenfolge). Voraussetzung: Teile A–D gebaut und installiert (Schnittplan).

1. **Lauf-Repo anlegen** (je Variante, `<n>` = Laufnummer):
   ```
   bash probe/anlegen.sh ~/to-spawn-probe-<n>-mit mit-staging --github Shavy72/to-spawn-probe-<n>-mit
   bash probe/anlegen.sh ~/to-spawn-probe-<n>-ohne ohne-staging --app-port 18511 --live-port 18513 \
        --github Shavy72/to-spawn-probe-<n>-ohne
   ```
   `--github` legt das private GitHub-Repo (mit `probe` im Namen) an und pusht den Start-Commit
   auf `main`, damit Tickets als Issues entstehen können und der erste Pull-Request ein Ziel hat
   (#467). Kein zweiter Schritt von Hand. Startzeit in die Messtabelle.
2. **Spec + Tickets:** Das Beschluss-Protokoll `docs/GRILL_<datum>_probe.md` liegt schon im
   Lauf-Repo (Abschnitt 3; nicht neu grillen), dann `/to-spec docs/GRILL_<datum>_probe.md`,
   `/to-tickets`.
3. **Start:** `/to-spawn <S>` (Bau-Server) bzw. `/to-spawn-local <S>` (PC). Ab hier kein Eingriff.
4. **Beobachten** (Abschnitt 5 laufend füllen): Start jedes Tickets, Dauer der schnellen Prüfung je
   Ticket, Rückfragen in den Fenstern, jede Strecke über 10 min ohne Fortschritt.
5. **Komplett-Prüfung:** mit Staging startet sie der capo-Takt selbst, sobald alle Tickets
   `staging:fertig` sind; ohne Staging vor dem Live-Schritt (Schritt 8). Zählen: genau 1
   (Merker `.to-spawn/pruef/komplett-<S>.json`).
6. **Rundschau:** liegt als Datei im Lauf-Repo und enthält die Entscheidungs-Liste.
7. **Kritik-Runde:** Kritik-Text aus Abschnitt 4.1 als Antwort auf die Abnahme-Frage geben. Erwartet:
   Nachbesserung ohne Rückfrage, neue Rundschau. Erst dann „passt“ geben.
8. **Live-Schritt:** nach „passt“ `python to_spawn.py live <S>` – die Tür prüft selbst (Komplett-Prüfung
   `vor-live`; mit Staging gilt der grüne Lauf aus dem Takt) und startet `live.befehl` nur bei Grün
   (Exit 0 = live grün, Exit 1 = nicht gestartet oder Live rot, Grund beheben, Exit 2 = `live.befehl`
   fehlt, Live-Schritt nach Repo-Ablauf von Hand). Beleg-Zeile ins Lauf-Protokoll, zusätzlich ein
   eigener Abruf der Live-Adresse.
9. **Aufräumen:** Übungs-Live und Staging beenden, Worktrees/Zweige der Spec weg, Tickets im
   richtigen Zustand (M6). Endzeit in die Messtabelle.

## 3 Inhalt der Probe-Spec: Leihliste (Beschluss-Protokoll)

Quelle des Inhalts ist genau eine Datei: `probe/gemeinsam/docs/GRILL_probe.md`. `anlegen.sh` legt
sie in **beiden** Varianten unverändert als `docs/GRILL_<heute>_probe.md` ins Lauf-Repo; daraus
schneiden `/to-spec` → `/to-tickets` die Tickets ohne Rückfrage. Hier nur der Überblick:

| Ticket | Inhalt | Code-Art | blockiert durch |
|---|---|---|---|
| T1 | `app/daten.py`: Ausleihe, Prüfung, JSON-Ablage, Startbestand (6 Ausleihen) | Daten | – |
| T2 | `app/rechnen.py`: Tage überfällig, Status, Mahngebühr (3 Tage Kulanz, 0,50 € je Tag, Deckel 10,00 €), Euro-Text | Rechenlogik | T1 |
| T3 | `app/ueberschneidung.py`: Doppel-Verleih je Gegenstand (Übergabe am Rückgabetag erlaubt, überfällig belegt bis heute) | Rechenlogik | T1 |
| T4 | `app/api.py` + `app/server.py`: `GET`/`POST /api/ausleihen` (201/400/409), `--heute`, Seiten-Adressen mit Platzhaltern | Server-Logik | T2, T3 |
| T5 | `app/seite_leihliste.py`: Seite `/leihliste` (Tabelle, Hervorhebung, Summe, Hinweis Doppel-Verleih) | Oberfläche | T4 |
| T6 | `app/seite_mahnungen.py`: Seite `/mahnungen` (je Person, sortiert) | Oberfläche | T4 |

Kette T1 → T2 → T4 → T5/T6; parallel T2 neben T3 und T5 neben T6. Dazu das übliche
Abnahme-Ticket (`checkpoint:human`), zusammen 7. `GET /` und `/version` bleiben unverändert
(Rauchtest und Live-Beleg hängen daran). Prüfwerte bei heute = 2026-12-10 stehen im Protokoll
(E3): Summe 15,50 €, Überschneidung (1, 5). Offen gelassen für die Entscheidungs-Liste:
Zeilen-Reihenfolge und Hervorhebung (T5), Darstellung der Mahnliste (T6).

Der Kritik-Text (Abschnitt 4) steht absichtlich **nicht** im Lauf-Repo, sonst bauen die Sessions
ihn vorweg.

## 4 Kritik-Runde und Abnahme

### 4.1 Kritik-Text

Wörtlich als Antwort auf die erste Abnahme-Frage geben (beide Varianten gleich):

> „Punkt Mahngebühr anders: Kulanz nur 2 Tage statt 3 und Deckel 7,50 € statt 10,00 €. Außerdem
> soll in der Leihliste bei jeder überfälligen Zeile ‚seit N Tagen überfällig‘ stehen. Sonst passt
> alles. Bitte nachbessern und die Rundschau neu machen.“

Er trifft die Rechenlogik (T2: Konstanten) und zwei sichtbare Stellen (T5 neuer Text, T5/T6 neue
Beträge). Erwartet: Nachbesserung ohne Rückfrage, neue Rundschau mit geänderter Regel (alte
Entscheidung als überholt markiert), danach erneute Abnahme-Frage. Erst dann „passt“.

### 4.2 Abnahme-Schritte

Im Lauf-Repo nach dem Merge aller Tickets, Port = `app` aus `ports.json`:

1. `python -m app.server --port <app> --heute 2026-12-10` starten.
2. `curl -s http://127.0.0.1:<app>/` → unverändert `Probe-App <Version> (lokal)`.
3. `curl -s http://127.0.0.1:<app>/api/ausleihen` → Werte aus E3: `summe_cent` 1550,
   `ueberschneidungen` `[[1, 5]]`, Ausleihe 3 `mahngebuehr_cent` 0.
4. `curl -s -X POST -d '{"gegenstand":"Leiter","person":"Gina","von":"2026-12-01","bis":"2026-12-12"}' http://127.0.0.1:<app>/api/ausleihen`
   → 409 (Leiter ist bei Ben überfällig); dasselbe mit `"gegenstand":"Zelt"` und `"von":"2026-12-11"` → 201.
   Danach `daten/leihliste.json` löschen (Startbestand wieder aktiv).
5. `/leihliste` im Browser: Summe `15,50 €`, Hinweis „Bohrmaschine doppelt verliehen (Ausleihe 1 und 5)“,
   überfällige Zeilen hervorgehoben.
6. `/mahnungen`: Ben 10,00 € · Anna 3,00 € · Dana 1,50 € · Emil 1,00 €.

Nach der Kritik-Runde (gleiche Schritte, neue Soll-Werte):

| Stelle | vorher | nachher |
|---|---|---|
| `summe_cent` / Summe | 1550 / 15,50 € | 1500 / 15,00 € |
| Ausleihe 2 (Ben, 39 Tage) | 10,00 € | 7,50 € |
| Ausleihe 3 (Cara, 3 Tage) | 0,00 € | 0,50 € |
| `/leihliste`, Zeile Anna | – | „seit 9 Tagen überfällig“ |
| `/mahnungen` | Ben, Anna, Dana, Emil | Ben 7,50 € · Anna 3,50 € · Dana 2,00 € · Emil 1,50 € · Cara 0,50 € |

## 5 Messtabelle (je Lauf, gleiche Spalten für jeden Lauf)

Geschwindigkeit, Lauf gesamt:

| Lauf | Variante | Maschine | Gesamtdauer | Dauer der Komplett-Prüfung | Zeit von „alle Tickets fertig“ bis Rundschau | Dauer Kritik-Runde | Dauer Live-Schritt | Dauer Aufräumen |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

Geschwindigkeit, je Ticket:

| Lauf | Variante | Ticket | Bauzeit | Wartezeit bis zum Start | Wartezeit auf Antworten | Dauer der schnellen Prüfung |
|---|---|---|---|---|---|---|
| | | | | | | |

Aufhänger (jede Strecke über 10 min ohne Fortschritt):

| Lauf | Uhrzeit | Dauer | Ursache | wer sie gelöst hat (selbst geheilt oder Eingriff) | zugehöriges Issue |
|---|---|---|---|---|---|
| | | | | | |

Zählwerte (Messgrößen #426, Soll in Klammern):

| Lauf | Variante | Eingriffe (0) | Rückfragen (0) | Komplett-Prüfungen (1) |
|---|---|---|---|---|
| | | | | |

Vergleichswerte Lauf 1 (Server): 3 h 53 min gesamt, davon über 2,5 h Warten; Ticket 1 in 3 min
gebaut, 42 min bis zum Abschluss.

## 6 Auswertung: Messlatte L10 „Schweizer Uhrwerk“

Quelle `docs/LASTENHEFT.md` L10 (A65–A68, N24, N25). Jede Wartezeit und jeder Aufhänger aus
Abschnitt 5 wird genau einer Klasse zugeordnet:

| Klasse | Bedeutung | Folge |
|---|---|---|
| Ticket-Abhängigkeit | Ticket wartet auf einen Vorgänger aus `blocked_by`, der noch **nicht** fertig ist (in dieser Probe nur T2/T3 auf T1, T4 auf T2+T3, T5/T6 auf T4) | zulässig |
| System-Anomalie | jede andere Wartezeit, auch Warten auf einen schon fertigen Vorgänger (A66) oder auf den nächsten Takt (N24) | Issue anlegen, in die Aufhänger-Tabelle |

Gemessen in Sekunden (A67): Vorgänger zu → Nachfolger arbeitet; Kommentar/Urteil → erste
Reaktion. **Bestanden heißt null System-Anomalien** (A68, N25), zusätzlich zu Abschnitt 7.

## 7 Bestehens-Kriterien

Der Lauf besteht nur, wenn alle sieben Punkte aus #426 in **beiden** Varianten belegt sind
(Beleg = Befehl + Ausgabe oder Protokoll-Zeile):

1. **Schnelle Prüfung je Ticket** läuft wirklich (Lauf-Repo hat einen Prüf-Befehl; in Lauf 1 fehlte
   er, #410) und dauert ≤ 3 min.
2. **Komplett-Prüfung genau einmal je Spec:** mit Staging, wenn alle Tickets auf Staging sind; ohne
   Staging vor dem Live-Schritt.
3. **Entscheidungs-Liste** steht in der Rundschau (in Lauf 1: 8 Entscheidungen, keine Liste, #421;
   Rundschau als Datei im Repo, nicht nur als Link).
3a. **Rundschau = Deck mit drei Teilen** (Nutzer-Präsentation, gewohnter Rest, Entscheidungs-Zeitstrahl;
   Definition `to_spawn/rundschau_deck.py`, #529): `skripte/rundschau_deck.py pruefen <datei> --entscheidungen
   <json>` ist grün. Markdown oder einfache HTML-Seite (wie in Lauf 2) = rot.
4. **Kritik-Runde:** vor der Abnahme kommt eine vorbereitete Kritik („Punkt X anders“); erwartet:
   Nachbesserung ohne Rückfrage, neue Rundschau, erst dann Abnahme (#370).
5. **Live-Schritt:** nach der Abnahme geht der Stand in eine Übungs-Live-App (zweiter Ordner/Port im
   Lauf-Repo), Beleg per Abruf; danach Aufräumen (B11).
6. **Zwei Varianten der Probe:** Repo mit Staging-App und Repo ohne Staging-App (nur lokaler Start).
   Beide müssen bestehen.
7. **Trennung:** Ein absichtlich roter Test des Bau-Werkzeugs darf die Probe-App nicht aufhalten.

Die Vorlage selbst ist geprüft durch `tests/test_probe_vorlagen_426.py` (echte Prozesse und
Abrufe): Anlegen, schnelle und Komplett-Prüfung grün, App-Abruf, Staging-/Live-Schritt mit
Aufräumen, roter Werkzeug-Test wird weder vom Prüf-Befehl noch von nacktem `pytest` eingesammelt.
