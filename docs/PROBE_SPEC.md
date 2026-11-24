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
2. **Spec + Tickets:** Beschluss-Protokoll `docs/GRILL_<datum>_probe.md` aus Abschnitt 3 anlegen
   (Beschlüsse übernehmen, nicht neu grillen), dann `/to-spec`, `/to-tickets`.
3. **Start:** `/to-spawn <S>` (Bau-Server) bzw. `/to-spawn-local <S>` (PC). Ab hier kein Eingriff.
4. **Beobachten** (Abschnitt 5 laufend füllen): Start jedes Tickets, Dauer der schnellen Prüfung je
   Ticket, Rückfragen in den Fenstern, jede Strecke über 10 min ohne Fortschritt.
5. **Komplett-Prüfung:** mit Staging startet sie der capo-Takt selbst, sobald alle Tickets
   `staging:fertig` sind; ohne Staging vor dem Live-Schritt (Schritt 8). Zählen: genau 1
   (Merker `.to-spawn/pruef/komplett-<S>.json`).
6. **Rundschau:** liegt als Datei im Lauf-Repo und enthält die Entscheidungs-Liste.
7. **Kritik-Runde:** Kritik-Text aus Abschnitt 4 als Antwort auf die Abnahme-Frage geben. Erwartet:
   Nachbesserung ohne Rückfrage, neue Rundschau. Erst dann „passt“ geben.
8. **Live-Schritt:** nach „passt“ `python to_spawn.py live <S>` – die Tür prüft selbst (Komplett-Prüfung
   `vor-live`; mit Staging gilt der grüne Lauf aus dem Takt) und startet `live.befehl` nur bei Grün
   (Exit 0 = live grün, Exit 1 = nicht gestartet oder Live rot, Grund beheben, Exit 2 = `live.befehl`
   fehlt, Live-Schritt nach Repo-Ablauf von Hand). Beleg-Zeile ins Lauf-Protokoll, zusätzlich ein
   eigener Abruf der Live-Adresse.
9. **Aufräumen:** Übungs-Live und Staging beenden, Worktrees/Zweige der Spec weg, Tickets im
   richtigen Zustand (M6). Endzeit in die Messtabelle.

## 3 Inhalt der Probe-Spec (für das Beschluss-Protokoll)

Ziel: Die Probe-App zeigt mehr als die Version. Drei Tickets, klein und unabhängig genug für
parallelen Bau, mit mindestens zwei Stellen, an denen die Bau-Session selbst entscheiden muss
(Stoff für die Entscheidungs-Liste):

- **T1 — Gesund-Abruf:** `GET /gesund` liefert `ok` (Code 200). Entscheidung offen gelassen:
  Text- oder JSON-Antwort.
- **T2 — Version 1.1.0:** Version auf 1.1.0, der Versions-Text nennt zusätzlich das Startdatum der
  App. Entscheidung offen gelassen: Datumsformat.
- **T3 — Unbekannte Adressen:** jeder andere Pfad liefert 404 mit einem kurzen deutschen Text
  statt der Standard-Fehlerseite.

Jedes Ticket bringt eigene App-Tests unter `tests/` mit. Der Werkzeug-Test unter
`.to-spawn/werkzeug/` bleibt unberührt und rot.

## 4 Kritik-Text für die Kritik-Runde

Wörtlich als Antwort auf die erste Abnahme-Frage geben:

> „Punkt T3 anders: Die 404-Antwort soll zusätzlich den aufgerufenen Pfad nennen, z. B.
> ‚Nicht gefunden: /abc‘. Sonst passt alles. Bitte nachbessern und die Rundschau neu machen.“

Erwartet: Nachbesserung ohne Rückfrage, neue Rundschau mit geändertem T3 (alte Entscheidung als
überholt markiert), danach erneute Abnahme-Frage. Erst dann „passt“.

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

## 6 Bestehens-Kriterien

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
