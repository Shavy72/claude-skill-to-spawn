# Auftrag kosten (bezahlte Ressourcen)

Frage je Pfad: Kann dieser Code Geld verbrennen, wenn er abbricht, hängt oder doppelt läuft?

## Prüfe

- Boot/Start einer bezahlten Ressource (Cloud-Phone, VM, Container, GPU) ohne Shutdown/Stop im `finally` (bzw. `try/finally`, Context-Manager, `trap EXIT`).
- Shutdown in `except` statt `finally` → bei Erfolg oder anderem Fehler läuft Ressource weiter.
- Frühes `return`/`continue`/`break` zwischen Boot und Shutdown.
- Shutdown-Fehler verschluckt (Ressource bleibt an, niemand merkt es).
- Proxy Pflicht: Netzwerkzugriff für Konten/Geräte ohne Proxy oder mit stillem Fallback auf Direktverbindung.
- Retry/Schleife um kostenpflichtige API ohne Obergrenze, Backoff oder Budget.
- Doppelstart: fehlende Sperre, sodass zwei Läufe dieselbe Ressource booten.
- Timeouts fehlen an Stellen, die eine bezahlte Ressource offen halten.
- Kostenpflichtige Modell-/Bild-Aufrufe in Schleifen ohne Cache oder Deckel.

Projekt-eigene Regeln: Liegt im geprüften Repo `.review/regeln.md`, gelten dessen Regeln zu Kosten zusätzlich (z. B. teure Ressourcen in `finally` freigeben, Abschalten nur über das Projekt-Modul, erlaubte Ausgabe-Wege).

## Ignoriere

- Kostenlose lokale Ressourcen (Dateien, lokale Prozesse ohne Abrechnung).
- Ressourcen mit Auto-Stop durch die Plattform, wenn im Code oder Kommentar belegt.
- Mikro-Optimierungen von Rechenzeit.

## Ausgabe

Wie `basis.md`. Kosten-Leck mit belegtem Pfad = `hoch`. `pruefer` = dein Name.
