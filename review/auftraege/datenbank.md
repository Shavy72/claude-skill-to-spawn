# Auftrag datenbank

## Prüfe

- Migration: nicht umkehrbar ohne Hinweis; `DROP`/`ALTER … DROP COLUMN` ohne Datensicherung; `NOT NULL` ohne Default auf gefüllter Tabelle; Umbenennung bricht laufenden Code.
- Reihenfolge: Code nutzt neue Spalte, bevor die Migration beim Deploy läuft (oder umgekehrt).
- Transaktionen: mehrere zusammengehörige Schreibvorgänge ohne Transaktion; Commit fehlt; Fehler zwischen Schritten lässt halben Zustand.
- Nebenläufigkeit: Lesen-dann-Schreiben ohne Sperre/Bedingung (Lost Update), doppelte Einfügung ohne Unique-Constraint.
- Constraints: Regel nur im App-Code, wo ein DB-Constraint (UNIQUE, FK, CHECK) sie sicher machen würde — nur bei belegtem Doppel-/Waisen-Risiko.
- Abfragen: SQL per String-Bau mit Fremddaten; fehlender Index auf neuer Filter-/Join-Spalte großer Tabellen; N+1 in Schleifen über unbegrenzte Mengen.
- SQLite-spezifisch: `ALTER TABLE`-Grenzen, fehlendes `PRAGMA foreign_keys=ON`, Schreibsperren bei parallelen Prozessen.
- Status-Felder: direkte `UPDATE`s am Status vorbei an der zentralen Funktion.

Projekt-eigene Regeln: Abschnitt `## datenbank` der privaten `~/.claude/review/regeln.md` (VERTRAG.md §2a) gilt zu Daten und Status zusätzlich, Zeilen `Falls Repo <name>: …` nur in diesem Repo (z. B. Status-Wechsel nur über eine Funktion, erlaubte Reihenfolge der Status).

## Ignoriere

- ORM-Stilfragen, Namenskonventionen von Tabellen/Spalten.
- Indexwünsche bei kleinen, festen Tabellen.
- N+1 bei fester, kleiner Kardinalität.

## Ausgabe

Wie `basis.md`. `pruefer` = dein Name.
