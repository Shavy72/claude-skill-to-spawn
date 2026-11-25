---
name: to-tickets
description: Break a spec (built from the decision protocol docs/GRILL_*.md) into a set of tracer-bullet tickets, each declaring its blocking edges, published to the configured tracker (edges as text in one file per ticket locally, or attached to the spec with `to_spawn.py anhaengen` on a real tracker).
disable-model-invocation: true
---

# To Tickets

<!-- pflicht:beschluss-protokoll -->
## Beschluss-Protokoll (gilt immer — auch vor jedem Projekt-Zusatz)

Dieser Abschnitt geht jedem anderen Text dieses Skills und jedem Projekt-Zusatz (`.to-spawn/kette.md`) vor. Herkunft der Regel: Abschnitt „Beschluss-Protokoll“ in `~/.claude/CLAUDE.md`. Kette: Grillen schreibt `docs/GRILL_*.md` → `/to-spec` baut die Spec nur daraus (Kopfzeile `Quelle: docs/GRILL_<…>.md`, jede Zeile endet mit `(E<n>)`) → hier entstehen Tickets nur aus der Spec.

1. **Quelle ist die Spec, nie der Chat.** Spec aus dem Aufruf (Issue-Nummer, URL oder Pfad) ganz lesen, samt Kommentaren. Fehlen Quelle-Zeile oder `(E<n>)`-Zuordnung: nicht schneiden, sondern dem Nutzer melden — erst `/to-spec` aus dem Protokoll.
2. **Nur ✅.** Tickets nur aus Spec-Punkten mit `(E<n>)`. Jeder Ticket-Text nennt seine E-Nummern (Abschnitt `## Beschlüsse`, auch Prefactoring- und Abnahme-Tickets: die E-Nummern, denen sie dienen).
3. **Nie 💭/❌.** Offene Fragen (💭) und Out of Scope (❌) werden keine Tickets und keine Akzeptanzkriterien.
4. **Abdeckung vor dem Veröffentlichen.** Jede E-Nummer der Spec steckt in mindestens einem Ticket; kein Ticket ohne E-Nummer. Lücke → Schnitt nachbessern, nicht still weglassen.

> **Projekt-Zusatz (Pflicht, zuerst prüfen):** Liegt im Arbeitsordner `.to-spawn/kette.md`, dann diese Datei jetzt lesen. Sie gilt **zusätzlich** zu diesem Skill, nie statt: Projekt-Regeln wie Klickweg-Rollen oder eine Werkzeug-Tabelle kommen dazu, der Text hier bleibt verbindlich. Der Abschnitt „Beschluss-Protokoll“ oben geht auch dem Projekt-Zusatz vor.

> **Übergang (bis das Projekt eine `.to-spawn/kette.md` hat):** Liegt im Arbeitsordner `.claude/skills/to-tickets/SKILL.md`, dann diese Datei zusätzlich lesen; solange es keine `.to-spawn/kette.md` gibt, haben ihre Projekt-Regeln Vorrang vor dem übrigen Text hier. Der Abschnitt „Beschluss-Protokoll“ oben und die Schluss-Prüfung (Schritt 6) gelten trotzdem.

Break a spec into a set of **tickets**: tracer-bullet vertical slices, each declaring the tickets that **block** it.

The issue tracker and triage label vocabulary should have been provided to you. If not, tell the user to run `/setup-matt-pocock-skills`.

Alle Befehle unten laufen im Arbeitsordner des Projekts; `to_spawn.py` liegt im installierten Skill `to-spawn` (`~/.claude/skills/to-spawn/to_spawn.py`, unter Windows derselbe Pfad im Benutzerordner).

## Process

### 0. Lernstoff lesen

Vor dem Schneiden `python ~/.claude/skills/to-spawn/to_spawn.py lernstoff --letzte 30` ausführen: Schätzung → Ist der zuletzt aktiven Tickets plus Faustregeln (mittlerer Faktor Ist/Schätzung, Sessions je Umfang-Art, Tickets mit Staffel > 1). Beim Schnitt anwenden: Schätzung × Faktor muss unter `staffel.grenze_k` (`.to-spawn/config.json`) bleiben, sonst das Ticket teilen. `anhaengen` weist ein zu großes Ticket später ohnehin ab.

### 1. Gather context

