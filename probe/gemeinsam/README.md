# Probe-App (@@VARIANTE@@)

Lauf-Repo für einen Probe-Lauf des Bau-Werkzeugs to-spawn, angelegt aus `probe/` im Skill-Repo
(Ablauf: `docs/PROBE_SPEC.md` dort). Hier baut nicht der Mensch, sondern die Ticket-Sessions.

Die App ist absichtlich winzig: ein Abruf liefert den Versions-Text, z. B. `Probe-App 1.0.0 (live)`.

## Befehle

| Zweck | Befehl |
|---|---|
| App lokal starten | `python -m app.server --port @@APP_PORT@@` |
| Schnelle Prüfung (je Ticket, Sekunden) | `python -m unittest discover -s tests -t .` |
| Komplett-Prüfung (1× je Spec: Tests + Rauchtest) | `python skripte/pruefen_komplett.py` |
| Staging-Schritt (nur Variante mit Staging) | `python skripte/ausrollen.py staging` |
| Live-Schritt (Übungs-Live-App) | `python skripte/ausrollen.py live` |
| Aufräumen | `python skripte/ausrollen.py live --aufraeumen` (bzw. `staging --aufraeumen`) |

Ports: lokal @@APP_PORT@@ · Staging @@STAGING_TEXT@@ · Übungs-Live @@LIVE_PORT@@ (stehen in `ports.json`).
Staging und Live laufen als Kopie von `app/` in den Ordnern `staging/` bzw. `live/` (nicht eingecheckt).

## Absichtlich roter Werkzeug-Test

`.to-spawn/werkzeug/test_werkzeug_rot.py` ist rot und gehört dem Bau-Werkzeug. Er darf die
App-Prüfung nie aufhalten (Prüfpunkt 7). Nicht reparieren, nicht löschen.
