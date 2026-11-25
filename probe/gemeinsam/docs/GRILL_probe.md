# Beschluss-Protokoll — Probe-Spec „Leihliste“

Status: abgeschlossen (Vorlage des Probe-Laufs, Inhalt nach Vorgabe des Auftraggebers in Skill-Issue
#426 vom 24.11.2026; nicht neu grillen, so übernehmen)

## Ziel

Die Probe-App bekommt eine kleine Leihliste: Wer hat welchen Gegenstand ausgeliehen, was ist
überfällig, welche Mahngebühr fällt an, wo ist ein Gegenstand doppelt verliehen. Der Inhalt soll
mehrere Code-Arten (Daten, Server-Logik, Oberfläche), echte Rechenlogik mit Randfällen, eine
Blocker-Kette zwischen Tickets und mehrere sichtbar geänderte Stellen haben.

## Befunde

- App heute: `app/server.py`, nur Standardbibliothek; `GET /` und `GET /version` liefern den
  Versions-Text `Probe-App <Version> (<Umgebung>)`. Rauchtest (`skripte/pruefen_komplett.py`) und
  Staging-/Live-Beleg (`skripte/ausrollen.py`) hängen an genau diesem Text.
- `skripte/ausrollen.py` kopiert nur `app/` nach `staging/` bzw. `live/` und startet dort mit dem
  Arbeitsordner `staging/` bzw. `live/`.
- Schnelle Prüfung: `python -m unittest discover -s tests -t .` (Sekunden).

## Beschlüsse

### E1 — Rahmen ✅
**Beschluss:** Nur Standardbibliothek, keine neue Abhängigkeit. `GET /` und `GET /version` bleiben
unverändert. Jedes Ticket bringt eigene Tests unter `tests/` mit (`unittest`, laufen auch mit
`pytest`); Tests geben das Datum „heute“ immer fest vor, nie das echte Tagesdatum. Alle Texte auf
Deutsch mit echten Umlauten. Der Werkzeug-Test unter `.to-spawn/werkzeug/` bleibt unberührt und rot.
**Herleitung:** Rauchtest und Live-Beleg hängen am Versions-Text (Befunde); feste Daten machen die
Rechenwerte prüfbar.
**Wort des Menschen:** „Der Inhalt soll wieder anspruchsvoller werden, ohne die Messbarkeit zu
verlieren“ (#426, 24.11.).

### E2 — Daten: Ausleihe und Ablage (T1) ✅
**Beschluss:** Neues Modul `app/daten.py`:
- `Ausleihe` (dataclass): `id: int`, `gegenstand: str`, `person: str`, `von: date`, `bis: date`
  (Fälligkeitstag), `zurueck: date | None` (Rückgabetag, `None` = noch verliehen).
- `pruefe(ausleihe)` wirft `ValueError` mit deutschem Text bei leerem Gegenstand oder leerer Person
  (nach Entfernen von Leerzeichen), `bis` vor `von` oder `zurueck` vor `von`.
- `lade(pfad) -> list[Ausleihe]` und `speichere(pfad, ausleihen)`: JSON-Liste, Datumsfelder als
  `JJJJ-MM-TT`, `zurueck` als `null`, wenn offen.
- `arbeitsbestand(arbeitsordner) -> list[Ausleihe]`: liest `daten/leihliste.json` im Arbeitsordner;
  fehlt die Datei, gilt der Startbestand `app/startbestand.json` (E3). `/daten/` kommt in
  `.gitignore`.
**Herleitung:** `ausrollen.py` kopiert nur `app/`, darum liegt der Startbestand dort; neue Einträge
landen im Arbeitsordner und verschwinden beim Aufräumen mit `staging/` bzw. `live/`.
**Wort des Menschen:** „mehrere Code-Arten (Oberfläche, Server-Logik, Daten)“ (#426, 24.11.).

### E3 — Startbestand und Prüfwerte ✅
**Beschluss:** `app/startbestand.json` enthält genau diese sechs Ausleihen:

| id | gegenstand | person | von | bis | zurueck |
|---|---|---|---|---|---|
| 1 | Bohrmaschine | Anna | 2026-11-20 | 2026-12-01 | – |
| 2 | Leiter | Ben | 2026-10-20 | 2026-11-01 | – |
| 3 | Zelt | Cara | 2026-11-25 | 2026-12-07 | – |
| 4 | Beamer | Dana | 2026-11-10 | 2026-11-20 | 2026-11-26 |
| 5 | Bohrmaschine | Emil | 2026-11-28 | 2026-12-05 | – |
| 6 | Beamer | Finn | 2026-11-26 | 2026-12-20 | – |

Prüfwerte bei heute = 2026-12-10 (Pflicht in den Tests von T2–T6):

| id | Tage überfällig | Status | Mahngebühr |
|---|---|---|---|
| 1 | 9 | überfällig | 3,00 € |
| 2 | 39 | überfällig | 10,00 € (Deckel) |
| 3 | 3 | überfällig | 0,00 € (Kulanz) |
| 4 | 6 | zurück | 1,50 € |
| 5 | 5 | überfällig | 1,00 € |
| 6 | 0 | offen | 0,00 € |

Summe Mahngebühren 15,50 €; Überschneidung genau das Paar (1, 5); Ausleihe 4 und 6 überschneiden
sich nicht (Übergabe am Rückgabetag).
**Herleitung:** Jede Zeile trifft einen Randfall aus E4/E5 (Kulanz-Grenze, Deckel, Rückgabe nach
Frist, Übergabe am selben Tag, überfällige Ausleihe belegt bis heute).
**Wort des Menschen:** „echte Rechenlogik mit Randfällen (z. B. Fristen, Mahngebühr,
Überschneidungen)“ (#426, 24.11.).

### E4 — Rechenlogik: Frist und Mahngebühr (T2) ✅
**Beschluss:** Neues Modul `app/rechnen.py`:
- `tage_ueberfaellig(ausleihe, heute) -> int`: Ende = `zurueck`, sonst `heute`; Ergebnis
  `max(0, (Ende − bis) in Tagen)`. Rückgabe am Fälligkeitstag = 0; heute = Fälligkeitstag = 0.
- `status(ausleihe, heute) -> str`: `zurück`, wenn zurückgegeben; sonst `überfällig`, wenn
  `heute` nach `bis`; sonst `offen`.
- `mahngebuehr_cent(ausleihe, heute) -> int`: die ersten 3 Tage überfällig sind Kulanz (0 Cent),
  ab dem 4. Tag 50 Cent je Tag über der Kulanz, höchstens 1000 Cent (Deckel). Nach der Rückgabe
  bleibt die Gebühr auf dem Stand des Rückgabetags stehen. Geld nur als ganze Cent (`int`), nie
  `float`.
- `euro(cent) -> str`: deutsches Format `3,00 €` (Komma, zwei Nachkommastellen, Leerzeichen vor €).
- Kulanz-Tage, Cent je Tag und Deckel stehen als benannte Konstanten genau einmal oben im Modul.
**Herleitung:** Ganze Cent vermeiden Rundungsfehler; die Konstanten an einer Stelle halten eine
spätere Regeländerung klein.
**Wort des Menschen:** „Fristen, Mahngebühr“ (#426, 24.11.).

### E5 — Rechenlogik: Überschneidungen (T3) ✅
**Beschluss:** Neues Modul `app/ueberschneidung.py` mit
`ueberschneidungen(ausleihen, heute) -> list[tuple[int, int]]`:
- Belegte Tage einer Ausleihe: von `von` bis einschließlich letztem Tag. Letzter Tag = Tag vor
  `zurueck`, wenn zurückgegeben (der Rückgabetag ist frei für die nächste Ausleihe); sonst der
  spätere von `bis` und `heute` (eine überfällige Ausleihe belegt bis heute). Rückgabe am
  Ausleihtag belegt keinen Tag.
- Zwei Ausleihen überschneiden sich, wenn der Gegenstand gleich ist (ohne Rücksicht auf Groß- und
  Kleinschreibung und Leerzeichen am Rand) und sich die belegten Tage überlappen.
- Ergebnis: jedes Paar genau einmal als `(kleinere id, größere id)`, aufsteigend sortiert.
**Herleitung:** Die Regel braucht nur das Datenmodell aus E2, nicht die Gebühren aus E4; darum
kann T3 neben T2 gebaut werden.
**Wort des Menschen:** „Überschneidungen“ (#426, 24.11.).

### E6 — Server-Logik: Stand und JSON-Schnittstelle (T4) ✅
**Beschluss:**
- Neues Modul `app/api.py` mit `stand(ausleihen, heute) -> dict`: `{"heute": "JJJJ-MM-TT",
  "ausleihen": [...], "summe_cent": int, "summe": "15,50 €", "ueberschneidungen": [[1, 5]]}`;
  je Ausleihe die Felder aus E2 plus `status`, `tage_ueberfaellig`, `mahngebuehr_cent`,
  `mahngebuehr` (Text aus `euro`), sortiert nach `id`.
- `app/server.py`: `GET /api/ausleihen` liefert `stand` als JSON (UTF-8, Code 200).
  `POST /api/ausleihen` nimmt JSON `{"gegenstand", "person", "von", "bis"}`, vergibt
  `id` = größte vorhandene + 1, speichert in `daten/leihliste.json` und antwortet 201 mit der neuen
  Ausleihe; kaputtes JSON oder `pruefe`-Fehler → 400 `{"fehler": "<deutscher Text>"}`; würde die
  neue Ausleihe eine Überschneidung (E5) erzeugen → 409
  `{"fehler": "Überschneidung mit Ausleihe <id>"}`, nichts gespeichert.
- Startschalter `--heute JJJJ-MM-TT` (fehlt er: echtes Tagesdatum); `baue_server` bekommt `heute`
  und den Arbeitsordner als Schlüsselwort-Parameter mit Vorgabe, bestehende Aufrufe
  `baue_server(port, umgebung)` laufen unverändert.
- T4 legt auch die Seiten-Adressen an: `GET /leihliste` ruft `app/seite_leihliste.py`
  `html(stand) -> str`, `GET /mahnungen` ruft `app/seite_mahnungen.py` `html(stand) -> str`
  (Content-Type `text/html; charset=utf-8`). Beide Module legt T4 als schlichten Platzhalter an
  (Überschrift + „noch leer“); T5 und T6 füllen je nur ihr eigenes Modul.
**Herleitung:** Die Seiten bekommen fertige Werte aus `stand`, rechnen nie selbst. Getrennte
Seiten-Module lassen T5 und T6 parallel bauen, ohne in derselben Datei zu kollidieren.
**Wort des Menschen:** „Server-Logik“ (#426, 24.11.).

### E7 — Oberfläche: Leihliste (T5) ✅
**Beschluss:** `app/seite_leihliste.py` baut die Seite `/leihliste`: Überschrift „Leihliste“,
Tabelle mit Gegenstand, Person, ausgeliehen am, fällig am, Status, Mahngebühr; Daten deutsch
(`01.12.2026`); überfällige Zeilen sichtbar hervorgehoben (CSS-Klasse `ueberfaellig`); darunter
die Zeile `Summe Mahngebühren: 15,50 €` (Wert aus `stand`); gibt es Überschneidungen, oben ein
Hinweis-Kasten je Paar: „Achtung: Bohrmaschine doppelt verliehen (Ausleihe 1 und 5)“. Alle Texte
aus den Daten werden mit `html.escape` ausgegeben. Bewusst offen (die Bau-Session entscheidet und
schreibt die Entscheidung auf): Reihenfolge der Zeilen und Art der Hervorhebung.
**Herleitung:** Erste sichtbare Stelle für Rundschau und Kritik-Runde; offene Punkte geben Stoff
für die Entscheidungs-Liste.
**Wort des Menschen:** „mehrere sichtbar geänderte Stellen, damit die Rundschau als Präsentation
und die Kritik-Runde etwas zu zeigen haben“ (#426, 24.11.).

### E8 — Oberfläche: Mahnliste (T6) ✅
**Beschluss:** `app/seite_mahnungen.py` baut die Seite `/mahnungen`: Überschrift „Mahnungen“, nur
Ausleihen mit Mahngebühr über 0, gruppiert je Person mit Summe je Person; Personen nach Summe
absteigend, bei Gleichstand nach Name. Bei heute = 2026-12-10: Ben 10,00 € · Anna 3,00 € ·
Dana 1,50 € · Emil 1,00 €; Cara und Finn fehlen. Ohne Gebühren der Satz „Keine offenen
Mahnungen.“ Texte mit `html.escape`. Bewusst offen: Darstellung (Tabelle oder Liste).
**Herleitung:** Zweite sichtbare Stelle, rechnet über Personen statt über Ausleihen (eigener
Randfall: Gleichstand, Gebühr 0 ausgeblendet).
**Wort des Menschen:** „mehrere sichtbar geänderte Stellen“ (#426, 24.11.).

### E9 — Ticket-Schnitt (Vorgabe für /to-tickets) ✅
**Beschluss:** Sechs Bau-Tickets plus das Abnahme-Ticket am Kettenende (blockiert durch T1–T6;
trägt **immer** die Labels `ready-for-agent` und `checkpoint:human`, nie nur `ready-for-agent`,
sonst verweigert `regularien` mit Exit 3). Abhängigkeiten als native `blocked_by`:

| Ticket | Titel | Beschluss | blockiert durch |
|---|---|---|---|
| T1 | Daten: Ausleihe, Ablage, Startbestand | E2, E3 | – |
| T2 | Frist und Mahngebühr | E4 | T1 |
| T3 | Überschneidungen | E5 | T1 |
| T4 | Stand, JSON-Schnittstelle, Seiten-Adressen | E6 | T2, T3 |
| T5 | Seite Leihliste | E7 | T4 |
| T6 | Seite Mahnungen | E8 | T4 |

Kette: T1 → T2 → T4 → T5 (bzw. → T6). Parallel baubar: T2 neben T3, T5 neben T6. Jedes Ticket
bleibt so klein, dass die schnelle Prüfung unter 3 min bleibt.
**Herleitung:** Eine echte Blocker-Kette prüft, ob ein Nachfolger in Sekunden startet, sobald der
Vorgänger fertig ist; die parallelen Paare prüfen gleichzeitigen Bau ohne Datei-Kollision.
**Wort des Menschen:** „mindestens eine Blocker-Kette zwischen Tickets, nicht nur drei
unabhängige“ (#426, 24.11.).

## Verworfen ❌

- V1: Eine einzige große Seite mit allem — zu wenig Code-Arten, keine Kette.
- V2: Datenbank (SQLite) — bringt nichts für die Messung, JSON-Datei reicht.

## Offen 💭

- O1: Rückgabe über die Oberfläche (Formular) — nicht in dieser Probe.
