---
name: review-dirigent
description: "Dirigent für Code-Reviews: Stufe per Skript, Prüfer parallel, Richter, Beleg. Trigger: review, prüfen lassen, Review fällig, review-dirigent, nach Subagent-Code, vor Commit, Ticket fertig, code review, review my changes."
---

# review-dirigent

Ein Einstieg für jedes Review. SSOT für Stufen, Signale, Prüfer, Beleg-Format und CLI: `~/.claude/review/VERTRAG.md`. Zahlen nur aus `~/.claude/review/config.json`. Auftragsvorlagen: `~/.claude/review/auftraege/*.md`.

Aufruf: `review-dirigent <moment> [--staged | --base <ref> | --files <f> …]`. `moment` ∈ `sitzung`, `subagent`, `commit`, `ticket`, `deploy`, `bug`, `ui`, `plan`, `manuell`. Fehlt er → `manuell`.

Hook-Anweisung `Review fällig (<moment>, Stufe <stufe>): Skill review-dirigent ausführen mit moment=<moment> und --files <…>.` → direkt verarbeiten: `moment` und `--files` unverändert an `review_route.py` durchreichen. Nicht nachfragen.

## 1. Route holen

```bash
python ~/.claude/review/review_route.py --moment <m> [--repo <pfad>] [--staged | --base <ref> | --files <f> …] [--log-ausloeser]
```

- `--files` aus der Hook-Anweisung (Moment `sitzung`); `--staged` bei `commit`; `--base <ref>` bei `ticket`/`deploy` (Ticket-Basis bzw. `origin/master`/`origin/main`); sonst ohne.
- `--log-ausloeser` nur bei manuellem Aufruf; kam die Anweisung vom Hook, hat er schon geloggt.
- `--files` aus mehreren Repos → je Repo ein eigener Lauf mit `--repo` und nur dessen Dateien.
- Exit 2 → Eingabe kaputt, Fehlerzeile an Hauptsession, fertig.
- `stufe` = `keine` → 1 Zeile „Review: keine Code-Änderung“, fertig.
- `beleg_gueltig` = true → kein neuer Lauf: `review_beleg.py kurz --datei <beleg>` ausgeben, fertig.
- Merke: `stufe`, `sha`, `fingerprint`, `dateien`, `pruefer[]`, `builtin[]`, `richter`, `modus`, Startzeit.

## 2. Prüfer parallel starten

Alle Einträge aus `pruefer[]` in EINER Nachricht als parallele Agent-Aufrufe. Nie nacheinander.

| `typ` | `subagent_type` | Hinweis |
|---|---|---|
| `agent` | `name` aus JSON, falls `~/.claude/agents/<name>.md` existiert | sonst `general-purpose` + Auftrag (Lücke im Beleg-Text nennen) |
| `skill` (oberflaeche) | `general-purpose` | je Linse ein eigener Agent: `impeccable` (Modus audit), `web-design-guidelines`, `accessibility` — Anweisung „Lies und befolge Skill `<linse>` nur als Prüfer“ |
| `builtin` | — | siehe Schritt 3 |

`model:` = `modell` aus JSON (Prüfer `sonnet`). Nie ohne `model:`.

Rot-Probe (bei Signal `tests` oder geänderten Code-Dateien), parallel zu den Prüfern im selben Zug:

```bash
python ~/.claude/review/rot_probe.py --repo <pfad> [--base <ref>] --files <dateien>
```

Ausgabe `{"befunde": […], "hinweis": "…"}`. Befunde (`pruefer: rot-probe`) mechanisch bewiesen → OHNE Richter direkt in den Beleg, `status: behalten`; der Richter bekommt sie nicht. `hinweis` (Timeout, Umgebung, Import-Rot, ignorierte Nicht-Python-Dateien) in den Beleg-Text, Lauf nie deswegen abbrechen.

Auftrag: nur der PFAD `~/.claude/review/auftraege/basis.md` + je Eintrag in `auftraege[]` der Pfad `~/.claude/review/auftraege/<name>.md` — Prüfer liest selbst, nie Volltext in den Prompt einfügen (Ausnahme: die Projektregeln aus `pruefer[].regeln` stehen wörtlich im Prompt). Bei `spec` zusätzlich die Spec-Quelle (Schritt 2a).

> 8 Dateien oder > 800 Diff-Zeilen für einen Prüfer → Dirigent schneidet selbst in 2 disjunkte Teile (je eigener Prüfer gleichen Typs, disjunkte Dateien), Befunde vor dem Richter zusammenlegen (Schritt 4.1).

Pflicht-Gerüst jedes Prüfer-Prompts:

