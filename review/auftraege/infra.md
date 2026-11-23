# Auftrag infra (Container, Abhängigkeiten, Build)

## Prüfe

- Dockerfile: Basis-Image ohne Versions-Tag (`latest`), Secrets in `ENV`/`ARG`/Layern, Laufen als root ohne Grund, Cache-Reihenfolge (Code vor Abhängigkeiten kopiert), fehlendes `.dockerignore`-Gegenstück für neue große Ordner.
- Abhängigkeiten: neue Pakete ohne Pin/Obergrenze, Paket nur für wenige Zeilen Code, Versionssprung mit Breaking Change, Lockfile nicht mitgeändert.
- Compose: Ports öffentlich statt `127.0.0.1`, Volume-Pfade geändert (Datenverlust), `restart`-Politik fehlt, Healthcheck entfernt, Umgebungsvariable umbenannt ohne `.env`-Anpassung.
- `pyproject.toml`/`package.json`: Skript-Befehle geändert, die CI/Deploy aufruft; `engines`/`requires-python` passt nicht zur Laufzeit.
- Build bricht auf Zielplattform (Linux-Server) wegen Windows-Pfaden, CRLF oder fehlender Systembibliothek.
- Änderung braucht Neuaufbau (Rebuild) — ist das im Ablauf vermerkt?

Projekt-eigene Regeln: Liegt im geprüften Repo `.review/regeln.md`, gelten dessen Regeln zu Infrastruktur zusätzlich (z. B. welche Änderung welchen Deploy-Schalter braucht; ohne Hinweis im Commit/Ticket als `mittel` melden).

## Ignoriere

- Reihenfolge von Paketen in Listen, Kommentare, Formatierung.
- Patch-Updates ohne bekannte Verhaltensänderung.
- Image-Größe ohne konkretes Problem.

## Ausgabe

Wie `basis.md`. `pruefer` = dein Name.
