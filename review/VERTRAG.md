# Review-System — Schnittstellen-Vertrag (SSOT)

Gilt für alle Bausteine: Skripte (`~/.claude/review/`), Skill `review-dirigent`, Hooks (`~/.claude/hooks/review/`).
Konzept: Entwurfs-Dokument im Projekt, aus dem das Werkzeug stammt (nicht Teil dieses Repos).
Wer diesen Vertrag ändert, passt alle Bausteine an.

## 1. Ordner

```
~/.claude/review/
  VERTRAG.md            dieses Dokument
  config.standard.json  Schalter + Schwellen (einzige Quelle für Zahlen), ausgeliefert
  review_route.py       Diff → Stufe, Signale, Prüfer-Liste (JSON auf stdout)
  review_beleg.py       Beleg schreiben / prüfen (Gate-Baustein)
  review_bericht.py     Zählbericht Soll vs. Ist
  rot_probe.py          Rot-Probe: neue Tests gegen den alten Code (rot?)
  auftraege/            Auftragsvorlagen je Prüfer + richter.md (Markdown)
  tests/                pytest, Fixtures mit Beispiel-Diffs
  ausloeser.jsonl       Auslöser-Log (Soll), eine Zeile je Auslösung
~/.claude/skills/review-dirigent/SKILL.md
~/.claude/hooks/review/  Hook-Skripte (Python, stdlib only)
<repo>/.review/          Belege je Repo (nie committen; globales gitignore)
```

Optional und privat, nie im Repo: `~/.claude/review/regeln.md` — Projektregeln (§2a). `~/.claude/review/namen_privat.txt` — Namensliste für die Öffentlich-Prüfung des Skill-Repos (Ort definiert in `tests/test_review_quelle_329.py`).

Nur Python-Standardbibliothek. Python 3.11+. Type Hints, `logging`, kein `print` außer für die JSON-/Tabellen-Ausgabe auf stdout, kein bare except. Windows (Git Bash) UND Linux (Bau-Server) müssen laufen: Pfade mit `pathlib`, `git` per `subprocess` mit Liste.

## 2. Config (config.standard.json + lokale config.json)

Ausgeliefert wird nur `config.standard.json`; der Installer aktualisiert sie. Lokale Einstellungen stehen in `~/.claude/review/config.json` (optional, nie ausgeliefert; Pfad überschreibbar per `REVIEW_CONFIG`). Der Installer entschlackt sie, damit sie künftige Standard-Änderungen nicht still verdeckt: gleicht sie einem je ausgelieferten Stand (aktueller Standard + git-Verlauf), kommt sie nach `_alt/`; sonst fallen nur Schlüssel weg, deren Wert dem aktuellen Standard gleicht (Original vorher in `_alt/`). Beim Lesen gewinnt jeder lokale Schlüssel, fehlende kommen aus dem Standard (Mischung nur auf oberster Ebene).

```json
{
  "version": 1,
  "modus": "hinweis",
  "hinweis_bis": "2026-10-08",
  "stufen": {
    "klein":  {"max_zeilen": 50,  "max_dateien": 3},
    "mittel": {"max_zeilen": 400, "max_dateien": 15}
  },
  "konfidenz": {"verwerfen_unter": 70, "rot_ab": 85},
  "max_fix_runden": 2,
  "modelle": {"pruefer": "sonnet", "richter": "opus"},
  "code_review_stufe": {"klein": "low", "mittel": "high", "gross": "xhigh"},
  "datei_groesse": {"grenze": 1000, "zuwachs": 150},
  "projekt_woerter": {"datenbank": ["<modul>"], "kosten": ["<dienst>"]},
  "ignorieren": ["*.md", "*.txt", "docs/**", ".review/**", "graphify-out/**", "archive/**", "*.lock"]
}
```

`modus`: `hinweis` = nichts blockiert, alles protokolliert. `scharf` = Gates blockieren bei ROT oder fehlendem Beleg.