```
Schätzung: ~<N>k Token. Lese-Budget: Diff + <Dateiliste>, je Datei nur betroffene Funktionen (offset/limit), max ~<M>k.
Rolle: Prüfer <name> im Review <moment>/<stufe>. Du änderst NIE Dateien (kein Edit/Write/NotebookEdit, keine Git-Schreibbefehle). Kein git stash/checkout im geteilten Baum. Keine Hintergrund-Befehle.
Diff holen: <exakter git-diff-Befehl wie review_route> -- <dateien des Prüfers>
Dateien: <dateien aus JSON>
Datei-Größe: <Einträge aus datei_groesse[] für diese Dateien als JSON; Zeile weglassen, wenn leer>
Auftrag: lies und befolge ~/.claude/review/auftraege/basis.md + ~/.claude/review/auftraege/<name>.md
Projektregeln (gelten zusätzlich): <regeln[] des Prüfers aus dem Route-JSON, wörtlich je Zeile; Zeile weglassen, wenn leer>
Ausgabe: NUR eine JSON-Liste von Befunden
[{"datei","zeile","schwere","konfidenz","pruefer","text"}], schwere ∈ hoch|mittel|niedrig,
konfidenz 0–100, pruefer = "<name>". Leere Liste [] ist gültig und erwünscht, wenn nichts belegbar ist.
```

Schätzung: klein ~30k, mittel ~50k, gross ~70k je Prüfer (Ziel/hart = `subagent` laut ~/.claude/smart-zone.json). Agent-Gate rechnet Schätzung × gelernten Faktor je `subagent_type` (`~/.claude/data/smart-zone-lernstoff.md`); liegt das über `subagent.max_schaetzung_k`, blockt der Hook den Start.

### 2a. Spec-Quelle für Auftrag `spec`

Reihenfolge: Ticket-/Issue-Nummer aus Auftrag oder Commit-Nachrichten (`git log <base>..HEAD --oneline`) → Handoff-Datei `.claude-handoff.md` → Auftragstext der bauenden Session. Keine Quelle → Auftrag `spec` mit Hinweis „keine Spec, nur Überbau prüfen“.

## 3. Eingebaute Befehle

Aus `builtin[]`, parallel zu den Prüfern:

- `/code-review <stufe>` (ab mittel): Skill-Tool, `skill: "code-review"`, `args: "<stufe>"` (Stufe aus JSON, z. B. `high`). Nie `--fix`.
- `/security-review` (bei Signal `sicherheit`): Skill-Tool, `skill: "security-review"` = eingebauter Befehl (lokaler Checklisten-Skill heißt seit 2026-09-24 `security-checkliste`, verdeckt nichts mehr). Zusätzlich bei Stufe gross + sicherheit: Skill `sentry-security-review` als zweiter Blick (nur Befunde mit belegtem Datenfluss).
- Hauptkontext > 120k Token → `/code-review` und `/security-review` auslassen (laufen im Hauptkontext, nicht im Subagent), im Beleg vermerken: „ausgelassen: Hauptkontext <N>k".
- Nicht erreichbar oder Fehler → im Beleg-Text vermerken, Lauf nicht abbrechen.
- Ausgabe der Builtins in Befunde nach Vertrag §5 übersetzen (`pruefer`: `builtin-code-review` bzw. `builtin-security-review`); ohne Datei+Zeile → verwerfen.
- `/code-review ultra` NIE selbst starten. Bei `gross` + hartem Signal: 1 Zeile an den Nutzer „Vorschlag: `/code-review ultra` (Cloud, kostet) — starten? Ja/Nein“.

## 4. Richter (nur wenn `richter` = true)

1. Rohbefunde sammeln (bei geteilten Prüfern aus Schritt 2 hier zusammenlegen), Nicht-JSON-Antworten → Prüfer als `befunde_roh: 0` + Vermerk.
2. Doppelte zusammenführen (3-Pass-Muster): gleiche Datei, Zeile ±3, gleicher Kern → ein Befund, höchste Schwere, höchste Konfidenz, `pruefer` = alle Namen mit `+`.
3. Frischer Subagent `general-purpose`, `model: opus`. Prompt = `auftraege/richter.md` + je Befund nur `id, datei, zeile, schwere, text` + Pfad der Code-Stelle. NICHT mitgeben: Konfidenz, Begründung, Prüfer-Name, Prüfer-Meinung.
4. Richter antwortet je `id`: `behalten` oder `gestrichen` + Grund (≤ 1 Satz), optional korrigierte `schwere`/`zeile`/`konfidenz`.
5. Ohne Richter (klein): Befunde unter `konfidenz.verwerfen_unter` → `gestrichen`, Rest `behalten`.

