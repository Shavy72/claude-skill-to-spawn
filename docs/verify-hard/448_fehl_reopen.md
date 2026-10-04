# Belegseite #448 — Aufseher nimmt eigene Fehl-Reopens zurück

Ticket: Shavy72/duoplus-management#448 (Spec #399, E27/E9) · Branch `ticket/448-reopen-ruecknahme` · Basis `7b5e2ba`

## Was gebaut ist
- `capo.umbenannte_tests`: Commit-Vermerk `test-umbenannt: alt -> neu` (auch `→`, `=>`, `pfad::name`) macht eine Umbenennung zu keinem Test-Verlust — nur wenn `-def alt` und `+def neu` in derselben Testdatei stehen. Stille Umbenennung ohne Vermerk bleibt `test_ersetzt` (bestehender Test `test_waechter_213::test_test_ersetzt_oeffnet_wieder`).
- `capo`-Tick, offenes Ticket: hat capo es selbst wieder geöffnet (`erledigt`-Schlüssel), kein Schließen seither, kein Ticket-Commit nach dem Schließen, keine laufende Folge-Runde, Regeln jetzt sauber → `befund.zuruecknehmen` schließt wieder mit Kommentar, Bau-Log-Zeile `ruecknahme`. Höchstens eine Rücknahme je Ticket, weitere Fälle nur gemeldet.
- `aufseher_stand`: Phase „Rücknahme“ in der Kurz-Zeile.

## Akzeptanz
| Häkchen | Beleg |
|---|---|
| Rot-Beweis #415-Fall → nachher wieder zu | `448_rot.txt` (8 failed vorher), `448_gruen.txt` (10 passed) — Test `test_rot_beweis_415_fehl_reopen_wird_zurueckgenommen` |
| Echter roter Befund bleibt offen | Test `test_echter_roter_befund_bleibt_offen` in `tests/test_fehl_reopen_448.py` |
| Rücknahme als Zeile im Aufseher-Stand | Test `test_stand_zeigt_ruecknahme` (Phase „Rücknahme“) |

## Review
- Runde 0: gelb, 6 behalten (2 mittel) — `.review/beleg-ffc7502-sitzung-4df13ec0739f.json`.
- Fixrunde 1: `448_fix1_rot.txt` (4 failed vorher), `448_fix1_gruen.txt` (17 passed), `448_fix1_regression.txt` (99 passed, 1 skipped).
- Ganze Suite: `448_suite.txt`.

## Grenzen
- Kein App-Klickweg (Bau-Werkzeug, E10); Beleg = Test-Ausgabe.
- Ruff auf dem Bau-Server nicht installiert → kein Format-Check gelaufen.
- Schließt und öffnet ein Mensch zwischen zwei Ticks, sieht capo das Schließen nicht; Obergrenze begrenzt auf eine Rücknahme.
- Ausrollen erst per PC-Abgleich (E14/E22), auf den Bau-Server nach Spec 412 (E29).
