# Pipeline-Vorlage (`workflow_call`) — Zielbild, Stufe 2 und Rolle des Bau-Servers

Zentrale Übersicht des gemeinsamen `workflow_call`-Rahmens im Skill-Repo (Stufe 0+1 aus #796/#580, Stufe 2 aus #797/#581, Parent #557). Alle Regeln und Generatoren leben an einer Stelle unter `to_spawn/pruef/` und laufen unter Linux und Windows gleich.

## Zielbild

Die Pipeline-Vorlage bildet vier fest verankerte Arbeitsweisen ab (auch als Kopf-Doku jeder erzeugten YAML-Vorlage aus `to_spawn.pruef.pipeline_vorlage.KOPF_DOKU_ZEILEN`):

1. **Triage**: neue Befunde, Fehlschläge und Tickets werden vor dem Bau sauber eingeordnet (System, Risiko, Umfang).
2. **Risikoarme Fixes bei grüner CI automatisch**: kleine, risikoarme Änderungen laufen bei grüner CI ohne unnötige Warteschleifen automatisch durch.
3. **Rest per Beweis**: größere oder risikobehaftete Änderungen gehen erst mit prüfbarem Beleg (Test-/Prüf-Beweis und Freigabe) weiter.
4. **Nächtliche Audits**: wiederkehrende Prüfrunden (Stufe 3 / Nachtrunde) suchen nach Wacklern, langsamsten Tests, Doppelten und fehlender Mutations-Abdeckung.

## Rolle des Bau-Servers

Die Aufgabenteilung zwischen Bau-Server und GitHub Actions ist strikt getrennt:

- **Bau-Server**: ausschließlich **Claude-Sessions** und **„braucht Server“-Tests** (`@pytest.mark.braucht("server")`, `@pytest.mark.braucht("bau-server")` oder `@pytest.mark.braucht_server`).
- **GitHub Actions**: alle regulären Tests, Stufe-0+1-Läufe und die verteilte **Stufe-2-Komplett-Prüfung** auf mindestens 2 parallelen Runnern.

Auf GitHub-Runnern (`GITHUB_ACTIONS=true`), bei `TO_SPAWN_BAU_SERVER=0`, in generischer CI ohne ausdrückliche Freigabe sowie auf Nicht-Linux-Systemen schließt `tests/conftest.py` (`braucht_fehlt`) Bau-Server-Tests automatisch aus. Zusätzlich setzt der Stufe-2-CI-Läufer `TO_SPAWN_BAU_SERVER=0` und filtert `-m "not braucht_server"`, sodass auch Fremd-Repos ohne eigene `conftest.py`-Erweiterung sauber geschützt sind.

## Stufen im `workflow_call`-Rahmen

| Stufe | Zweck | Modul / Doku |
| --- | --- | --- |
| **Stufe 0+1** | Schnelle Ticket-Prüfung: `ruff`, Typ-Prüfung, AST-Diff-Test-Schutz und statische AST-Import-Graph-Testauswahl | `to_spawn/pruef/pipeline_vorlage.py` · `docs/PIPELINE_STUFE_0_1.md` |
| **Stufe 2** | **Komplett-Prüfung genau einmal je Spec vor Live**: alle Tests verteilt auf `>= 2` GitHub-Actions-Runner (`pytest-split` + `pytest-xdist`) + bedingter Staging-Probe-Schritt | `to_spawn/pruef/pipeline_stufe2.py` · diese Datei |
| **Stufe 3** | Wöchentliches Aufräumen ohne Feature-Bau (Doppelte-Vorschläge mit unverbrüchlicher Schutzliste und Zweitprüfer-Sperre, Wackler-Quarantäne, langsamste Tests, Diff-Mutationstest) | `to_spawn/pruef/aufraeumen.py`, `to_spawn/pruef/quarantaene.py`, `to_spawn/pruef/mutation.py` · diese Datei |

Wann Stufe 2 dran ist, entscheidet weiterhin ausschließlich `to_spawn.pruefplan.welche(konfig, spec_stand)` in Verbindung mit `to_spawn.config.hat_staging(konfig)` (über `pipeline_stufe2.ist_stufe2_dran`):

- Repo **mit Staging-App**: genau einmal bei `Moment.ALLE_AUF_STAGING` (Sicherheitsnetz bei `Moment.VOR_LIVE`, falls abgebrochen).
- Repo **ohne Staging-App**: genau einmal bei `Moment.VOR_LIVE`.

## Stufe 2 (`pipeline-stufe-2.yml`) — Dokumentierte Eingaben

| Eingabe | Typ | Vorgabe | Zweck |
| --- | --- | --- | --- |
| `python-version` | `string` | `"3.13"` | Python-Version für die GitHub-Actions-Runner |
| `teile` | `number` | `2` | Anzahl paralleler Runner-Teile für `pytest-split` (mindestens `2`) |
| `xdist-worker` | `string` | `"auto"` | Anzahl `pytest-xdist`-Worker je Runner (`"auto"`, Zahl oder `"0"` zum Abschalten) |
| `test-pfade` | `string` | `"tests"` | Test-Wurzelordner oder Test-Pfade (leerzeichengetrennt) |
| `anforderungen` | `string` | `"tests/requirements.txt"` | Pfad zu pip-Abhängigkeiten (`pytest-split` und `pytest-xdist` werden immer sichergestellt) |
| `pytest-befehl` | `string` | `""` | Optionale zusätzliche pytest-Argumente (z. B. `--dist worksteal --tb=short`) |
| `auslassungen` | `string` | `""` | Optionaler Pfad zu einer Auslassungs-Liste (`<test-id>  # <grund>`) |
| `dauer-datei` | `string` | `".test_durations"` | Optionale Laufzeit-Datei für `pytest-split` (`least_duration`, falls vorhanden) |
| `betriebssysteme` | `string` | `'["ubuntu-latest"]'` | JSON-Array der Runner-Betriebssysteme für Stufe 2 |
| `mit-staging-app` | `boolean` | `false` | Schalter „mit Staging-App“: aktiviert den bedingten Staging-Probe-Schritt (wie `config.hat_staging`) |
| `staging-probe-befehl` | `string` | `""` | Befehl für den Staging-Probe-Schritt (läuft ausschließlich bei `mit-staging-app=true`) |
| `server-marker-ausschliessen` | `boolean` | `true` | Schließt Bau-Server-Tests (`braucht("server")` / `braucht_server`) auf GitHub-Runnern aus |
| `skill-pfad` | `string` | `"."` | Pfad zum Skill-Repo im Runner (Eigenanschluss `"."`, Fremdrepo z. B. `".to-spawn-skill"`) |

## Staging-Probe-Schalter (`mit-staging-app`)

Der Job `staging-probe` hängt hinter `stufe-2` und ist strikt an `if: ${{ inputs.mit-staging-app }}` gebunden. Auch die CLI (`python -m to_spawn.pruef.pipeline_stufe2 staging-probe`) prüft den Schalter explizit (oder liest ihn über `config.hat_staging` aus `.to-spawn/config.json`):

- **`mit-staging-app: false` (`--ohne-staging-app`)**: der Staging-Probe-Befehl wird nicht ausgeführt (Exit `0`, sauber übersprungen).
- **`mit-staging-app: true` (`--mit-staging-app`)**: der Staging-Probe-Befehl läuft; fehlt der Befehl, bricht der Schritt fail-closed mit Gate-Fehler (Exit `2`) ab.

## Aktivierung im Skill-Repo (Eigenanschluss mit `>= 2` Runnern)

Das Anlegen und Aktualisieren von Dateien unter `.github/workflows/` verlangt beim GitHub-Push den OAuth-Scope `workflow` (auf dem Bau-Server bewusst nicht vergeben). Auf einem Rechner mit `workflow`-Scope reichen drei Befehle:

```bash
python -m to_spawn.pruef.pipeline_stufe2 vorlage --ausgabe .github/workflows/pipeline-stufe-2.yml
python -m to_spawn.pruef.pipeline_stufe2 vorlage --eigenanschluss --teile 2 --ausgabe .github/workflows/pipeline-stufe-2-eigen.yml
python -m to_spawn.pruef.pipeline_stufe2 vorlage --pruefen .github/workflows/pipeline-stufe-2.yml
```

Alternativ steht derselbe Generator über den gemeinsamen Rahmen bereit:

```bash
python -m to_spawn.pruef.pipeline_vorlage vorlage --stufe 2 --ausgabe .github/workflows/pipeline-stufe-2.yml
```

Danach die erzeugten Workflow-Dateien committen und pushen, damit der Eigenanschluss mit Aufteilung auf `>= 2` Runner bei GitHub Actions läuft und der grüne Lauf samt Laufzeit im Ticket verlinkt werden kann.

## Stufe 3 (`pipeline-stufe-3.yml`) — Wöchentliches Aufräumen (nur Vorschläge)

Stufe 3 bündelt drei Aufräum-Bausteine ohne Feature-Bau mit strikt minimalen Leserechten (`permissions: contents: read`) — es werden ausschließlich Vorschläge und Berichte erzeugt, niemals Tests gelöscht:

1. **Doppelte-/Überdeckt-Vorschläge, Schutzliste & Zweitprüfer** (`to_spawn/pruef/aufraeumen.py`, #800):
   - Findet strukturell doppelte und überdeckte Tests per AST und gibt ausschließlich Vorschläge aus.
   - **Unumgehbare Schutzliste**: Nutzer-Ablauf-Tests (`nutzer_ablauf`, `e2e`, `user_flow`, `smoke`, …) und Tests mit echtem Fund (`echter_fund`, `regression`, `bugfix`, `vorfall`, …) bleiben immer geschützt. Zusätzliche Muster/Marker/Tests können über `.to-spawn/config.json` unter `aufraeumen.schutzliste` ergänzt, Pflichtregeln aber niemals abgeschaltet werden.
   - **Zweitprüfer-Sperre (fail-closed)**: `pruefe_loeschfreigabe` / `python -m to_spawn.pruef.aufraeumen freigabe-pruefen` erteilt eine technische Freigabe erst nach bestätigtem Prüf-Nachweis eines zweiten unabhängigen Agenten (Selbst-Freigabe desselben Agenten oder derselben Session sowie Schutzlisten-Treffer werden zwingend abgelehnt).
2. **Wackler-Quarantäne & langsamste Tests** (`to_spawn/pruef/quarantaene.py`, #798): schreibt das Quarantäne-Ledger fort und meldet Top-N langsamste Tests aus JUnit-XML.
3. **Diff-Mutationstest** (`to_spawn/pruef/mutation.py`, #799, nur Linux): prüft `mutmut` strikt auf geändertem Produktivcode.

### Aktivierung von Stufe 3 im Skill-Repo (Rechner mit `workflow`-Scope)

```bash
python -m to_spawn.pruef.aufraeumen vorlage --ausgabe .github/workflows/pipeline-stufe-3.yml
python -m to_spawn.pruef.aufraeumen vorlage --eigenanschluss --ausgabe .github/workflows/pipeline-stufe-3-eigen.yml
python -m to_spawn.pruef.aufraeumen vorlage --pruefen .github/workflows/pipeline-stufe-3.yml
```

Alternativ über den gemeinsamen Rahmen:

```bash
python -m to_spawn.pruef.pipeline_vorlage vorlage --stufe 3 --ausgabe .github/workflows/pipeline-stufe-3.yml
```
