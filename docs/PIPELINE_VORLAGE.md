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
| **Nachtrunde** | Nächtliche Stufe-2-Prüfung mit Commit-Vorabprüfung, `concurrency`-Schutz und genau einem offenen Ticket „Nachtrunde rot“ (keine Mail, keine Benachrichtigung) | `to_spawn/pruef/nachtrunde.py` · diese Datei |

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

## Einbindung im Skill-Repo (Eigenanschluss mit `>= 2` Runnern)

Die erzeugten Workflow-Dateien `.github/workflows/pipeline-stufe-2.yml` (Vorlage) und `.github/workflows/pipeline-stufe-2-eigen.yml` (Eigenanschluss mit `teile: 2`) sind im Skill-Repo eingehängt. Zum Aktualisieren oder Prüfen aus der einen Quelle (`to_spawn/pruef/pipeline_stufe2.py`) reichen drei Befehle:

```bash
python -m to_spawn.pruef.pipeline_stufe2 vorlage --ausgabe .github/workflows/pipeline-stufe-2.yml
python -m to_spawn.pruef.pipeline_stufe2 vorlage --eigenanschluss --teile 2 --ausgabe .github/workflows/pipeline-stufe-2-eigen.yml
python -m to_spawn.pruef.pipeline_stufe2 vorlage --pruefen .github/workflows/pipeline-stufe-2.yml
```

Alternativ steht derselbe Generator über den gemeinsamen Rahmen bereit:

```bash
python -m to_spawn.pruef.pipeline_vorlage vorlage --stufe 2 --ausgabe .github/workflows/pipeline-stufe-2.yml
```

## Stufe 3 (`pipeline-stufe-3.yml`) — Wöchentliches Aufräumen (nur Vorschläge)

Stufe 3 bündelt drei Aufräum-Bausteine ohne Feature-Bau mit strikt minimalen Leserechten (`permissions: contents: read`) — es werden ausschließlich Vorschläge und Berichte erzeugt, niemals Tests gelöscht:

