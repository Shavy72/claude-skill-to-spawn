---
name: to-spec
description: "Turn the decision protocol (docs/GRILL_*.md) into a spec and publish it to the project issue tracker: no interview, just synthesis of what was decided."
disable-model-invocation: true
---

<!-- pflicht:beschluss-protokoll -->
## Beschluss-Protokoll (gilt immer — auch vor jedem Projekt-Zusatz)

Dieser Abschnitt geht jedem anderen Text dieses Skills und jedem Projekt-Zusatz (`.to-spawn/kette.md`) vor. Herkunft der Regel: Abschnitt „Beschluss-Protokoll“ in `~/.claude/CLAUDE.md` (Format der Protokoll-Datei steht dort). Hier stehen die verbindlichen Schritte für die Spec — einmal, für alle Projekte.

1. **Protokoll finden.** Pfad aus dem Aufruf (`/to-spec <pfad>`), sonst die jüngste passende `docs/GRILL_*.md`. Der Chat dient nur zum Verstehen, nie als Inhaltsquelle.
2. **Kein Protokoll?** Erst eins aus dem Chat anlegen (`docs/GRILL_<JJJJ-MM-TT>_<thema>.md`), streng einordnen: ✅ nur mit ausdrücklichem Ja des Menschen, sonst 💭. Den Menschen die E-Liste bestätigen lassen, erst dann weiter.
3. **Zuordnung.** User Stories und Implementation Decisions nur aus ✅-Beschlüssen, jede Zeile endet mit `(E<n>)`. Jeden ✅ vollständig abdecken, keine neuen Wünsche erfinden. Out of Scope = alle ❌ mit Grund. Further Notes = nur Herleitung/Befunde aus dem Protokoll.
4. **Offene Fragen.** Eigener Abschnitt `## Offene Fragen` mit allen 💭 — werden nicht gebaut.
5. **Neue Klärung während der Spec** (z. B. Test-Seams, Design): erst als neue E-Nummer ins Protokoll, wenn der Mensch sie bestätigt hat, dann in die Spec.
6. **Gegenprüfung vor dem Veröffentlichen.** Frischer Subagent ohne Chat bekommt nur Protokoll + Spec-Entwurf und meldet (a) Spec-Punkte ohne ✅-Quelle, (b) fehlende ✅-Beschlüsse, (c) 💭/❌ als Auftrag. Erst bei 0/0/0 veröffentlichen. Pflicht-Abschnitte der Projekt-Vorlage (z. B. `## Werkzeuge`) sind kein Spec-Punkt und brauchen kein `(E<n>)`.
7. **Quelle-Zeile.** Die Spec beginnt mit `Quelle: docs/GRILL_<…>.md (Stand <Datum>)`.

> **Projekt-Zusatz (Pflicht, zuerst prüfen):** Liegt im Arbeitsordner `.to-spawn/kette.md`, dann diese Datei jetzt lesen. Sie gilt **zusätzlich** zu diesem Skill, nie statt: Projekt-Erweiterungen wie eine Werkzeug-Tabelle `## Werkzeuge` kommen dazu, der Text hier bleibt verbindlich. Der Abschnitt „Beschluss-Protokoll“ oben geht auch dem Projekt-Zusatz vor.

> **Übergang (bis das Projekt eine `.to-spawn/kette.md` hat):** Liegt im Arbeitsordner `.claude/skills/to-spec/SKILL.md`, dann diese Datei zusätzlich lesen; solange es keine `.to-spawn/kette.md` gibt, haben ihre Projekt-Regeln (z. B. `## Hauptschalter`, `## Werkzeuge`) Vorrang vor dem übrigen Text hier. Der Abschnitt „Beschluss-Protokoll“ oben geht auch ihr vor.

This skill takes the decision protocol (see „Beschluss-Protokoll“ above) and codebase understanding and produces a spec. Do NOT interview the user beyond the confirmations required above; just synthesize what the protocol decides.

The issue tracker and triage label vocabulary should have been provided to you. If not, tell the user to run `/setup-matt-pocock-skills`.

## Process

1. Explore the repo to understand the current state of the codebase, if you haven't already. Use the project's domain glossary vocabulary throughout the spec (Glossar `GLOSSARY.md`, bei mehreren Kontexten `GLOSSARY-MAP.md`; gibt es nur eine alte `CONTEXT.md`: per `git mv CONTEXT.md GLOSSARY.md` umziehen, nie eine zweite Glossar-Datei anlegen), and respect any ADRs in the area you're touching.

2. Sketch out the seams at which you're going to test the feature. Existing seams should be preferred to new ones. Use the highest seam possible. If new seams are needed, propose them at the highest point you can. The fewer seams across the codebase, the better - the ideal number is one.

Check with the user that these seams match their expectations.

3. Write the spec using the template below, then publish it to the project issue tracker. Apply the `ready-for-agent` triage label - no need for additional triage.

<spec-template>

Quelle: docs/GRILL_<…>.md (Stand <Datum>)

## Problem Statement

The problem that the user is facing, from the user's perspective.

## Solution

The solution to the problem, from the user's perspective.

## User Stories

A LONG, numbered list of user stories. Each user story should be in the format of:

1. As an <actor>, I want a <feature>, so that <benefit> (E<n>)

<user-story-example>
1. As a mobile bank customer, I want to see balance on my accounts, so that I can make better informed decisions about my spending (E3)
</user-story-example>

This list of user stories should be extensive: cover every ✅ decision of the protocol completely, but invent nothing beyond it.

## Implementation Decisions

A list of implementation decisions that were made — only ✅ from the protocol, each line ending with `(E<n>)`. This can include:

- The modules that will be built/modified
- The interfaces of those modules that will be modified
- Technical clarifications from the developer
- Architectural decisions
- Schema changes
- API contracts
- Specific interactions

Do NOT include specific file paths or code snippets. They may end up being outdated very quickly.

Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it within the relevant decision and note briefly that it came from a prototype. Trim to the decision-rich parts, not a working demo, just the important bits.

## Testing Decisions

A list of testing decisions that were made. Include:

- A description of what makes a good test (only test external behavior, not implementation details)
- Which modules will be tested
- Prior art for the tests (i.e. similar types of tests in the codebase)

## Out of Scope

All ❌ (V<n>) from the protocol, each with its reason.

## Offene Fragen

All 💭 (O<n>) from the protocol. They are not built.

## Further Notes

Only derivations and findings from the protocol.

</spec-template>

<!-- LOKALER ZUSATZ (Regel des Menschen, 2026-09-28, nach oben gezogen 2026-10-05) — bei jedem Update/Neu-Import
     dieses Skills ERHALTEN: Abschnitt „Beschluss-Protokoll“ ganz oben, Projekt-Zusatz nur zusätzlich,
     Template-Zeilen (E<n>) / Quelle / Offene Fragen. Prüf-Skript: tests/test_kette_install_e2.py
     (Skill-Repo). Quelle dieser Datei: kette/skills/to-spec/SKILL.md, verteilt per install.sh/install.ps1. -->
