# Belegseite #436 — Aufsicht erzwingt Aufseher-Ablösung + Rückkehr auf Opus nach Limit

Ticket: Shavy72/duoplus-management#436 (Spec #399). Repo: Shavy72/claude-skill-to-spawn, Branch `ticket/436-aufsicht-opus`, Basis `8e0a126` (#431, inzwischen in `main`).
Art: Bau-Werkzeug (Skill to-spawn), kein App-Klickweg — Beweis über Tests gegen die echte Aufsicht mit nachgebautem tmux.

## Achse Spec — was gebaut ist
| Häkchen | Wo | Beleg-Test |
|---|---|---|
| Aufsicht misst Kontext des Aufsehers, Grenze aus `~/.claude/smart-zone.json` (Opus / Nicht-Opus) | `to_spawn/waechter_lauf.py` | `tests/test_aufseher_abloesung_436.py` (Kontext unter/über Grenze, Nicht-Opus-Grenze) |
| Ablösung über die respawn-Tür (Handoff-Auftrag ins wache-Pane, Nachfolger startet) | `to_spawn/respawn_aufseher.py::aufseher_abloesen`, `skripte/wache.py` | Ablöse-Tests, Exit 0/1/2 |
| Rückkehr auf Opus nach Limit-Ende | `waechter_lauf.py` (Rückkehr nur bei ruhiger Session, `session_ruhig`) | `test_rueckkehr_auf_opus_wartet_auf_ruhige_session`, `test_limit_pause_auf_ausweich_bleibt_vor_rueckkehr_ab` |
| Gescheiterte Ablösung wird wiederholt (max. 3, Abstand 600 s), danach Bau-Log `blockiert` | Klasse `Abloesung` | `test_abloesung_nach_fehlschlag_wiederholt_mit_abstand`, `test_abloesung_obergrenze_warnung_und_blockiert` |
| Bau-Log-Typ `aufseher_abloesung` | `bau_log` | Ablöse-Tests |

## Achse Beweis — Rot → Grün
| Runde | Rot (vor Fix) | Grün (nach Fix) | Volle Reihe |
|---|---|---|---|
| Bau (`91a750f` → `a0070f0`) | 7 rot — `436_rot.txt` | 7 grün | 1336 grün, 0 rot, 30 übersprungen — `436_gruen.txt` |
| Fixrunde 1 (`bc23500` → `0e03219`) | 5 rot — `436_rot_fixrunde.txt` | 16 grün | 1341 grün, 0 rot, 30 übersprungen — `436_gruen_fixrunde.txt` |
| Fixrunde 2 (`37dcbde` → `98746a2`, `8680e67`) | 6 rot — `436_rot_fixrunde2.txt` | 22 grün | 1347 grün, 0 rot, 30 übersprungen — `436_gruen_fixrunde2.txt` |

Tests nur addiert, keine bestehenden geändert.

## Achse Standards — Prüfpanel
- Ticket-Panel `8e0a126..0e03219`: silent-failure-hunter (opus) 9, code-reviewer (opus) 3, python-reviewer (sonnet) 6 Rohbefunde → Richter (opus): 7 behalten (2 mittel, 5 niedrig), 7 gestrichen. Beleg `.review/beleg-0e03219-sitzung-cc87b59d71e7.json`.
- Alle 7 in Fixrunde 2 behoben. Re-Review (general-purpose, pr-test-analyzer, code-reviewer opus, Rot-Probe): 1 Stil-Befund (Leerzeilen), in `8680e67` behoben. Beleg `.review/beleg-37dcbde-sitzung-5b297364e541.json`.
- Security-Prüfer (Vorsession): 0 Befunde.

## Achse Diagnose — offene Punkte
- ruff/mypy auf dem Bau-Server nicht installiert → Lint/Typprüfung konnte nicht laufen.
- Eingebautes `/code-review` und `/security-review` im Hauptkontext ausgelassen (Kontext > 110k); ersetzt durch code-reviewer (opus) als Subagent.
- Echter Lauf auf dem Bau-Server (echter Aufseher über der Grenze) erst nach Skill-Abgleich am PC möglich.
