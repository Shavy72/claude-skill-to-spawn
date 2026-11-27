# Smart-Zone-Nachmessung (Ticket #875 / App #1391)

- **Zeitraum:** 2026-11-23 bis 2026-11-27 (5 Tage)
- **Modell-Filter:** `opus`
- **14-Tage-Frist:** NICHT erfüllt (4 von 14 Tagen seit 2026-11-23)
- **Stichprobengröße:** 289 `opus`-Sessions (von 440 Sessions gesamt, >= 5 Runden; 11 mit Compaction)
- **Datenquellen:** Nur Bau-Server (~/.claude/projects und ~/.claude/data/staffel); lokale Windows-Laptop-Transcripts fehlen auf dem Bau-Server; 14-Tage-Frist läuft erst ab 03.12.2026 (lokal) bzw. 07.12.2026 (Bau-Server) ab.

## Empfehlung an den Nutzer

- **Urteil (`abwarten`):** 14-Tage-Frist noch nicht abgelaufen (frühestens ab 2026-12-07); Nachmessung nach Fristablauf wiederholen.

## Messgröße A: Gepaarte echte Tool-Fehlerquote (250–330k vs. 100–175k)

Schadens-Schwelle: Quote oben > 1.5× Quote unten bei >= 300 Tool-Aufrufen oben (ohne Hook-Blocks und Nutzer-Ablehnungen).

| Kennzahl | Wert |
|---|---|
| Gepaarte Sessions (Tools in beiden Bändern) | 0 |
| Unten (100–175k): echte Fehler / Tools | 0 / 0 (–) |
| Oben (250–330k): echte Fehler / Tools | 0 / 0 (–) |
| Verhältnis (oben / unten) | – |
| Session-Median unten vs. oben | – vs. – (schlechter oben in 0/0) |
| Stichprobe >= 300 Tools oben | nein |
| Schaden erkannt | nein |

## Messgröße B: Code-Re-Edit-Anteil ab 250k (<= 10 Runden)

Schadens-Schwelle: > 60 % Code-Edits (ohne `.md`, Handoff, Memory), die dieselbe Datei innerhalb von <= 10 Runden erneut anfassen.

| Kennzahl | Wert |
|---|---|
| Code-Edits ab 250k | 0 |
| Davon Re-Edits <= 10 Runden | 0 (–) |
| Markdown-/Handoff-Edits ab 250k (ausgeklammert) | 0 |
| Edits ab 250k gesamt | 0 |
| Dateiarten ab 250k | {} |
| Schaden erkannt | nein |

## Nebenkennzahlen: Übergaben je Woche & Cache-Read

| Kennzahl | Wert |
|---|---|
| Staffel-Übergaben im Zeitraum (gesamt gefunden) | 3 (57) |
| Übergaben je Woche (Referenz vorher: ~7/Woche) | 4.2 |
| Staffel-Status im Zeitraum | {"wartet_subagenten": 2, "übernommen": 1} |
| Interaktive `opus`-Sessions | 166 |
| Median `cache_read` je interaktiver Session | 4.03 M |

## Kontext-Bänder (opus)

| Band | Sessions | Runden | Tools | Fehler gesamt | Fehler echt (±95 %-KI) | Kategorien (rej/hook/str/exit/sonst) | Code-Edits (Re-Edit) |
|---|---|---|---|---|---|---|---|
| 0-100k | 289 | 3785 | 3782 | 4.1 % | 2.6 % ± 0.5 | 0/56/0/88/11 | 180 (22 %) |
| 100-175k | 167 | 5174 | 4695 | 3.2 % | 2.1 % ± 0.4 | 0/52/1/81/17 | 54 (19 %) |
| 175-250k | 40 | 879 | 816 | 3.2 % | 1.3 % ± 0.8 | 0/15/0/10/1 | 6 (17 %) |
| 250-300k | 0 | 0 | 0 | – | – | 0/0/0/0/0 | 0 (–) |
| 300-330k | 0 | 0 | 0 | – | – | 0/0/0/0/0 | 0 (–) |
| >=330k | 0 | 0 | 0 | – | – | 0/0/0/0/0 | 0 (–) |

## Ehrliche Aussagekraft & Hinweise

- 14-Tage-Frist nicht erfüllt (Umstellung: 2026-11-23, Stichtag: 2026-11-27, vergangen: 4 Tage).
- Messgröße A unter Stichproben-Schwelle: 0 von 300 nötigen Tool-Aufrufen in 250–330k.
- Messgröße B: keine Code-Edits ab 250k im Zeitraum.
- 11 Sessions mit Compaction im Zeitraum (Spitzen-Kontext potenziell unterschätzt).
