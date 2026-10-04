# PLAN #438 — Gelb-Liste: gelber Befund → Folge-Ticket, nur rot öffnet wieder

Quelle: duoplus-management `docs/GRILL_2026-09-30_aufseher.md` E9, E23, V5 · Spec #399 · Ursache #389 Punkt 7.

## Heute (Rot-Zustand)
- `skripte/wache.py` PROMPT Schritt 4: jeder Mangel an der Belegseite → `gh issue reopen` (auch gelbe Review-Hinweise).
- `to_spawn/capo.py:_wieder_oeffnen` öffnet bei Maschinen-Verstößen wieder (Commit/Beweis/Tests/VPS) — das sind rote Befunde, bleibt so.
- `skripte/spec_stand.py` zeigt nur die Unter-Tickets der Spec, keine gelben Folge-Tickets.

## Design (Kästen und Türen)
Neues tiefes Modul `to_spawn/befund.py` — kennt als einziges die Regel „gelb → Folge-Ticket + Manifest, rot → wieder öffnen“.
- Tür 1: `melde(repo, gh_repo, spec, ticket, stufe, text, dry_run) -> list[str]` — Aufrufer sagt nur, wie schlimm; Modul entscheidet die Aktion.
- Tür 2: `gelbe_folgen(repo, spec) -> list[GelbeFolge]` — Liste für die Gesamtabnahme (Nummer, Herkunfts-Ticket, Titel, Status gebaut/verschoben/offen).
- Versteckt: Issue-Text des Folge-Tickets, Manifest-Format (`gelb_von`, `schaetzung_k`, `umfang`), Label `verschoben`, Reopen-Kommentar.
- CLI: `to_spawn.py befund --spec S --ticket N --stufe gelb|rot --text "…" [--dry-run]` → der Aufseher-Prompt ruft nur noch das.
- capo nutzt `befund.wieder_oeffnen` statt eigener Kopie (Wissen an einem Ort, Ausgabezeilen unverändert).
- `spec_stand.py` hängt Abschnitt „Gelbe Folge-Tickets (Gesamtabnahme)“ an.

Variante B (verworfen): Stufe als Feld in `capo.Verstoss` + gelb-Zweig in capo. Grund: capo-Regeln sind alle maschinell rot; gelb kommt nur aus der Belegseiten-Prüfung des Aufsehers — die Regel gehört nicht in den Tick-Kern.

## Entscheidungen
- Folge-Ticket wird kein Sub-Issue der Spec (sonst blockiert ein verschobenes Ticket „Spec fertig“); Verbindung über Manifest `gelb_von` + Body „Teil von Spec #S, gelber Befund aus #N“.
- Status: Issue zu = gebaut · offen + Label `verschoben` = verschoben (David) · sonst offen.
- Manifest-Eintrag erfüllt die Pflichtfelder (`schaetzung_k` 30, `umfang` = Befundtext).

## Akzeptanz
1. Rot-Beweis: Test „gelber Befund“ → vorher Reopen, nachher Folge-Ticket + Manifest-Eintrag, Ticket bleibt zu.
2. Roter Befund öffnet weiterhin wieder (Test).
3. Spec-Stand zeigt gelbe Folge-Tickets mit Status gebaut/verschoben.
