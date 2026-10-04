# #431 Ticket-Review Runde 5 — Befunde (Fixrunde 5 offen)

Stand 527487a, Basis origin/main. Prüfer: general-purpose ×2 (sonnet, geteilt: respawn.py / Rest-Code), pr-test-analyzer ×2 (sonnet, Tests A/B), silent-failure-hunter (opus), code-reviewer (opus), python-reviewer (sonnet), Rot-Probe (0). Richter (opus): 5 behalten, 3 gestrichen. Beleg `.review/beleg-527487a-ticket-*.json` (gelb, Modus hinweis, fix_runde 2). Fixrunde 4 (Befunde 1–4, 7 aus runde4 + Sitzungs-Review) ist erledigt.

## Mittel
2. respawn.py ~911 `_halb_gestartet`: unlesbare Fensterliste → None, nur Log; `_aufraeumen` meldet dann kein evtl. offenes „bau N neu“. Fix: Zustand „unbekannt“ und Zeile „Fenster „bau N neu“ evtl. offen (Fensterliste unlesbar), Handarbeit nötig“ (wie `_fenster_offen`: unklar = offen). Rot-Test zuerst.

## Niedrig
1. Kein Test für `aliase/respawn/SKILL.md` (Schritte a–e, `to_spawn.py respawn`) und Verweis in `SKILL.md` ~63. Fix: kleiner Test (vgl. tests/test_umzug_205.py:175-189).
4. respawn.py ~286 `committet`: git-Fehler → False → Zeile „nicht committet“. Fix: drei Zustände, bei Fehler „Commit-Stand nicht prüfbar (<Fehler>)“. Rot-Test zuerst.
5. respawn.py ~633 `_abbruch_ergebnis` (Zweig nach `beenden_begonnen`): `stand.hinweise` fehlen. Fix: wie `_aufraeumen` (~954) anhängen. Rot-Test zuerst.
6. respawn.py ~686 `_pruefe_duplikat`: prüft nicht, ob unter `alt.pane_pid` Claude läuft → Auftrag landet in Shell. Fix: `prozessbaum.ist_claude` über `prozessbaum.baum(alt.pane_pid)`; ohne Claude Exit 3 „alte Session fehlt“. Rot-Test zuerst.

## Gestrichen (Richter)
3. `_schirm` unlesbar → leer (gewollt, Z. 434 dokumentiert). 7. ARBEIT_HINWEIS vs. aufpasser.ARBEITS_MARKER (andere Erkennung). 8. Handoff-Schema doppelt zu bau_loop (heute gleich).