1. **Doppelte-/Überdeckt-Vorschläge, Schutzliste & Zweitprüfer** (`to_spawn/pruef/aufraeumen.py`, #800):
   - Findet strukturell doppelte und überdeckte Tests per AST und gibt ausschließlich Vorschläge aus.
   - **Unumgehbare Schutzliste**: Nutzer-Ablauf-Tests (`nutzer_ablauf`, `e2e`, `user_flow`, `smoke`, …) und Tests mit echtem Fund (`echter_fund`, `regression`, `bugfix`, `vorfall`, …) bleiben immer geschützt. Zusätzliche Muster/Marker/Tests können über `.to-spawn/config.json` unter `aufraeumen.schutzliste` ergänzt, Pflichtregeln aber niemals abgeschaltet werden.
   - **Zweitprüfer-Sperre (fail-closed)**: `pruefe_loeschfreigabe` / `python -m to_spawn.pruef.aufraeumen freigabe-pruefen` erteilt eine technische Freigabe erst nach bestätigtem Prüf-Nachweis eines zweiten unabhängigen Agenten (Selbst-Freigabe desselben Agenten oder derselben Session sowie Schutzlisten-Treffer werden zwingend abgelehnt).
2. **Wackler-Quarantäne & langsamste Tests** (`to_spawn/pruef/quarantaene.py`, #798): schreibt das Quarantäne-Ledger fort und meldet Top-N langsamste Tests aus JUnit-XML.
3. **Diff-Mutationstest** (`to_spawn/pruef/mutation.py`, #799, nur Linux): prüft `mutmut` strikt auf geändertem Produktivcode.

### Einbindung von Stufe 3 im Skill-Repo

Die erzeugten Workflow-Dateien `.github/workflows/pipeline-stufe-3.yml` (Vorlage) und `.github/workflows/pipeline-stufe-3-eigen.yml` (wöchentlicher Eigenanschluss) sind im Skill-Repo eingehängt. Zum Aktualisieren oder Prüfen aus `to_spawn/pruef/aufraeumen.py`:

```bash
python -m to_spawn.pruef.aufraeumen vorlage --ausgabe .github/workflows/pipeline-stufe-3.yml
python -m to_spawn.pruef.aufraeumen vorlage --eigenanschluss --ausgabe .github/workflows/pipeline-stufe-3-eigen.yml
python -m to_spawn.pruef.aufraeumen vorlage --pruefen .github/workflows/pipeline-stufe-3.yml
```

Alternativ über den gemeinsamen Rahmen:

```bash
python -m to_spawn.pruef.pipeline_vorlage vorlage --stufe 3 --ausgabe .github/workflows/pipeline-stufe-3.yml
```

## Nachtrunde (`pipeline-nachtrunde.yml`, #582, Beschluss E32)

Die wiederverwendbare `workflow_call`-Vorlage `pipeline-nachtrunde.yml` (`to_spawn/pruef/nachtrunde.py`) kapselt die nächtliche Komplett-Prüfung (Stufe 2 aus #581) für das Skill-Repo, Übungs-Repos und angeschlossene Projekt-Repos. Sie läuft **ausschließlich auf GitHub Actions**, niemals auf dem Bau-Server (`self-hosted` ist verboten; Bau-Server-Tests werden wie in Stufe 2 ausgeschlossen).

### Ablauf und Schutzregeln

1. **Auslöser (im Anschluss-Workflow)**: `on: schedule` mit schiefer Minute (Standard `17 2 * * *` UTC, nie zur vollen Stunde `:00`) plus `workflow_dispatch`.
2. **`concurrency`-Gruppe**: `cancel-in-progress: false` sowohl im Anschluss-Workflow (`nachtrunde-${{ github.ref }}`) als auch in der Vorlage (`nachtrunde-vorlage-${{ github.repository }}-${{ inputs.hauptzweig || github.ref_name }}`) — es laufen nie zwei Nachtrunden gleichzeitig; ein parallel gestarteter Lauf wartet sauber auf den Abschluss.
3. **Vorab-Prüfung (`vorab`)**: vergleicht den aktuellen Commit des Hauptzweigs über `to_spawn.pruef.nachtrunde vorab` mit dem jüngsten grünen Nachtrunden-Lauf auf demselben Zweig.
   - **Keine neuen Commits**: alle Folge-Jobs (`vorbereiten`, `stufe-2`, `staging-probe`, `ticket-abgleich`) werden übersprungen; der Lauf endet in Sekunden (< 60 s) grün ohne Testlauf.
   - **Neue Commits oder letzter Lauf war rot**: Stufe 2 läuft vollständig verteilt auf `>= 2` GitHub-Runnern.
4. **Genau ein offenes Ticket „Nachtrunde rot“ (`ticket-abgleich`)**:
   - **Erster roter Lauf**: erstellt genau ein offenes Ticket mit Titel `Nachtrunde rot` samt Zweig, Commit, Job-Status und Link zum Lauf.
   - **Folgenacht weiterhin rot**: kommentiert ausschließlich dieses eine offene Ticket — es wird kein zweites Ticket angelegt.
   - **Wieder grüner Lauf**: schließt das offene Ticket automatisch mit Abschluss-Kommentar und Link zum grünen Lauf.
   - **Keine Mail, keine Benachrichtigung**: die Vorlage enthält absichtlich keinerlei Mail-, Chat- oder Webhook-Schritte (`pruefe_nachtrunde_vorlage_text` prüft das automatisch).

### Nachtrunde (`pipeline-nachtrunde.yml`) — Dokumentierte Eingaben

| Eingabe | Typ | Vorgabe | Zweck |
| --- | --- | --- | --- |
| `hauptzweig` | `string` | `""` | Hauptzweig für die Commit-Vorabprüfung (leer = Standardzweig des Repos, z. B. `main`) |
| `workflow-datei` | `string` | `""` | Optionaler Dateiname des aufrufenden Nachtrunden-Workflows (leer = automatisch über aktuelle Lauf-ID) |
| `immer-laufen` | `boolean` | `false` | Überspringt die Commit-Vorabprüfung und erzwingt Stufe 2 (z. B. manueller Prüflauf) |
| `python-version` | `string` | `"3.13"` | Python-Version für die GitHub-Actions-Runner |
| `teile` | `number` | `2` | Anzahl paralleler Runner-Teile für Stufe 2 (`pytest-split`, mindestens `2`) |
| `xdist-worker` | `string` | `"auto"` | Anzahl `pytest-xdist`-Worker je Runner (`"auto"`, Zahl oder `"0"`) |
| `test-pfade` | `string` | `"tests"` | Test-Wurzelordner oder Test-Pfade für Stufe 2 (leerzeichengetrennt) |
| `anforderungen` | `string` | `"tests/requirements.txt"` | Pfad zu pip-Abhängigkeiten (`pytest-split` und `pytest-xdist` werden sichergestellt) |
| `pytest-befehl` | `string` | `""` | Optionale zusätzliche pytest-Argumente für Stufe 2 |
| `auslassungen` | `string` | `""` | Optionaler Pfad zu einer Auslassungs-Liste (`<test-id>  # <grund>`) |
| `dauer-datei` | `string` | `".test_durations"` | Optionale Laufzeit-Datei für `pytest-split` (`least_duration`, falls vorhanden) |
| `betriebssysteme` | `string` | `'["ubuntu-latest"]'` | JSON-Array der Runner-Betriebssysteme für Stufe 2 |
| `mit-staging-app` | `boolean` | `false` | Schalter „mit Staging-App“ für den bedingten Staging-Probe-Schritt in Stufe 2 |
| `staging-probe-befehl` | `string` | `""` | Befehl für den Staging-Probe-Schritt (läuft nur bei `mit-staging-app=true`) |
| `server-marker-ausschliessen` | `boolean` | `true` | Schließt Bau-Server-Tests (`braucht("server")` / `braucht_server`) auf GitHub-Runnern aus |
| `ticket-titel` | `string` | `"Nachtrunde rot"` | Titel des genau einen offenen Fehler-Tickets im Repo |
| `skill-pfad` | `string` | `"."` | Pfad zum Skill-Repo im Runner (Eigenanschluss `"."`, Fremdrepo z. B. `".to-spawn-skill"`) |
| `skill-repo` | `string` | `""` | Optionales Skill-Repo (`owner/name`) für automatischen Checkout bei Fremd- und Übungs-Repos |
| `skill-ref` | `string` | `"main"` | Git-Ref des Skill-Repos beim automatischen Checkout über `skill-repo` |

### Kosten deckeln: GitHub-Actions-Budget (Empfehlung 30 $)

Um unerwartete Minutenkosten bei nächtlichen Läufen oder vielen angeschlossenen Repos sicher zu begrenzen, sollte im GitHub-Konto (bzw. in der Organisation) ein festes monatliches Actions-Budget von **30 $** (30 USD) eingerichtet werden:

1. Auf GitHub öffnen: **Settings** (Persönliches Konto oder Organisation) → **Billing and plans** (bzw. **Billing & licensing**) → **Budgets and alerts** (bzw. **Spending limits**).
2. Neues Budget für das Produkt **Actions** anlegen (bzw. das bestehende Actions-Budget bearbeiten) und den Monatsbetrag auf **30 $** setzen.
3. **Stop usage when budget limit is reached** aktivieren, damit GitHub Actions ab Erreichen der 30-$-Grenze hart stoppt statt still weiter abzurechnen.
4. Warn-Schwellen (z. B. **75 %** und **90 %**) aktivieren.

Zusätzlich spart die Vorab-Prüfung der Nachtrunde Minuten, weil Nächte ohne neue Commits auf dem Hauptzweig bereits nach wenigen Sekunden ohne Testlauf enden.

### Aktivierung der Nachtrunde (Skill-Repo & Übungs-Repo)

Im Skill-Repo (auf einem Rechner mit GitHub-`workflow`-Scope):

```bash
python -m to_spawn.pruef.nachtrunde vorlage --ausgabe .github/workflows/pipeline-nachtrunde.yml
python -m to_spawn.pruef.nachtrunde vorlage --eigenanschluss --ausgabe .github/workflows/pipeline-nachtrunde-eigen.yml
python -m to_spawn.pruef.nachtrunde vorlage --pruefen .github/workflows/pipeline-nachtrunde.yml
```

Alternativ über den gemeinsamen Vorlagen-Rahmen:

```bash
python -m to_spawn.pruef.pipeline_vorlage vorlage --stufe nachtrunde --ausgabe .github/workflows/pipeline-nachtrunde.yml
```

Übungs-Repo mit wenigen Zeilen direkt anschließen (`probe/anlegen.sh` erzeugt `.github/workflows/nachtrunde.yml` automatisch):

```bash
bash probe/anlegen.sh /pfad/zum/uebungs-repo ohne-staging --mit-nachtrunde
```

Oder die Anschluss-Datei gezielt erzeugen und prüfen:

```bash
python -m to_spawn.pruef.nachtrunde vorlage --uebungsrepo --ausgabe .github/workflows/nachtrunde.yml
python -m to_spawn.pruef.nachtrunde vorlage --uebungsrepo --pruefen .github/workflows/nachtrunde.yml
```