## 5. Beleg

Entwurf nach Vertrag §5 in `<scratchpad oder $TMPDIR>/review-entwurf-<sha>-<moment>.json` schreiben: `version, repo, moment, stufe, modus, sha, fingerprint, dateien, zeit, dauer_s, token_schaetzung, pruefer[], richter, befunde[], fix_runde`. `status` NICHT setzen (rechnet das Skript). `richter` ohne Richter-Lauf: `null`.
`repo` und `diff_modus` (`{staged, base}`) aus review_route-JSON unverändert übernehmen (ohne `repo` landet der Beleg im Repo des cwd); fehlt `diff_modus` bei commit/ticket/deploy, trägt das Skript keine `datei_hashes` nach (nur fingerprint gilt).

```bash
python ~/.claude/review/review_beleg.py schreiben --datei <entwurf> --repo <repo aus review_route-JSON>
python ~/.claude/review/review_beleg.py kurz --datei <beleg-pfad aus Ausgabe>
```

An die Hauptsession geht NUR die Kurzfassung (≤ 15 Zeilen). Rohbefunde, Richter-Gründe bleiben im Beleg.

## 6. Fix-Schleife

- `gruen` → fertig. `gelb` → Hinweise mitgeben, nichts blockiert.
- `rot` → behaltene Befunde (Datei:Zeile, Schwere, Text) als Aufgabenliste an die bauende Session bzw. den Subagenten. Der Prüfer fixt nie.
- Nach Fix: neuer Lauf ab Schritt 1, `fix_runde` = vorige + 1 (aus letztem Beleg desselben Moments).
- `fix_runde` ≥ `max_fix_runden` und weiter `rot` → Stopp, an den Nutzer: „Mensch nötig: Review <moment> nach <n> Fix-Runden rot. Beleg: <pfad>.“
- `modus` = `hinweis`: Befunde nur melden, nichts erzwingen, keine Fix-Schleife starten. Beleg trotzdem immer schreiben — ohne Beleg blockt der Stop-Hook erneut.

## 7. Moment-Varianten

| moment | Besonderheit |
|---|---|
| `sitzung` | Haupt-Moment, vom Stop-Hook am Zugende. `--files` aus der Anweisung. Beleg ist Pflicht (auch im Modus hinweis), sonst blockt der Stop-Hook erneut. Kurzfassung an den Nutzer, dann Zug beenden. |
| `subagent` | Stufe meist klein, max 2 Prüfer, kein Richter. Befunde gehen als nächste Anweisung an den Subagenten, nicht an den Nutzer. |
| `commit` | `--staged`. Gültiger Beleg für gleichen fingerprint → kein neuer Lauf. |
| `ticket` | `--base <ticket-basis>`. Nach grünem Beleg Skill `verify-hard` (liest Beleg als Achse „Standards“). |
| `deploy` | `--base origin/<hauptzweig>`. Belege aller enthaltenen Tickets prüfen (`review_beleg.py pruefen`); fehlt einer → voller Lauf. `/security-review` bei Signal sicherheit. |
| `bug` | Zuerst Skill `diagnosing-bugs` (Rot-Test zuerst); nach 2 Fehlversuchen Agent `deep-debugger` (`model: opus`). `silent-failure-hunter` auf betroffene Dateien. |
| `ui` | Drei Linsen parallel + Screenshot im Viewport 390×844 (Browser-Werkzeug laut Browser-Doktrin). Danach Skill `screenshot-critique`: frischer Subagent sieht nur Screenshots + Ziel, nie den Code. Nutzer-Ablauf → Live-Klickweg bleibt Pflicht. |
| `plan` | Kein Diff-Review. Skill `grill-me`; großer Plan zusätzlich `premortem`; UI-Plan `user-flow-lens` (Modus A). Ergebnis Hinweis, der Nutzer entscheidet. |
| `manuell` | Wie Route sagt; Moment im Beleg `manuell`. |

## 8. Nicht tun

- Kein Prüfer und kein Richter ändert Dateien. Fixes nur die bauende Session.
- Keine Prüfer-Meinung, Begründung oder Konfidenz an den Richter.
- Keine Befunde ohne Datei + Zeile.
- Sicherheitsbefund nur mit belegtem Datenfluss (Quelle → Senke), sonst streichen.
- Kein neuer Lauf bei `beleg_gueltig` = true.
- Kein `--fix`, kein `/code-review ultra`, kein `/simplify` im Review.
- Kein Hintergrund-Befehl, keine Rohbefunde in die Hauptsession.
- `REVIEW_AUS=1` gesetzt → nichts starten, 1 Zeile „Review aus (REVIEW_AUS)“.
