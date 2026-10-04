# #431 Ticket-Review Runde 3 — Befunde (Fixrunde 3 offen)

Stand fea167e, Basis 680f018. Prüfer: general-purpose ×2 (sonnet, geteilt), pr-test-analyzer (sonnet), silent-failure-hunter (opus), code-reviewer (opus), python-reviewer (sonnet), Rot-Probe. Richter (opus): alle 6 behalten. Beleg: `.review/beleg-fea167e-ticket-ae0b778641c5.json` (rot, Modus hinweis). Nach Fixrunde 3 neuer Lauf `moment=ticket --base 680f018`, `fix_runde` 1.

## Mittel
4. respawn.py ~893 `_aufraeumen`: Die neue bau.py schreibt `.to-spawn/sessions/<N>.json` (bau.py `session_id_setzen`, ~Z. 628/998) schon vor Schritt c. Bricht respawn danach ab, schließt `_aufraeumen` nur das Fenster, die Datei behält die verworfene ID → Aufpasser (`to_spawn/aufpasser.py` ~1653/1663 `fehlendes_fenster`/`resume_aus_datei`) setzt später die falsche Session fort. Fix: alten Inhalt vor Schritt b merken, beim Aufräumen zurückschreiben (bzw. löschen, wenn vorher keine Datei da war). Rot-Test zuerst.

## Niedrig
0. Rot-Probe: `tests/test_sessions_stand_respawn_431.py:34` kann nicht rot werden — `skripte/sessions_stand.py` ist wieder identisch zur Basis (Sonderzuordnung entfernt). Entscheidung nötig: Datei löschen (kein geänderter Code, kein Test nötig) oder als Absicherung in eine bestehende sessions_stand-Testdatei verschieben. Vorschlag: löschen, Begründung ins Bau-Log.
1. bau.py ~1046: `main()` mit `--ohne-prompt` ungetestet (dry-run ohne `<prompt>`, prompt-runde1.txt leer, `mit_prompt=True` ab Runde 2). Fix: bau.main-Test `--dry-run --ohne-prompt` + Staffel-Lauf mit gefaktem `starte_session`.
2. respawn.py ~637–641: Signal während `_abbruch_ergebnis`/`_aufraeumen` nach normalem Abbruch → `_Abbruch` verlässt `abloesen()` gegen den Docstring. Fix: Flag „räumt auf“ vor dem Aufräumen setzen, Handler merkt dann nur.
3. respawn.py ~818 `_alte_ruhig`: `bildschirm()` liefert bei tmux-Fehler `""` → gilt als ruhig; Wartezeit + Hinweis in der Ergebniszeile fehlen. Fix: unlesbar = nicht ruhig (z. B. `None` unterscheiden), Hinweis in Zeile.
5. respawn.py:15 Modul-Kommentar Schritt d „alte Session unangetastet“ widerspricht `_aufraeumen` (tippt WEITER_AUFTRAG). Fix: Text korrigieren.

## Hoch (Echtlauf 5, rot)
6. respawn.py `BEREIT_MAX_S = 120` fest, unabhängig von `--warte-max`. Die neue bau.py wartet unter Last an der Speicher-Sperre („wartet auf Speicher … N Claude-Sessions (Obergrenze 12)“, 60-s-Takte, auch mit `--sofort`) → respawn bricht nach 120 s ab (Exit 1, sauber aufgeräumt). Beleg `431_echtlauf5_*` (Commit 8546611). Entscheidung (Bau-Log): Bereit-Frist hängt an `--warte-max`; solange der neue Bildschirm „wartet auf Speicher“ zeigt, zählt das nicht als Fehler, Ergebniszeile nennt die Wartezeit. Rot-Test zuerst.
   Echtlauf-Gerüst: wt-431 braucht für den Lauf einen `.venv`-Link und ein Manifest `spec-9399.json` für Ticket 9431 (Echtlauf-5-Agent hat beides angelegt und wieder gelöscht) — Ablauf in `431_echtlauf5_log.txt`/Handoff.
