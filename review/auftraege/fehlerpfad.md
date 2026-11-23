# Auftrag fehlerpfad (verschluckte Fehler)

Null Toleranz für stille Fehler. Frage je Fänger: Erfährt irgendwer (Log, Aufrufer, Nutzer, Alarm), dass es schiefging?

## Prüfe

- Leere Fänger: `except: pass`, `except Exception: pass`, `catch {}`, `catch (e) {}`.
- Fehler zu Leerwert umgewandelt ohne Kontext: `return None`, `return []`, `.catch(() => [])`, `or ""`, `.get(k, default)`, wo der Default einen echten Fehler verdeckt.
- Gefährliche Fallbacks: Standardwert führt still in einen falschen, teuren oder datenverlierenden Pfad (z. B. leere Liste → „nichts zu tun“ statt Alarm).
- Logging unzureichend: kein Stacktrace (`logger.error` statt `logger.exception`), falsche Stufe (`debug` bei Fehler), fehlende IDs (Konto, Gerät, Lauf).
- Weitergabe: Stacktrace verloren (`raise X` ohne `from e`), generisches Re-raise, Fehlercode zu `True`/`200` umgebogen.
- Async/Nebenläufig: Task-Fehler nie abgeholt, Thread-Ausnahmen verschwinden, Promise ohne Handler.
- Fehlende Behandlung: Netzwerk/Datei/DB ohne Timeout; mehrschrittige Schreibvorgänge ohne Rollback/Transaktion.
- Retry-Schleifen, die nach letztem Versuch still aufgeben.
- Exit-Codes: Skript endet mit 0 trotz Fehler.

## Ignoriere

- Fänger mit Log (inkl. Stacktrace/Kontext) und bewusster Fortsetzung, die im Kommentar begründet ist.
- `.get()` mit Default auf optionalen Konfigwerten mit dokumentiertem Standard.
- Aufräum-Code in `finally`/`__exit__`, der beim Aufräumen Folgefehler bewusst schluckt und loggt.
- Tests, die Ausnahmen absichtlich fangen.

## Ausgabe

Wie `basis.md`. `text`: welcher Fehler wird verschluckt → was sieht man danach nicht. `pruefer` = dein Name.