`projekt_woerter` (optional): je Signal-ID zusätzliche Pfad-/Zeilen-Wörter eines Projekts (Modul- und Dienstnamen). Einzige Stelle dafür — der Code kennt nur allgemeine Wörter (#329 P2). Unbekannte Signal-ID → Eingabefehler.

## 2a. Projektregeln (`~/.claude/review/regeln.md`, privat)

Projekt-eigene Regeln (SSOT-Module, Status-Wege, Deploy-Wege …) stehen nie im öffentlichen Werkzeug, sondern in `~/.claude/review/regeln.md`: Markdown, je Abschnitt `## <auftrag>` (z. B. `## python`), eine Zeile `Falls Repo <name>: …` gilt nur in diesem Repo. Die Route gibt je Prüfer die geltenden Zeilen seiner Abschnitte (`basis` + `auftraege[]`) als `pruefer[].regeln` mit; `Falls Repo <name>` gilt, wenn `<name>` der Ordner- oder `origin`-Name des Repos ist. Der Dirigent reicht `regeln` wörtlich in den Prüfer-Auftrag weiter; die Vorlagen nennen den Abschnitt nur noch zur Orientierung. Nie ausgeliefert, nie versioniert (`review/.gitignore`), der Installer überschreibt sie nie.

Übernahme: Vor dem Ersetzen einer lokal geänderten Vorlage (weder neue noch zuletzt installierte Fassung) schreibt der Installer deren eigene Zeilen in den passenden Abschnitt, ebenso aus früher gesicherten Vorlagen unter `_alt/review-*/auftraege`, diese nur für Abschnitte, die `regeln.md` noch nicht hat (Dubletten je Abschnitt, auch als `Falls Repo <name>: <derselbe Text>`; ein vorhandener Abschnitt wächst, Handänderungen bleiben; `to_spawn/review_hook.py`). Die lokale Config wird erst nach dem Einspielen entschlackt. Fehlt die Datei, trägt die Route `"projektregeln": {"datei": …, "vorhanden": false}`, die Kurzzeile endet mit `· Projektregeln fehlen (…)` und es gibt eine Warnung; bei Stufe `keine` ist das Feld `null`.

## 3. Stufen und Signale

Stufen: `keine` (nur ignorierte Dateien), `klein`, `mittel`, `gross`.
- klein: Zeilen ≤ klein.max_zeilen UND Dateien ≤ klein.max_dateien UND kein hartes Signal.
- mittel: Zeilen ≤ mittel.max_zeilen UND Dateien ≤ mittel.max_dateien UND kein hartes Signal.
- gross: sonst, oder irgendein hartes Signal.
- Blast-Radius: wenn `graphify-out/` im Repo existiert und `graphify affected` > 10 Knoten meldet → eine Stufe höher (max gross). Fehlt graphify → überspringen, kein Fehler.

Signal-IDs (Klasse normal/hart) → zusätzliche Prüfer:

| id | Klasse | Treffer (Pfad oder hinzugefügte Diff-Zeilen) | Prüfer |
|---|---|---|---|
| python | normal | `*.py` | python-reviewer |
| typescript | normal | `*.ts`, `*.tsx` | typescript-reviewer |
| react | normal | `*.tsx`, `*.jsx`, Import von `react` | react-reviewer |
| javascript | normal | `*.js`, `*.mjs`, `*.cjs` | code-reviewer |
| shell | hart | `*.sh`, `*.ps1`, Pfad enthält `deploy` | code-reviewer (Auftrag shell) |
| n8n | normal | `*.json` mit `"nodes"` und `"connections"` | code-reviewer (Auftrag n8n) |
| fehlerpfad | normal | hinzugefügt: `except`, `catch`, `\|\| `, `?? `, `.get(`, `or ""`, `default=` | silent-failure-hunter |
| sicherheit | hart | Pfad/Zeile: `auth`, `login`, `token`, `bearer`, `secret`, `password`, `.env`, `permission`, `rolle`, `role` (case-insensitiv) | security-reviewer + builtin `/security-review` |
| datenbank | hart | `migration`, `*.sql`, `set_status`, `ALTER TABLE`, `CREATE TABLE`, `projekt_woerter.datenbank` | database-reviewer + silent-failure-hunter |
| kosten | hart | `shutdown`, `boot`, `proxy`, `power_off`, `projekt_woerter.kosten` | silent-failure-hunter (Auftrag kosten) |
| oberflaeche | normal | `web/static/**`, `*.html`, `*.css`, `*.tsx`, `*.jsx`, `*.vue` | oberflaeche (Skill-Linsen impeccable-audit, web-design-guidelines, accessibility) |
| infra | hart | `Dockerfile*`, `requirements*.txt`, `*compose*.y*ml`, `pyproject.toml`, `package.json` | code-reviewer (Auftrag infra) |
| tests | normal | `test_*.py`, `*_test.py`, `*.test.*`, `*.spec.*` | pr-test-analyzer |

Immer dabei ab klein (wenn Code geändert): Grund-Prüfer `code-reviewer`, außer nur Python geändert → `python-reviewer` ersetzt ihn bei klein.
Ab mittel zusätzlich: `spec` (Zwei-Achsen-Prüfer „tut der Diff, was Ticket/Auftrag verlangt?“, Auftrag spec) und builtin `/code-review <stufe>` als Tiefen-Prüfer.
Bei klein: höchstens 2 Prüfer. Richter nur ab mittel.
Doppelte Prüfer zusammenlegen (Aufträge addieren).

**Profil** (`config.json` → `"profil"`, Default `schlank`): Obige Tabelle gilt für `profil: voll` (heutiges Verhalten). Bei `profil: schlank` (Default, Grund: Messung 63 % Fehlalarme bei Fachprüfern, Nutzen kam fast nur von Spec/Sicherheit/Tests):
- Stufe klein: 1 Prüfer `general-purpose` (Auftrag basis + alle Signal-IDs als Fachaufträge).
- Stufe mittel/groß: höchstens `general-purpose` (Auftrag spec, immer), `security-reviewer` (nur bei Signal `sicherheit`), `pr-test-analyzer` (nur bei Signal `tests` oder wenn Code-Dateien ohne jede Test-Datei geändert sind — dann mit Hinweis „Änderung ohne Test“).
- Alle übrigen Signal-IDs (python, fehlerpfad, kosten, datenbank, typescript, react, n8n, shell, infra, oberflaeche) werden als zusätzliche Aufträge an `general-purpose` gehängt statt eigenen Prüfer zu starten — Fachwissen bleibt erhalten, ohne eigene Prüfer-Instanz.

## 4. review_route.py (CLI)

```
python ~/.claude/review/review_route.py --moment <moment>
    [--repo <pfad>] [--base <git-ref>] [--staged] [--files <f> ...]
    [--log-ausloeser] [--kurz]
```

`moment` ∈ `sitzung`, `subagent`, `commit`, `ticket`, `deploy`, `bug`, `ui`, `plan`, `manuell`.
Diff-Quelle: `--staged` → `git diff --cached`; `--base X` → `git diff X...HEAD` + Arbeitsbaum; sonst `git diff HEAD` (Arbeitsbaum + gestagt). `--files` schränkt ein. Untracked Dateien in `--files` zählen als voll hinzugefügt.
`--log-ausloeser` hängt eine Zeile an `~/.claude/review/ausloeser.jsonl`: `{"zeit","repo","moment","stufe","fingerprint"}`.
`--kurz` gibt statt JSON eine Zeile Text: `Review <stufe>: <prüfer, …> (<n> Zeilen, <m> Dateien)`.

Ausgabe (stdout, JSON, UTF-8):

```json
{
  "version": 1,
  "moment": "commit",
  "repo": "/pfad/zum/projekt",
  "stufe": "mittel",
  "modus": "hinweis",
  "sha": "97634cf",
  "fingerprint": "sha256 über sha + sortierte Dateiliste + Diff-Text, erste 16 Hex",
  "diff_zeilen": 120,
  "dateien": ["web/services/x.py"],
  "signale": [{"id": "python", "klasse": "normal", "dateien": ["web/services/x.py"]}],
  "datei_groesse": [{"datei": "web/services/x.py", "vorher": 990, "nachher": 1010, "art": "ueber_1000"}],
  "pruefer": [
    {"name": "python-reviewer", "typ": "agent", "modell": "sonnet",
     "dateien": ["web/services/x.py"], "auftraege": ["basis", "python"],
     "regeln": ["Zeilen aus regeln.md, Abschnitte basis + python, für dieses Repo"]}
  ],
  "projektregeln": {"datei": "~/.claude/review/regeln.md", "vorhanden": true},
  "builtin": [{"befehl": "/code-review", "stufe": "high"}],
  "richter": true,
  "beleg": "C:/…/.review/beleg-97634cf-commit.json oder null",
  "beleg_gueltig": false
}
```

`auftraege`-Namen = Dateien in `auftraege/` (basis, python, typescript, react, shell, n8n, fehlerpfad, sicherheit, datenbank, kosten, oberflaeche, infra, tests, spec); `basis` immer zuerst. Prüfer `spec` = `general-purpose` mit Auftrag spec.
`datei_groesse` (Liste, deterministisch, ohne LLM): je geänderter Code-Datei (ohne Tests, `tests/**`, `*.md`/`*.txt`/`*.rst`, `*.json`, Lockfiles) Zeilen `vorher` (Basis des Diff-Modus: `--staged` → HEAD, `--base` → merge-base, sonst HEAD; neue Datei = 0) und `nachher` (`--staged` → Index, sonst Arbeitsbaum). `art` `ueber_1000`: vorher < `grenze` ≤ nachher; `waechst_gross`: vorher ≥ `grenze` und nachher − vorher > `zuwachs`. Grenzen aus `config.json` → `datei_groesse` (Vorgabe 1000/150). Gelöschte Dateien fehlen. Dirigent gibt die Einträge je Prüfer als Zeile `Datei-Größe:` mit; Prüfer melden `ueber_1000` als `mittel`, `waechst_gross` als `niedrig` (`auftraege/basis.md`).
`fix_runde` (int) im JSON: Runde des vorhandenen roten Belegs gleichen moment+sha +1, wenn fingerprint abweicht; sonst 0.
`typ` ∈ `agent` (Subagent-Typ in `~/.claude/agents/<name>.md`), `skill` (Linse aus Skill), `builtin`.
`beleg_gueltig` = Beleg existiert UND (fingerprint gleich ODER Dateimenge Teilmenge des Belegs mit gleichen `datei_hashes`, §5).
Fehlt `beleg-<HEAD>` oder ist er ungültig, sucht die Route den jüngsten Beleg desselben Moments über `inhalt_hashes` (§5) — unabhängig von HEAD; Treffer → `beleg` zeigt auf ihn, `beleg_gueltig` true.
Exit 0 immer, außer kaputte Eingabe (Exit 2). Kein Git-Repo → Stufe `keine`, Exit 0.

## 5. Beleg `<repo>/.review/beleg-<sha7>-<moment>-<fingerprint12>.json`

Fingerprint im Namen, damit parallele Sessions im selben Repo sich nicht überschreiben (alter Name ohne Fingerprint wird weiter gelesen). Repo = `--repo`, sonst Entwurf-Feld `repo`, sonst cwd; gehört die `sha` nicht zum Repo → Exit 2.

```json
{
  "version": 1,
  "moment": "commit",
  "stufe": "mittel",
  "modus": "hinweis",
  "sha": "97634cf",
  "fingerprint": "…",
  "dateien": ["…"],
  "datei_hashes": {"web/services/x.py": "…"},
  "diff_modus": {"staged": true, "base": null},
  "zeit": "2026-09-24T10:00:00+02:00",
  "dauer_s": 95,
  "token_schaetzung": 90000,
  "pruefer": [{"name": "python-reviewer", "modell": "sonnet", "befunde_roh": 3}],
  "richter": {"modell": "opus", "gestrichen": 2},
  "befunde": [
    {"datei": "web/services/x.py", "zeile": 42, "schwere": "hoch",
     "konfidenz": 90, "pruefer": "silent-failure-hunter",
     "text": "except: pass verschluckt Boot-Fehler", "status": "behalten"}
  ],
  "status": "rot",
  "fix_runde": 0
}
```

`schwere` ∈ `hoch`, `mittel`, `niedrig`. `status` (Beleg) ∈ `gruen`, `gelb`, `rot`. Befund-`status` ∈ `behalten`, `gestrichen`. Optional je Befund: `grund` (Richter-Begründung). `richter: null` bei Stufe klein. `pruefer` darf mehrere Namen mit `+` verbinden (zusammengelegte Doppel-Befunde).
Status-Regel (in `review_beleg.py` berechnet, nicht vom Schreiber gesetzt): behaltene Befunde mit Konfidenz < verwerfen_unter zählen nicht; irgendein behaltener mit schwere `hoch` und Konfidenz ≥ rot_ab → `rot`; sonst irgendein behaltener → `gelb`; sonst `gruen`.
`datei_hashes` (optional, {pfad: sha256[:16] des Datei-Diff-Texts}): erlaubt Gültigkeit für Teilmenge B ⊆ A (andere Datei-Auswahl, anderer Gesamt-fingerprint) statt nur exaktem fingerprint-Treffer — Bedingung: gleiche `sha`, alle Dateien von B im Beleg, je Datei gleicher Hash (`review_beleg.beleg_gueltig_pruefen`). Beleg ohne `datei_hashes` (alt) → abwärtskompatibel nur fingerprint-Vergleich.
`inhalt_hashes` (optional, {pfad: sha256[:16] über Hunk-Köpfe + Hunk-Zeilen + Modus-/Binär-Kopf}, Runde 5; Suche nur in den jüngsten 50 Belegen, kaputte Belege übersprungen, `fix_runde` bleibt): sha-frei, gleich für „untracked“ und „new file“ → Beleg überlebt den Commit des geprüften Codes (Stop-Hook prüft danach mit `--base <start>`). Anderer Inhalt oder andere Basis → anderer Hash → neues Review. Ohne Hunk-Zeilen (Binär) → "" = nie Treffer. `schreiben` trägt es wie `datei_hashes` nach.
`diff_modus` (optional, {staged: bool, base: Text|null}, von review_route ausgegeben): `review_beleg.py schreiben` trägt fehlende `datei_hashes` im selben Diff-Modus nach; fehlt `diff_modus` bei moment commit/ticket/deploy → kein Nachtrag (nur fingerprint).

## 6. review_beleg.py (CLI)

```
python ~/.claude/review/review_beleg.py schreiben --datei <entwurf.json> [--repo <pfad>]
    validiert, berechnet status, schreibt .review/beleg-…json, druckt Pfad + Status-Zeile
python ~/.claude/review/review_beleg.py pruefen --moment <m> [--repo <pfad>] [--staged|--base X|--files …]
    berechnet aktuellen fingerprint (wie review_route), sucht passenden Beleg.
    Exit 0: Beleg gültig und nicht rot, ODER Stufe keine, ODER modus hinweis (dann Grund auf stderr).
    Exit 1: modus scharf UND (Beleg fehlt ODER fingerprint alt ODER status rot). Grund 1 Zeile auf stderr.
python ~/.claude/review/review_beleg.py kurz --datei <beleg>   ≤ 15 Zeilen Zusammenfassung für die Hauptsession
```

## 7. review_bericht.py

```
python ~/.claude/review/review_bericht.py [--tage 7] [--repo <pfad> …] [--md <ausgabe.md>]
```
Liest `ausloeser.jsonl` (Soll) + alle `.review/beleg-*.json` der Repos (Ist; Repos = aus ausloeser.jsonl bekannte + `--repo`).
Tabelle je Moment: Soll, Ist, Quote, gruen/gelb/rot, gestrichene Befunde (Fehlalarm-Quote = gestrichen / roh), Token-Summe. Soll ≠ Ist → Zeile markiert „Hook oder Dirigent defekt?“.

## 8. Hooks → Dirigent

Hooks starten keine Subagenten (geht technisch nicht). Sie rechnen, protokollieren und geben Claude die Anweisung:
`Review fällig (<moment>, Stufe <stufe>): Skill review-dirigent ausführen mit moment=<moment> und --files <…>.`

- **Merker** PostToolUse `Edit|Write|NotebookEdit`: schreibt geänderte Datei (absolut) nach `~/.claude/review/sitzungen/<session_id>.json` (feuert auch für Subagenten derselben Sitzung). Nur Merken, keine Ausgabe.
- **Commit** PreToolUse `Bash` (`git commit`): merkt zusätzlich die Dateien des eigenen Commits (Pathspec nach `--`, sonst Index, bei `-a` getrackte) im selben Sitzungs-State — so zählen per Skript geänderte, selbst committete Dateien; fremde Commits paralleler Sessions laufen nie durch diesen Hook.
- **Stop** (Moment `sitzung`): Dateien aus dem Merker, die im Repo noch einen Diff haben → `review_route.py --moment sitzung --files … --log-ausloeser`. Stufe ≥ klein und kein gültiger Beleg → `decision: block` mit der Anweisung oben (auch im Modus hinweis: das Block dient nur dem Auslösen des Reviews). Beleg gültig → im Modus hinweis durchlassen; im Modus scharf bei rot erneut block (Fix-Runde).
- **PreToolUse Bash** (Moment `commit`, nur wenn Befehl `git commit` enthält): `--staged`. Modus hinweis → erlauben + `additionalContext` Anweisung, falls kein gültiger Beleg. Modus scharf → `deny` bei fehlendem/altem/rotem Beleg.
- Endlos-Schutz: Zähler je fingerprint in `~/.claude/review/sitzungen/<session_id>.json`; nach 1 Block (hinweis) bzw. `max_fix_runden` Blocks (scharf) → durchlassen, Meldung „Mensch nötig“. Außerdem Stop-Eingabe `stop_hook_active` beachten.
- Rekursions-Schutz: Prüfer ändern keine Dateien → kein Merker-Eintrag → kein neuer Review.
- Mehrere Repos in einer Sitzung: je Repo getrennt routen.
- `REVIEW_AUS=1` schaltet alle Review-Hooks stumm (Notbremse). Jeder Hook-Fehler → Exit 0 ohne Block, Fehler nach `~/.claude/review/hook-fehler.log` (Hooks dürfen nie die Arbeit lahmlegen).
