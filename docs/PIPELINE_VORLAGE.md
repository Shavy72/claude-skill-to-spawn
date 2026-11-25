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
| **Stufe 3** | Wöchentliches Aufräumen ohne Feature-Bau (Wackler-Quarantäne, langsamste Tests, Diff-Mutationstest, Doppelte-Vorschläge, Schutzliste, Zweitprüfer) | Folge-Tickets #798, #799, #800 |

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
