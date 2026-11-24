# Auftrag shell (Bash, PowerShell, Deploy-Skripte)

## Prüfe

- Bash: `set -euo pipefail` fehlt in Skripten, die schreiben, deployen oder löschen.
- Quoting: ungequotete Variablen (`$x`, `$@`, `$(…)`) bei Pfaden/Argumenten → Wortaufteilung, Glob.
- `rm -rf "$VAR/…"` ohne Prüfung, dass `VAR` gesetzt und nicht leer ist (`${VAR:?}`).
- Fehlercodes verschluckt: `cmd || true`, `cmd | tee` ohne `pipefail`, `if cmd; then` bei dem `-e` nicht greift.
- `cd` ohne `|| exit` bzw. ohne `set -e`.
- Temp-Dateien ohne `mktemp` und ohne `trap … EXIT`-Aufräumen.
- Windows/Linux: CRLF-Zeilenenden, Backslash-Pfade, `C:/` vs. `/c/`, fehlende Befehle (`sed -i` GNU vs. BSD, `readlink -f`), Git-Bash-Pfadumwandlung bei `/opt/…`-Argumenten.
- PowerShell: `$ErrorActionPreference = 'Stop'` fehlt; externe Befehle ohne `$LASTEXITCODE`-Prüfung; `-File` vs. `-Command`.
- SSH/Remote: Variablen lokal statt remote expandiert (falsches Quoting um den Remote-Befehl).
- Secrets in Befehlszeilen, Logs oder `set -x`-Ausgabe.
- Idempotenz: zweiter Lauf zerstört Zustand (z. B. doppeltes Anhängen, Überschreiben von Backups).
- Rollback-Pfad fehlt oder wird bei Fehler nicht erreicht.

Projekt-eigene Regeln: Abschnitt `## shell` der privaten `~/.claude/review/regeln.md` (VERTRAG.md §2a) gilt zu Shell und Deploy zusätzlich, Zeilen `Falls Repo <name>: …` nur in diesem Repo (z. B. Deploy nur über das Projekt-Skript, kein Force-Push, Parallelität nicht still senken).

## Ignoriere

- `[[ ]]` vs. `[ ]`, Einrückung, `function f` vs. `f()`.
- Fehlendes `pipefail` in reinen Lese-Einzeilern ohne Seiteneffekt.
- shellcheck-Hinweise ohne konkreten Fehlerfall.

## Ausgabe

Wie `basis.md`. `pruefer` = dein Name.
