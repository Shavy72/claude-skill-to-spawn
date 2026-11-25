# Pipeline-Vorlage Stufe 0+1 (`workflow_call`)

Umsetzung zu Issue #796 (Teil 3 von #580, Parent #557). Die gesamte Logik für Stufe 0, Stufe 1 und die wiederverwendbare GitHub-Actions-Vorlage lebt an einer Stelle in `to_spawn/pruef/pipeline_vorlage.py` — plattformübergreifend für Linux und Windows.

## Aufbau

- **Stufe 0 (`to_spawn.pruef.pipeline_vorlage stufe0`)**: ruft den einheitlichen Ablauf `to_spawn.pruef.sofort.stufe0` aus Teil 1 (#794) auf (`ruff`, konfigurierbare Typ-Prüfung, AST-Diff-Test-Schutz).
- **Stufe 1 (`to_spawn.pruef.pipeline_vorlage stufe1`)**: Ersatz-Lauf der statischen AST-Import-Graph-Testauswahl aus Teil 2 (#795) auf GitHub Actions — ermittelt die direkt und transitiv betroffenen Testdateien, berücksichtigt optionale Auslassungs-Listen, behandelt 0 betroffene Tests sauber grün (kein pytest-Exit 5) und schreibt eine Markdown-Zusammenfassung für `GITHUB_STEP_SUMMARY`.

## Dokumentierte Eingaben der `workflow_call`-Vorlage

| Eingabe | Typ | Vorgabe | Zweck |
| --- | --- | --- | --- |
| `python-version` | `string` | `"3.13"` | Python-Version für die Runner |
| `pfade` | `string` | `""` | Optionale Pfad-Filter (leerzeichengetrennt); leer = ganzes Repo |
| `basis-ref` | `string` | `""` | Git-Basis-Ref für Diff/Test-Schutz/Import-Graph (leer = automatisch PR-Basis oder Vorgänger-Commit) |
| `ruff-befehl` | `string` | `""` | Optionaler ruff-Befehl für Stufe 0 (leer = Basis-Konfig oder Standard-ruff) |
| `typ-befehl` | `string` | `""` | Optionaler Typ-Prüf-Befehl für Stufe 0 (leer = Basis-Konfig oder ausgelassen) |
| `test-schutz` | `boolean` | `true` | AST-Diff-Test-Schutz in Stufe 0 aktivieren |
| `anforderungen` | `string` | `"tests/requirements.txt"` | Pfad zu pip-Abhängigkeiten für Tests (leer = nur `pytest` + `ruff`) |
| `test-pfade` | `string` | `""` | Optionale Test-Wurzelordner für die Import-Graph-Auswahl in Stufe 1 |
| `pytest-befehl` | `string` | `""` | Optionale zusätzliche pytest-Argumente für Stufe 1 |
| `auslassungen` | `string` | `""` | Optionaler Pfad zu einer Auslassungs-Liste (`<test-id>  # <grund>`) |
| `betriebssysteme` | `string` | `'["ubuntu-latest"]'` | JSON-Array der Runner-Betriebssysteme für Stufe 1 |
| `skill-pfad` | `string` | `"."` | Pfad zum Skill-Repo im Runner (Eigenanschluss `"."`) |

## Aktivierung im Skill-Repo (Eigenanschluss)

Das Erzeugen und Aktualisieren von Dateien unter `.github/workflows/` verlangt beim GitHub-Push den OAuth-Scope `workflow` (auf dem Bau-Server bewusst nicht vergeben). Auf einem Rechner mit `workflow`-Scope reichen zwei Befehle:

```bash
python -m to_spawn.pruef.pipeline_vorlage vorlage --ausgabe .github/workflows/pipeline-stufe-0-1.yml
python -m to_spawn.pruef.pipeline_vorlage vorlage --eigenanschluss --ausgabe .github/workflows/pipeline-stufe-0-1-eigen.yml
python -m to_spawn.pruef.pipeline_vorlage vorlage --pruefen .github/workflows/pipeline-stufe-0-1.yml
```

Danach die erzeugten Workflow-Dateien committen und pushen, damit der Eigenanschluss bei GitHub Actions anläuft und der grüne Lauf im Ticket verlinkt werden kann.
