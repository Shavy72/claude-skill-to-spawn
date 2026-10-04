# Belegseite #438 — Gelb-Liste: gelber Befund → Folge-Ticket, nur rot öffnet wieder

Quelle: duoplus-management `docs/GRILL_2026-09-30_aufseher.md` E9, E23 · Spec #399 · Plan `docs/PLAN_438_gelb_liste.md`.
Kein App-Klickweg (Bau-Werkzeug, keine Nutzer-Rolle) — Beleg = Test-Ausgabe + Terminal-Ablauf (E10).

## Akzeptanz

| Häkchen | Beweis |
|---|---|
| Rot-Beweis: gelber Befund → vorher Reopen, nachher Folge-Ticket + Manifest-Eintrag, Ticket bleibt zu | `438_rot.txt` (rot: Modul fehlte, alter Prompt-Schritt 4 mit `gh issue reopen` zitiert) → `test_gelber_befund_legt_folge_ticket_an_statt_wieder_zu_oeffnen`, `test_wache_prompt_schritt_4_nutzt_befund` grün (`438_gruen.txt`) |
| Roter Befund öffnet weiterhin wieder | `test_roter_befund_oeffnet_wieder`, `test_capo_nutzt_befund_wieder_oeffnen` grün |
| Gesamtabnahme-Liste zeigt gelbe Folge-Tickets mit Status gebaut/verschoben | `test_gelbe_folgen_status`, `test_spec_stand_zeigt_gelbe_folgen`, `test_fix7_not_planned_ist_verworfen` grün |

## Läufe
- Rot vor Bau: `438_rot.txt` · Grün: `438_gruen.txt` (13) · Regression: `438_regression.txt` (620 passed, 17 skipped).
- Fixrunde nach Prüfpanel: `438_fix_rot.txt` (11 neue Tests rot) → `438_fix_gruen.txt` (25 passed) → `438_fix_regression.txt` (156 passed).

## Terminal-Ablauf (2026-10-04, im duoplus-Repo, Spec 399, Probe-Modus)
```
$ to_spawn.py befund --spec 399 --ticket 438 --stufe gelb --text "Zusatz-Test für Nebenpfad fehlt" --dry-run
#438 [Probe] gelb → würde Folge-Ticket „Gelb aus #438: Zusatz-Test für Nebenpfad fehlt“ anlegen
exit=0
$ to_spawn.py befund --spec 399 --ticket 438 --stufe rot --text "Akzeptanz-Häkchen 2 ohne Beleg" --dry-run
#438 [Probe] würde wieder öffnen
exit=0
```
`skripte/spec_stand.py 399` läuft mit dem neuen Code durch (Spec 399 hat noch keine gelben Folgen, Abschnitt bleibt leer).

## Prüfpanel
silent-failure-hunter + code-reviewer (opus), python-reviewer (sonnet) + review-dirigent (Beleg `.review/beleg-872b8c5-sitzung-775831ba16d5.json`, gelb, 4 behalten). Fixrunde behob alle 4 behaltenen Befunde plus 5 gestrichene: atomares Manifest-Schreiben, Doppel-Schutz, Exit-Code über ok-Flag, „nicht geplant“ = verworfen, Beispiele gelb/rot im Prompt.

## Konnte nicht testen
Echtes `gh issue create` gegen GitHub (absichtlich nur Probe-Modus, um kein Schein-Folge-Ticket in Spec 399 anzulegen). Der Live-Weg folgt beim ersten echten gelben Befund des Aufsehers.