Work from the spec the user passes as an argument (a spec path, an issue number or URL): fetch it and read its full body and comments. The conversation is only for understanding, never a source of tickets (see „Beschluss-Protokoll“ above).

### 2. Explore the codebase (optional)

If you have not already explored the codebase, do so to understand the current state of the code. Ticket titles and descriptions should use the project's domain glossary vocabulary (Glossar `GLOSSARY.md`, bei mehreren Kontexten `GLOSSARY-MAP.md`; gibt es nur eine alte `CONTEXT.md`: per `git mv CONTEXT.md GLOSSARY.md` umziehen, nie eine zweite Glossar-Datei anlegen), and respect ADRs in the area you're touching.

Look for opportunities to prefactor the code to make the implementation easier. "Make the change easy, then make the easy change."

**Kontext-Paket je Ticket.** Beim Erkunden pro Ticket die konkreten Stellen als `Pfad:Start-Ende` festhalten. Sie kommen als Block `## Kontext-Paket` in den Ticket-Text und als `--datei` in den Manifest-Eintrag (Schritt 5).

### 3. Draft vertical slices

Break the work into **tracer bullet** tickets.

<vertical-slice-rules>

- Each slice cuts a narrow but COMPLETE path through every layer (schema, API, UI, tests): vertical, NOT a horizontal slice of one layer
- A completed slice is demoable or verifiable on its own
- Each slice is sized to fit in a single fresh context window (Schätzung × Faktor aus Schritt 0 unter `staffel.grenze_k`)
- Any prefactoring should be done first

</vertical-slice-rules>

Give each ticket its **blocking edges**: the other tickets that must complete before it can start. A ticket with no blockers can start immediately.

**Abnahme-Ticket.** Mindestens ein Ticket der Spec ist die Abnahme durch den Nutzer und trägt das Label `checkpoint:human` (anderer Name nur über `regularien.checkpoint_label` in `.to-spawn/config.json`). Es steht am Kettenende: blockiert von den Tickets, die es abnimmt, nie selbst Blocker. Sein Text sagt, auf welchen Kommentar es wartet.

**Wide refactors are the exception to vertical slicing.** A **wide refactor** is one mechanical change (rename a column, retype a shared symbol) whose **blast radius** fans across the whole codebase, so a single edit breaks thousands of call sites at once and no vertical slice can land green. Don't force it into a tracer bullet; sequence it as **expand–contract**. First expand: add the new form beside the old so nothing breaks. Then migrate the call sites over in batches sized by blast radius (per package, per directory), each batch its own ticket blocked by the expand, keeping CI green batch to batch because the old form still exists. Finally contract: delete the old form once no caller remains, in a ticket blocked by every migrate batch. When even the batches can't stay green alone, keep the sequence but let them share an integration branch that all block a final integrate-and-verify ticket; green is promised only there.

### 4. Quiz the user

Present the proposed breakdown as a numbered list. For each ticket, show:

- **Title**: short descriptive name
- **Blocked by**: which other tickets (if any) must complete first
- **What it delivers**: the end-to-end behaviour this ticket makes work
- **Schätzung**: `schaetzung_k` (Tausend Token) und Umfang in einem Satz

Ask the user:

- Does the granularity feel right? (too coarse / too fine)
- Are the blocking edges correct: does each ticket only depend on tickets that genuinely gate it?
- Should any tickets be merged or split further?

Iterate until the user approves the breakdown. Vor der Zustimmung nichts veröffentlichen: ein angehängtes Ticket ist startbereit.

### 5. Publish the tickets to the configured tracker

Publish the approved tickets. **How** depends on the tracker `/setup-matt-pocock-skills` configured; the tickets are the same either way, only the shape of the blocking edges changes:

- **Local files** → write one file per ticket under `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01` in dependency order (blockers first). Each file's "Blocked by" lists the numbers/titles it depends on. Use the per-ticket file template below: one ticket per file, never a single combined file.
- **A real issue tracker (GitHub, …)** → je Ticket in Blocker-Reihenfolge (Blocker zuerst, Abnahme-Ticket zuletzt):
  1. Issue im Repo dieses Arbeitsordners anlegen (Vorlage unten, `ready-for-agent`, beim Abnahme-Ticket zusätzlich `checkpoint:human`). `## Blocked by` nennt nur echte Issue-Nummern, nie Entwurfs-Nummern (#1, #2 …).
  2. Ticket an die Spec hängen — **die eine Tür für Kanten und Manifest**, nie Kanten von Hand setzen:

     ```
     python ~/.claude/skills/to-spawn/to_spawn.py anhaengen <S> <N> --blockiert-von <B1,B2> --schaetzung <K> --umfang "<ein Satz>" --datei <pfad:a-b> --datei <pfad:a-b>
     ```

     Erst mit `--trocken` ansehen, dann ohne. `anhaengen` setzt die nativen Blocker-Kanten, schreibt den Manifest-Eintrag (Ordner aus `pfade.manifeste` in `.to-spawn/config.json`, fehlt der Schlüssel: Vorgabe des Skills; Ticket-Schlüssel = echte Issue-Nummer, `files` = Kontext-Paket), hängt die Kante vor das offene Abnahme-Ticket seiner Kette (bei mehreren Abnahmen ggf. `--abnahme` angeben) und macht das Issue zuletzt zum Unter-Issue der Spec. Ein zweiter Aufruf ändert nichts; Exit ≠ 0 heißt: nichts geschrieben, Fehlerzeile beheben, neu aufrufen.
  3. Das Abnahme-Ticket zuletzt anhängen, mit `--blockiert-von` aller Tickets, die es abnimmt.

Work the **frontier**: any ticket whose blockers are all done. For a purely linear chain that means top to bottom.

Do NOT close the parent issue or edit its text — `anhaengen` only links each ticket to it as Unter-Issue.

<local-ticket-template>

# <NN>: <Ticket title>

**What to build:** the end-to-end behaviour this ticket makes work, from the user's perspective, not a layer-by-layer implementation list.

**Blocked by:** the numbers/titles of the tickets that gate this one, or "None (can start immediately)".

**Beschlüsse:** the spec's E-Nummern this ticket implements, e.g. E3, E7.

**Status:** ready-for-agent

- [ ] Acceptance criterion 1
- [ ] Acceptance criterion 2

</local-ticket-template>

<issue-template>

## Parent

A reference to the parent issue on the tracker (if the source was an existing issue, otherwise omit this section).

## What to build

The end-to-end behaviour this ticket makes work, from the user's perspective, not layer-by-layer implementation.

## Beschlüsse

The spec's E-Nummern this ticket implements, e.g. E3, E7 (only ✅ from the spec — never 💭/❌).

## Kontext-Paket (nur das lesen)

- pfad/zur/datei.py:120-180   (was dort steht)

Sonst nichts lesen; Unbekanntes per Grep, nie per Voll-Read.

## Werkzeuge

Skills/MCPs/Docs aus der Spec-Tabelle, nur was dieses Ticket wirklich braucht (gibt die Spec keine Tabelle: Abschnitt weglassen).

## Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2

## Blocked by

- A reference to each blocking ticket (echte Issue-Nummer), or "None (can start immediately)".

</issue-template>

In either form, avoid specific file paths or code snippets outside `## Kontext-Paket`: they go stale fast. Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it and note briefly that it came from a prototype. Trim to the decision-rich parts, not a working demo, just the important bits.

### 6. Schluss-Prüfung

`python ~/.claude/skills/to-spawn/to_spawn.py regularien <S>` ausführen — muss Exit 0 liefern, sonst laut Fehlerzeile nachbessern (meist per `anhaengen`), bevor an den Nutzer übergeben wird. Ohne grüne Prüfung verweigert `/to-spawn` den Start.

Dann pro Ticket eine Zeile an den Nutzer, in Reihenfolge der Blocker-Tiefe (blockerfreie zuerst):

```
#<N>  <Titel>  (blockiert von: …)
```

und als Startbefehl `/to-spawn <S>` — der Rest läuft als Domino.

<!-- LOKALER ZUSATZ — bei jedem Update/Neu-Import dieses Skills ERHALTEN: Abschnitt „Beschluss-Protokoll“
     ganz oben, Projekt-Zusatz nur zusätzlich, Kanten und Manifest nur über `to_spawn.py anhaengen`,
     Schluss mit `to_spawn.py regularien <S>`. Prüf-Skript: tests/test_kette_to_tickets_e2.py (Skill-Repo).
     Quelle dieser Datei: kette/skills/to-tickets/SKILL.md, verteilt per install.sh/install.ps1. -->
