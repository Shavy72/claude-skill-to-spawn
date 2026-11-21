---
name: grill-with-docs
description: A relentless interview to sharpen a plan or design, which also creates docs (ADR's and glossary) as we go.
disable-model-invocation: true
---

Call the Skill tool twice, for "grilling" and "domain-modeling".

<!-- LOKALER ZUSATZ (Davids Regel, 2026-10-07) — bei jedem Update/Neu-Import dieses Skills ERHALTEN. -->
## Zusatz: UX-Runde und Architektur-Runde (ergänzt den Ablauf, ändert ihn nicht)

- **UX-Runde:** Hat der Auftrag sichtbare, hörbare oder fühlbare Teile (UI, Töne, Animation, Haptik, Benachrichtigungen), gibt es eigene Grill-Fragen dazu, nicht erst am Ende: Aussehen, Klang, Gefühl, Zustände (leer, lädt, Fehler, fertig), Geräte und Browser, gewünschte Qualitätsstufe. Braucht die Absprache etwas zum Ansehen oder Anhören, schlägt das Grillen einen Prototyp vor (HTML-Werkbank oder Artifact mit Varianten) und klärt damit. Rein technischer Auftrag ohne UX-Anteil: Runde entfällt.
- **Architektur-Runde:** Bei mittleren und größeren Aufträgen 2–3 Fragen zur Codebase-Architektur (Block „Strategisches Design“ in `~/.claude/CLAUDE.md`): Gibt es ein bestehendes Modul, das erweitert wird statt Neues zu bauen? Bleibt es ein tiefes Modul mit schmaler Schnittstelle? Wird nichts verschachtelter? Ziel: strategisch statt taktisch. Kleine Aufträge ohne Modul-Änderung: Runde entfällt.
- Beide Runden enden als normale E-Beschlüsse im Beschluss-Protokoll.
