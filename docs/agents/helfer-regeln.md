# Regeln für Helfer der Leit-Session (Skill-Repo `Shavy72/claude-skill-to-spawn`)

Absender: Leit-Session Bau-System auf dem Bau-Server, im Auftrag des Menschen, nicht vom Menschen getippt.

1. Arbeite nur im genannten Worktree (er steht schon auf dem Zweig des Pull-Requests). Kein anderer Ordner, nie `~/.claude`, nie `install.sh`.
2. Temp nur über den Befehl anlegen, nie mit Platzhalter (`*`) löschen, Aufräumen nur über den Befehl (#551):
   - anlegen: `TMPDIR=$(python3 ~/.claude/skills/to-spawn/to_spawn.py helfer-temp anlegen <sitzung>-<issue>)` und dieses `TMPDIR` für jeden Test-Aufruf setzen,
   - aufräumen: `python3 ~/.claude/skills/to-spawn/to_spawn.py helfer-temp aufraeumen <sitzung>-<issue>`,
   - alte Reste: `python3 ~/.claude/skills/to-spawn/to_spawn.py helfer-temp aufraeumen --aelter-als-tage 7`.
   Kein `rm`, kein `mkdir` im Home für Temp-Ordner.
3. Rot-Beweis vor dem Fix: neuer oder geschärfter Test läuft erst rot (Ausgabe merken), dann Fix, dann grün.
4. Nur gezielte Tests (einzelne Dateien, `python3 -m pytest <datei> -q`). Keine volle Suite auf dem Server – die Pflicht-Prüfung ist der Linux-Lauf bei GitHub.
5. Tests nur addieren, nie ersetzen oder abschwächen. Kein Import über den Paketnamen `tests` (Test `tests/test_windows_sammelbar_68.py` bricht sonst); Hilfen aus anderen Testdateien per Pfad laden wie die Nachbar-Tests.
6. Commit mit ausdrücklichem Pathspec (`git commit -m "<typ>: <text> (#<issue>)" -- <dateien>`), vorher `git status --short`. Der Hook hängt `[skip ci]` nicht an – das ist hier richtig, der Lauf bei GitHub soll starten.
7. Hochladen immer mit vollem Zweignamen: `git push origin <zweigname>` (nie ohne Zweignamen, nie `HEAD`, nie erzwungen, kein Rebase eines schon hochgeladenen Zweigs – Folge-Commits nur anhängen).
8. Pull-Request legt die Leit-Session an; nicht ready stellen, nicht mergen, keine Issues anlegen.
9. Keine Hintergrund-Befehle, keine Fragen. Unklares selbst entscheiden und im Bericht begründen.
10. Verlangt der Stop-Hook einen Review: nicht selbst reviewen – die Leit-Session startet den Prüfer nach deinem Commit. Committe und lade hoch, dann Bericht.
11. Bericht höchstens 15 Zeilen: Kopf-Commit (voller SHA), geänderte Dateien, Rot-Beweis (1 Zeile), Ergebnis der gezielten Tests, offene Punkte, real verbrauchte Token grob.
