# Auftrag python

## Prüfe

- Veränderliche Standardargumente (`def f(x=[])`), geteilter Zustand zwischen Aufrufen.
- Ausnahmen: bare `except`, `except Exception: pass`, zu breite Fänge um Kernlogik, `raise` ohne `from` bei Umwandlung.
- Ressourcen ohne `with` (Dateien, Locks, DB-Verbindungen, HTTP-Sessions).
- Async: blockierende Aufrufe in `async def` (`time.sleep`, `requests`), vergessenes `await`, fehlende Timeouts.
- Typ-Hints stimmen nicht mit Rückgabe überein (z. B. `-> dict` gibt `None` zurück); `Optional` ohne Prüfung genutzt.
- `subprocess` mit `shell=True` und Fremdeingabe; String-Liste statt Liste.
- Zeit: naive `datetime` gemischt mit aware; `datetime.now()` ohne Zeitzone in Vergleichen.
- Pfade: String-Konkatenation statt `pathlib`, wenn Windows und Linux laufen müssen.
- `print` statt `logging` in Produktionscode; Logging ohne Kontext beim Fehler.
- Iteration: Liste ändern während Iteration, Integer-Division/Rundung bei Geldbeträgen.
- Importzeit-Seiteneffekte (Netzwerk, DB, Dateischreiben beim Import).

Projekt-eigene Regeln: Liegt im geprüften Repo `.review/regeln.md`, gelten dessen Regeln zu Python-Code zusätzlich (z. B. SSOT-Module nicht kopieren oder umgehen, Status-Wechsel nur über eine Funktion, Aufräumen in `finally`).

## Ignoriere

- Formatierung, Import-Reihenfolge, Zeilenlänge (ruff/black).
- Fehlende Type Hints in Tests und privaten Einzeilern.
- `Any` an Grenzen zu untypisierten Bibliotheken.
- `assert` in Tests.
- f-String vs. `%` in `logging` (nur melden bei teurer Auswertung in Schleife).

## Ausgabe

Wie `basis.md`. `pruefer` = dein Name.
