# #431 Ticket-Review Runde 4 — Befunde (Fixrunde 4 offen)

Stand 6e4178a, Basis origin/main (nach Merge eb8a7b6; 680f018 enthielte jetzt Upstream-Code). Prüfer: general-purpose ×2 (sonnet, geteilt), pr-test-analyzer (sonnet), silent-failure-hunter (opus), code-reviewer (opus), python-reviewer (sonnet), Rot-Probe (0). Richter (opus): 5 behalten, 2 gestrichen. Beleg: `.review/beleg-6e4178a-ticket-abee2ef693b6.json` (gelb, Modus hinweis). Fixrunde 3 (Befunde 0–8 aus runde3 + Merge-Befund 7) ist erledigt.

## Mittel
1. `to_spawn/respawn.py` hat 1057 Zeilen (Grenze 1000). Zusammen mit Befund 2 lösen: Prozess-/tmux-Helfer (~Z. 195–440) aus respawn.py herausziehen.

## Niedrig
2. respawn.py ~333–440: zweites Gerüst zum Beenden eines Pane-Prozessbaums (`alte_session_beenden`, `_nachkommen`, `_signal`, `_lebt`, `_ist_claude`, tmux-Wrapper) neben `to_spawn/aufpasser.py` (`prozess_baum` ~450, `_prozess_lebt` ~483, `_prozesse_beenden` ~1188, `_fenster_schliessen` ~1213 „einzige Stelle, die ein Fenster schließt“). Fix: gemeinsames Modul (z. B. `to_spawn/prozessbaum.py`), das aufpasser und respawn nutzen — oder im Bau-Log begründen, warum nicht. Vorschlag: gemeinsames Modul, löst Befund 1 gleich mit. Vorsicht: aufpasser ist Cron-kritisch, Verhalten dort unverändert lassen (Tests test_aufpasser_* grün).
3. respawn.py ~210/779/1029: `fenster_starten` scheitert per TimeoutExpired (30 s) → TmuxFehler, obwohl tmux das Fenster „bau N neu“ evtl. angelegt hat → `stand.neu` None → `_aufraeumen` schließt nichts und legt Sessions-Datei nicht zurück. Fix: bei `stand.neu is None` per `fenster_liste` nach `name_neu` suchen und schließen; Sicherung immer zurücklegen, wenn `stand.sessions` gesetzt. Rot-Test zuerst.
4. respawn.py ~935–941 `_alte_unruhe`: `unlesbar` bleibt nach einem einzigen `None` dauerhaft True → Hinweis „nicht lesbar“ statt „arbeitete nach 120 s noch“. Fix: nur letzten Zustand werten bzw. Flag bei lesbarem Bildschirm zurücksetzen. Rot-Test zuerst.
7. `tests/test_respawn_431_runde2.py:387` test_b12: `pytest.skip` ohne ruff/uvx steht vor dem AST-Teil (Z. 397–411). Fix: AST-Teil vor den Skip ziehen bzw. in eigenen Test.

## Gestrichen (Richter)
5. SPEICHER_SCHIRM im Test fest eingetippt — Erkennung nutzt `speicher.WARTE_TEXT` (SSOT), Test würde rot statt still.
6. `capo.bau_startzeile` ohne direkten Test — indirekt über `tests/test_respawn_431.py:259-270`.
