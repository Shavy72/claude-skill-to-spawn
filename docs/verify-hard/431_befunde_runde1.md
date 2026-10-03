# #431 Prüfpanel Runde 1 — Befunde (Fixrunde offen)

Quelle: silent-failure-hunter (opus), python-reviewer (sonnet), code-reviewer (opus, siehe unten). Volltext-JSON in den Session-Notizen; hier verdichtet, Zeilen = Stand be86550.

## Echtlauf 1 (Wegwerf spec-9399 / bau 9431, 2026-10-04 01:07)
- Ergebnis: Exit 1 „alte Session (Pane-PID …) nicht beendet“. Ursache: Fenster wurde direkt mit `claude` gestartet → Pane-PID IST claude; `_nachkommen()` zählt die Pane-PID selbst nicht mit. Fix: Pane-PID selbst prüfen (bau.py-Fenster: claude ist Kind, Wegwerf: claude ist Pane).
- a) ok: Handoff + START-Datei wurden von der alten Session geschrieben (01:08).
- b) ok: neues Fenster mit Opus 5.5 ohne Prompt-Argument.
- e) ok: Start-Prompt kam an, neue Session arbeitete (Bildschirm 431_echtlauf_neu_bildschirm.txt).
- c) UNKLAR: im Verlauf des neuen Fensters keine Spur von `/remote-control` → nach dem Tippen Bestätigung auf dem Bildschirm prüfen (Text „Remote Control“ o. ä.), sonst Exit 1. Ursache klären (zu früh getippt? Slash-Menü schluckt Enter?).

## Hoch
1. respawn.py ~408/398: Ausnahme nach Schritt b (tippen, read_text, bildschirm-Timeout) → neues Fenster bleibt offen, zwei Sessions, jeder Folge-respawn Exit 3. Fix: b–e in try/except, bei Abbruch `fenster_schliessen(neu)`. (SFH + PY)
2. respawn.py ~394: Handoff-Auftrag sagt „Danach nichts mehr tun“ → bei jedem Abbruch nach a arbeitet KEINE Session mehr, Zeile sagt aber „alte läuft weiter“. Fix: bei Abbruch nach a der alten Session „Ablösung abgebrochen — weiterarbeiten“ tippen, Zeile ehrlich formulieren. (SFH)
3. respawn.py ~426/428: G4-Beweis `arbeitet()` = jede Bildschirmänderung, Paste-Echo reicht schon. Fix: auf echtes Arbeitszeichen warten (Spinner „esc to interrupt“, „✻“, „●“), Paste-Echo zählt nicht. (SFH + PY)

## Mittel
4. ~430: G4 scheitert → neues Fenster bleibt offen mit Prompt, zwei Sessions. Fix: neues Fenster schließen oder Zeile „zwei Sessions offen, Handarbeit nötig“.
5. ~437 / alte_session_beenden: drei False-Fälle gleich behandelt. Kein claude-Prozess (schon selbst beendet) = Erfolg; nach 30 s SIGKILL; pgrep-Returncode prüfen.
6. ~444: fenster_umbenennen/fenster_schliessen(alt) Fehler → Zustand in Zeile nennen, Schließen per fenster_liste nachprüfen.
7. ~116 fenster_liste: jeder CalledProcessError = []. Nur „no server running“ → [], sonst loggen + weiterwerfen.
8. ~183 Race: nach SIGTERM an claude kann bau.py eine Folge-Runde starten, bevor das alte Fenster zu ist. Fix: alten Pane-Prozessbaum (bau.py) beenden bzw. Fenster schließen statt nur claude.
9. ~346 `_abloesen` ~100 Zeilen / 8 Parameter → Schritt 0 als `_pruefe_duplikat`, c–e als Funktionen, Parameter in frozen Dataclass `Auftrag`.

## Niedrig
10. tippen/alte_session_beenden umgehen Naht `schlafen()`/`jetzt()`; Magic Numbers 1/30 als Konstanten.
11. `_warte(bedingung: Any)` → `Callable[[], bool]`; Docstrings für private Helfer.
12. fenster_liste filtert `pane_index == "0"` → bei pane-base-index 1 leer; `#{pane_active}` nehmen.
13. Dateiname-Datum aus `seit` (Mitternacht) → per Glob `HANDOFF_*_{N}.md` + mtime suchen.
14. `_frisch` nur size>0 → stabile Größe abwarten, leerer Prompt nach strip() = Exit 2.
15. äußeres except fängt AttributeError/TypeError nicht → letztes `except Exception` mit log.exception + Ergebniszeile.
