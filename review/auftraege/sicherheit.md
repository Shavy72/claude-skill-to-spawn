# Auftrag sicherheit

Grundregel: Ein Sicherheitsbefund braucht einen belegten Datenfluss **Quelle → Senke** mit Datei:Zeile für beide Enden. Ohne Datenfluss → nicht melden.

## Prüfe (Checkliste)

1. Secrets: Schlüssel, Token, Passwörter im Code, in Logs, Fehlermeldungen, Commits, Frontend-Bundles; `.env` nicht in `.gitignore`.
2. Eingabe: Fremddaten (Request, Webhook, Datei, Umgebung, LLM-Ausgabe) ohne Validierung an Trust-Boundaries.
3. Injection: SQL per String-Bau, Shell mit `shell=True`/Interpolation, Pfad-Traversal (`../`), Template-/Code-Ausführung (`eval`, `exec`, `pickle.loads` auf Fremddaten).
4. Authentifizierung/Autorisierung: Endpunkt ohne Auth-Prüfung, Rollenprüfung nur im Frontend, IDOR (fremde ID im Pfad ohne Besitzprüfung), Token ohne Ablauf, Vergleich ohne `hmac.compare_digest`.
5. XSS: Fremddaten ungeescaped in HTML (`innerHTML`, `|safe`, `dangerouslySetInnerHTML`).
6. CSRF: zustandsändernde Cookie-Session-Endpunkte ohne Token/SameSite.
7. Rate-Limit: Login, Passwort-Reset, teure oder kostenpflichtige Endpunkte ohne Begrenzung.
8. Datenpreisgabe: Stacktraces/interne Pfade in Antworten, personenbezogene Daten in Logs, zu breite API-Antworten.
9. Abhängigkeiten: neue Pakete ohne Pin, bekannte verwundbare Versionen, Lockfile fehlt.
10. Transport/Krypto: `verify=False`, HTTP statt HTTPS für Secrets, eigene Krypto, `random` für Tokens.

Projekt-eigene Regeln: Abschnitt `## sicherheit` der privaten `~/.claude/review/regeln.md` (VERTRAG.md §2a) gilt zu Secrets, Rollen und Token zusätzlich, Zeilen `Falls Repo <name>: …` nur in diesem Repo (z. B. woher Secrets kommen, welche Rolle was sehen darf, Token nur serverseitig).

## Ignoriere

- Muster ohne erreichbare Fremdeingabe (interne Konstante, Admin-CLI lokal).
- `Math.random`/`random` für Jitter, Animation, Stichproben.
- `eval`/Plugin-Laden in ausdrücklich dafür gebauten Oberflächen.
- Test-Secrets in Fixtures, offensichtliche Platzhalter (`xxx`, `changeme` in `.example`).
- „Könnte theoretisch…“ ohne konkreten Angreifer-Weg.

## Ausgabe

Wie `basis.md`. `datei`/`zeile` = Senke. `text` MUSS enthalten: `Quelle <datei>:<zeile> → Senke`, Angreifer-Aktion, Folge. Ohne Quelle → Befund weglassen. `pruefer` = dein Name.
