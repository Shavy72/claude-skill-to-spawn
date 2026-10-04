# #431 Prüfpanel Runde 2 — Befunde (Fixrunde 2 offen)

Stand 92bc5e2, Basis 680f018. Prüfer: general-purpose ×2 (sonnet, geteilt), pr-test-analyzer (sonnet), silent-failure-hunter (opus), code-reviewer (opus), python-reviewer (sonnet). Rot-Probe: keine Befunde (alte Basis scheitert am Import, erwartet). Richter noch nicht gelaufen — Review-Beleg `ticket` nach Fixrunde 2 neu (review-dirigent moment=ticket --repo /home/bau/wt/skill-431 --base 680f018).

## Hoch
1. respawn.py ~404 `_startbefehl` (code-reviewer): neue Session = nacktes `claude --model … --effort …`. Fehlt alles, was bau.py jeder Bau-Session gibt (bau.py ~Z. 484–527, 882–900): `--settings` (permissions.deny AskUserQuestion #321, Stop-Hooks staffel_stop/frage-sperre, skillOverrides), `--mcp-config` + `--strict-mcp-config`, `--session-id` (#236), Umgebung BAU_STAFFEL_*, BAU_HANDOFF_DIRS, TO_SPAWN_START/STAFFEL/EFFORT. Folge: Session ohne Fragesperre → AskUserQuestion hängt (genau das soll respawn beheben). Fix: Bau-Session-Konfiguration NICHT in respawn nachbauen, sondern aus bau.py als gemeinsame Funktion holen (z. B. `bau.sitzung_vorbereiten(N) -> (argv_ohne_prompt, env, aufraeumen)`), respawn nutzt sie. Zwei Varianten skizzieren (§26).

## Mittel
2. respawn.py ~224/538 `tippen()` nicht atomar (SFH + general-purpose): Paste ok, Enter scheitert → Auftrag steht im Eingabefeld, `auftrag_getippt` False → Zeile „alte Session unangetastet“ falsch. Fix: vor `tippen` als „versucht“ markieren, bei Fehler `C-u` senden, Zeile ehrlich.
3. respawn.py ~158 `_tmux`: stderr von tmux geht verloren. Fix: CalledProcessError → RuntimeError mit stderr + Unterbefehl (`from`).
4. respawn.py ~236 `bildschirm()`: CalledProcessError → leerer Text ohne Log, kein TimeoutExpired. Fix: log.warning; Fenster weg → sofort `_Abbruch` „neue Session beendet sich selbst“.
5. respawn.py ~577/644 Schritt e (SFH + code-reviewer + general-purpose): Schwelle `vorher` = Bildschirm + Marker im Prompt; Paste erscheint eingeklappt „[Pasted text …]“ → Schwelle zu hoch, falsches „keine Arbeit“ → Fenster einer arbeitenden Session zu. Fix: nur Zustandsmarker im aktuellen Bildschirm („esc to interrupt“/Spinner), keine Zählung gegen vorher.
6. respawn.py ~660 Schritt d: nur Dateien stabil geprüft, nicht ob der Commit fertig ist → SIGTERM mitten in git commit möglich (index.lock). Fix: vor Beenden alten Bildschirm ruhig (kein ARBEIT_MARKER) abwarten; `git log -1 -- <handoff>` prüfen, sonst in Zeile.

## Niedrig
7. Tür fängt nur Exception: SIGTERM/SIGHUP/KeyboardInterrupt → kein `_aufraeumen`. Fix: Signal-Handler → `_Abbruch` oder `except BaseException` + aufräumen + raise.
8. ~670 `fenster_schliessen` ohne TimeoutExpired → falsche Zeile „alte evtl. noch offen“.
9. ~322 `_ist_claude`: npm-Installation heißt `node` → SCHON_WEG falsch. `node` mit `claude` in cmdline erkennen; OSError außer ProcessLookupError als lebend werten.
10. sessions_stand.py ~266/274 (SFH + code-reviewer): Enkel (bash→node) erben BAU_TICKET; Zuordnung hängt an Reihenfolge. Fix: Vorfahrenkette über `nachkommen()`, für diese Zuordnung nur claude/claude.exe; Block als `_respawn_sessions_zuordnen()` herausziehen (python-reviewer).
11. tests ~604: echter LEBT-Pfad von `TmuxWerkzeug.alte_session_beenden` ohne Test (pr-test-analyzer).
12. python-reviewer: `REMOTE_MARKER: tuple[str, ...]` annotieren; `_signal(sig: signal.Signals)`; `dict[str, Any]`-Zustand in `_warte_dateien`/`_warte_ruhig_bereit` durch kleine Klasse ersetzen; breite `except Exception` auf CalledProcessError/OSError/TimeoutExpired eingrenzen.
