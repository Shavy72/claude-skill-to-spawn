# ADR 0008 – Nachtrag: Taktgeber ohne Gedächtnis und Ereignis-Protokoll (#906, App #1402)

Bezug: ADR 0008 (Bau-Server, Leitstand und Aufseher-Takt), Beschlüsse E3, E18, E19, E20, E22, E23, E50.

## Entscheidung (Nachtrag)

1. **Taktgeber ohne Gedächtnis neben dem Aufseher-Takt:**
   - Der neue Taktgeber (`to_spawn/taktgeber.py`, CLI `to_spawn.py taktgeber <S>`, Cron `to_spawn.py taktgeber-einrichten <S>` mit eigener Marke `# to-spawn-taktgeber #<S>`) läuft auf dem Bau-Server unabhängig vom zustandsbehafteten Aufseher-Takt (`to_spawn/waechter_takt.py`) und benötigt keine wache Aufseher-Session.
   - Der Taktgeber führt **keine eigene Zustandsdatei** (kein Leitstand-Merker, kein JSON-Gedächtnis). Jeder Lauf liest ausschließlich den GitHub-Stand (Unter-Tickets der Spec, Labels, Kommentare „grün auf <Hauptzweig>“ mit vollem 40-Zeichen-Hash und „live“ der Deploy-Schlange) sowie das Urteil des Prüfers.
   - Zwei Läufe hintereinander oder zwei gleichzeitige Läufe erzeugen denselben GitHub-Endstand (Idempotenz und Gleichlauf über OS-Dateisperre je Ticket ohne Zustandsdatei).

2. **Aufgabenverteilung und Regeln an genau einer Stelle:**
   - Alle 4 maschinellen Schließ-Regeln (Commit-Nummer, Belegseite in fester Form auf dem Hauptzweig, kein Test ersetzt, Commit grün auf dem Hauptzweig) liegen ausschließlich im Prüfer (`to_spawn/schliess_pruefer.py` aus #864). Der Taktgeber enthält keine eigene Fertig-Regel.
   - Tickets mit `checkpoint:human` (Konfig `regularien.checkpoint_label`) schließt der Taktgeber nie.
   - Ist ein Ticket grün auf Staging, aber noch nicht live, setzt der Taktgeber das Label `staging:fertig` („Fertig auf Staging, wartet auf Live“), öffnet nicht wieder und schließt erst nach Live-Meldung der Deploy-Schlange (dabei wird das Label wieder entfernt).
   - Wiedereröffnete Tickets weckt der Taktgeber genau einmal über die eine Start-Stelle (`to_spawn/startstelle.py`) mit Sperre je Ticket.
   - Das Urteil des Aufsehers (`to_spawn/anleitung.py`: `AUFSEHER_URTEIL`) beschränkt sich nachgelagert auf Klickweg, Rolle, Bild und Inhalt; bei inhaltlichem Mangel darf der Aufseher weiter wieder öffnen.

3. **Ereignis-Protokoll als gemeinsame Schreib-Tür:**
   - Jede Handlung des Taktgebers (`geschlossen`, `label_gesetzt`, `geweckt`) schreibt eine Zeile über `to_spawn.bau_log.schreibe()` (`wer="taktgeber"`).
   - Der Aufpasser schreibt weiterhin als eigener Schreiber ins Ereignis-Protokoll (kein Vermischen der Rollen, gemeinsame Schreib-Tür nach #902/#903).
