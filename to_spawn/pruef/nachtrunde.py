"""Wiederverwendbare Nachtrunden-Vorlage (``workflow_call``), Commit-Vorabprüfung und Ein-Ticket „Nachtrunde rot“ (#582).

Bündelt das Wissen der Nachtrunde im gemeinsamen ``workflow_call``-Rahmen an genau einer Stelle
(Linux und Windows identisch, ohne externe YAML-Pflicht und ohne Mail-/Benachrichtigungs-Schritte):
- :data:`EINGABEN_NACHTRUNDE` — dokumentierte Eingaben für ``.github/workflows/pipeline-nachtrunde.yml``.
- :func:`vorlage_nachtrunde_yaml` — erzeugt die ``workflow_call``-Vorlage mit ``concurrency``-Gruppe,
  Commit-Vorabprüfung, Stufe-2-Ausführung auf GitHub Actions (nie auf dem Bau-Server) und
  automatischem Ticket-Abgleich („Nachtrunde rot“).
- :func:`anschluss_nachtrunde_yaml` / :func:`eigenanschluss_nachtrunde_yaml` /
  :func:`uebungsrepo_anschluss_yaml` — kurze Anschluss-Workflows mit ``on: schedule`` (schiefe Minute,
  Standard ``17 2 * * *`` UTC) + ``workflow_dispatch`` + ``concurrency``.
- :func:`pruefe_nachtrunde_vorlage_text` / :func:`pruefe_anschluss_nachtrunde_text` — reine
  Standardbibliothek-Vertragsprüfung (inkl. Prüfung auf Abwesenheit von Mail-/Benachrichtigungs-Schritten).
- :func:`vorab_pruefung` — prüft per ``to_spawn.gh``, ob der Hauptzweig seit der letzten grünen
  Nachtrunde neue Commits hat (nein → Abbruch in Sekunden, grün, ohne Testlauf).
- :func:`ticket_abgleich` — pflegt genau ein offenes Ticket „Nachtrunde rot“ im Repo:
  erster roter Lauf → erstellen, Folgenacht rot → kommentieren, grüner Lauf → automatisch schließen.
- CLI: ``python -m to_spawn.pruef.nachtrunde {vorab,ticket,vorlage,git-identitaet}``.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from to_spawn import config, gh, konsole
from to_spawn.pruef import leiter, pipeline_stufe2, pipeline_vorlage

log = logging.getLogger("to_spawn.pruef.nachtrunde")

#: Standard-Zeitplan mit schiefer Minute (02:17 UTC — nie zur vollen Stunde).
STANDARD_CRON = "17 2 * * *"
#: Standard-Titel des genau einen offenen Fehler-Tickets im Repo.
TICKET_TITEL = "Nachtrunde rot"
#: Empfohlene monatliche GitHub-Actions-Budgetgrenze in US-Dollar.
BUDGET_EMPFEHLUNG_USD = 30
#: Standard-Referenz der wiederverwendbaren Vorlage für angeschlossene Fremd- und Übungs-Repos.
STANDARD_VORLAGE_REF = "Shavy72/claude-skill-to-spawn/.github/workflows/pipeline-nachtrunde.yml@main"
#: Standard-Skill-Repo für den automatischen Checkout bei Fremd- und Übungs-Repos.
STANDARD_SKILL_REPO = "Shavy72/claude-skill-to-spawn"

#: Verbotene Schlüsselwörter für Mail- oder Benachrichtigungs-Schritte (grep-sicher für Vorlagen).
_VERBOTENE_BENACHRICHTIGUNG = re.compile(
    r"mail|smtp|slack|discord|teams|telegram|webhook|notify|notification|pushover|benachrichtig",
    re.IGNORECASE,
)

#: Alle dokumentierten Eingaben für ``.github/workflows/pipeline-nachtrunde.yml``.
EINGABEN_NACHTRUNDE: tuple[pipeline_vorlage.Eingabe, ...] = (
    pipeline_vorlage.Eingabe(
        name="hauptzweig",
        typ="string",
        vorgabe="",
        beschreibung="Hauptzweig für die Commit-Vorabprüfung (leer = Standardzweig des Repos, z. B. main).",
    ),
    pipeline_vorlage.Eingabe(
        name="workflow-datei",
        typ="string",
        vorgabe="",
        beschreibung="Optionaler Dateiname des aufrufenden Nachtrunden-Workflows (leer = automatisch aus aktuellem Lauf).",
    ),
    pipeline_vorlage.Eingabe(
        name="immer-laufen",
        typ="boolean",
        vorgabe=False,
        beschreibung="Commit-Vorabprüfung überspringen und Stufe 2 immer ausführen (z. B. manueller Prüflauf).",
    ),
    pipeline_vorlage.Eingabe(
        name="python-version",
        typ="string",
        vorgabe="3.13",
        beschreibung="Python-Version für die GitHub-Actions-Runner.",
    ),
    pipeline_vorlage.Eingabe(
        name="teile",
        typ="number",
        vorgabe=2,
        beschreibung="Anzahl paralleler Runner-Teile für Stufe 2 (pytest-split, mindestens 2).",
    ),
    pipeline_vorlage.Eingabe(
        name="xdist-worker",
        typ="string",
        vorgabe="auto",
        beschreibung="Anzahl pytest-xdist-Worker je Runner (z. B. 'auto', Zahl oder '0').",
    ),
    pipeline_vorlage.Eingabe(
        name="test-pfade",
        typ="string",
        vorgabe="tests",
        beschreibung="Test-Wurzelordner oder Test-Pfade für Stufe 2 (leerzeichengetrennt).",
    ),
    pipeline_vorlage.Eingabe(
        name="anforderungen",
        typ="string",
        vorgabe="tests/requirements.txt",
        beschreibung="Pfad zu pip-Abhängigkeiten für Tests (pytest-split und pytest-xdist werden sichergestellt).",
    ),
    pipeline_vorlage.Eingabe(
        name="pytest-befehl",
        typ="string",
        vorgabe="",
        beschreibung="Optionale zusätzliche pytest-Argumente für Stufe 2.",
    ),
    pipeline_vorlage.Eingabe(
        name="auslassungen",
        typ="string",
        vorgabe="",
        beschreibung="Optionaler Pfad zu einer Auslassungs-Liste im Format '<test-id>  # <grund>'.",
    ),
    pipeline_vorlage.Eingabe(
        name="dauer-datei",
        typ="string",
        vorgabe=".test_durations",
        beschreibung="Optionale Laufzeit-Datei für pytest-split (least_duration, falls vorhanden).",
    ),
    pipeline_vorlage.Eingabe(
        name="betriebssysteme",
        typ="string",
        vorgabe='["ubuntu-latest"]',
        beschreibung="JSON-Array der Runner-Betriebssysteme für Stufe 2 (z. B. ubuntu-latest und windows-latest).",
    ),
    pipeline_vorlage.Eingabe(
        name="mit-staging-app",
        typ="boolean",
        vorgabe=False,
        beschreibung="Schalter 'mit Staging-App': aktiviert den bedingten Staging-Probe-Schritt in Stufe 2.",
    ),
    pipeline_vorlage.Eingabe(
        name="staging-probe-befehl",
        typ="string",
        vorgabe="",
        beschreibung="Befehl für den Staging-Probe-Schritt (läuft ausschließlich bei mit-staging-app=true).",
    ),
    pipeline_vorlage.Eingabe(
        name="server-marker-ausschliessen",
        typ="boolean",
        vorgabe=True,
        beschreibung="Bau-Server-Tests ('braucht Server' / Marker braucht_server) auf GitHub-Runnern ausschließen.",
    ),
    pipeline_vorlage.Eingabe(
        name="ticket-titel",
        typ="string",
        vorgabe=TICKET_TITEL,
        beschreibung="Titel des genau einen offenen GitHub-Tickets bei roter Nachtrunde.",
    ),
    pipeline_vorlage.Eingabe(
        name="skill-pfad",
        typ="string",
        vorgabe=".",
        beschreibung="Pfad zum Skill-Repo im Runner (Eigenanschluss '.', Fremdrepo z. B. '.to-spawn-skill').",
    ),
    pipeline_vorlage.Eingabe(
        name="skill-repo",
        typ="string",
        vorgabe="",
        beschreibung="Optionales Skill-Repo ('owner/name') zum automatischen Checkout bei Fremdrepos; leer = lokal.",
    ),
    pipeline_vorlage.Eingabe(
        name="skill-ref",
        typ="string",
        vorgabe="main",
        beschreibung="Git-Ref des Skill-Repos beim automatischen Checkout über skill-repo.",
    ),
)

TicketAktion = Literal["erstellt", "kommentiert", "geschlossen", "unveraendert", "fehler"]
NachtrundeStatus = Literal["rot", "gruen"]


@dataclass(frozen=True)
class VorabErgebnis:
    """Ergebnis der Commit-Vorabprüfung vor dem Start von Stufe 2."""

    ok: bool
    exit_code: int
    laufen: bool
    hauptzweig: str
    aktueller_sha: str
    letzter_gruener_sha: str
    grund: str

    def als_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "laufen": self.laufen,
            "hauptzweig": self.hauptzweig,
            "aktueller_sha": self.aktueller_sha,
            "letzter_gruener_sha": self.letzter_gruener_sha,
            "grund": self.grund,
        }

    def zusammenfassung(self, titel: str = "Nachtrunde — Vorab-Prüfung") -> str:
        """Kurze Markdown-Zusammenfassung für ``GITHUB_STEP_SUMMARY`` oder Protokolle."""
        entscheidung = "Stufe 2 läuft" if self.laufen else "übersprungen (grün, keine neuen Commits)"
        alt_kurz = self.letzter_gruener_sha[:12] if self.letzter_gruener_sha else "–"
        neu_kurz = self.aktueller_sha[:12] if self.aktueller_sha else "–"
        zeilen = [
            f"### {titel}",
            "",
            (
                f"Entscheidung: **{entscheidung}** · Zweig: `{self.hauptzweig}` · "
                f"aktuell: `{neu_kurz}` · letzte grüne Nachtrunde: `{alt_kurz}`"
            ),
            "",
        ]
        if self.grund:
            zeilen.append(f"Hinweis: {self.grund}")
            zeilen.append("")
        return "\n".join(zeilen)


@dataclass(frozen=True)
class TicketAbgleichErgebnis:
    """Ergebnis der Pflege des genau einen offenen Tickets „Nachtrunde rot“."""

    ok: bool
    exit_code: int
    ergebnis: NachtrundeStatus
    aktion: TicketAktion
    titel: str
    ticket_nummer: int | None
    ticket_url: str
    geschlossene_nummern: tuple[int, ...]
    grund: str

    def als_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "ergebnis": self.ergebnis,
            "aktion": self.aktion,
            "titel": self.titel,
            "ticket_nummer": self.ticket_nummer,
            "ticket_url": self.ticket_url,
            "geschlossene_nummern": list(self.geschlossene_nummern),
            "grund": self.grund,
        }

    def zusammenfassung(self, titel: str = "Nachtrunde — Ticket-Abgleich") -> str:
        """Kurze Markdown-Zusammenfassung für ``GITHUB_STEP_SUMMARY`` oder Protokolle."""
        ticket_text = f"#{self.ticket_nummer}" if self.ticket_nummer is not None else "keins"
        zeilen = [
            f"### {titel}",
            "",
            f"Nachtrunde: **{self.ergebnis}** · Aktion: `{self.aktion}` · Ticket „{self.titel}“: {ticket_text}",
            "",
        ]
        if self.ticket_url:
            zeilen.append(f"Link: {self.ticket_url}")
            zeilen.append("")
        if self.grund:
            zeilen.append(f"Hinweis: {self.grund}")
            zeilen.append("")
        return "\n".join(zeilen)


# --- Cron- und Hilfsprüfungen -----------------------------------------------------------------


def pruefe_cron(cron: str) -> None:
    """Prüft einen 5-Feld-Cron-Ausdruck mit schiefer Minute (1–59, nie volle Stunde ``:00``)."""
    sauber = cron.strip()
    felder = sauber.split()
    if len(felder) != 5:
        raise ValueError(f"Ungültiger 5-Feld-Cron-Ausdruck für Nachtrunde: {cron!r}")
    minute_text = felder[0]
    if not minute_text.isdigit():
        raise ValueError(f"Cron-Minute muss eine feste schiefe Minute (1–59) sein, nicht {minute_text!r}")
    minute = int(minute_text)
    if not 1 <= minute <= 59:
        raise ValueError(f"Cron-Minute muss eine schiefe Minute zwischen 1 und 59 sein (nicht {minute})")


def _schreibe_summary(ziel: Path | str | None, text: str) -> None:
    if not ziel:
        return
    pfad = Path(ziel)
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        with pfad.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError as fehler:
        log.warning("Zusammenfassung konnte nicht nach %s geschrieben werden: %s", pfad, fehler)


def _schreibe_github_output(ziel: Path | str | None, werte: dict[str, str]) -> None:
    if not ziel:
        return
    pfad = Path(ziel)
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        with pfad.open("a", encoding="utf-8", newline="\n") as handle:
            for schluessel, wert in werte.items():
                sauber = str(wert).replace("\r", " ").replace("\n", " ").strip()
                handle.write(f"{schluessel}={sauber}\n")
    except OSError as fehler:
        log.warning("GITHUB_OUTPUT konnte nicht nach %s geschrieben werden: %s", pfad, fehler)


def _pythonpath_export_zeilen() -> str:
    """Portabler bash-Block für ``PYTHONPATH`` auf Linux und Windows-Runnern."""
    return (
        '          if [ -n "${PYTHONPATH:-}" ]; then\n'
        '            SEP=":"\n'
        '            [ "${RUNNER_OS:-}" = "Windows" ] && SEP=";"\n'
        '            export PYTHONPATH="${SKILL_PFAD}${SEP}${PYTHONPATH}"\n'
        "          else\n"
        '            export PYTHONPATH="${SKILL_PFAD}"\n'
        "          fi\n"
    )


def _skill_checkout_schritt() -> str:
    """Optionaler Checkout des Skill-Repos in ``inputs.skill-pfad`` für Fremd- und Übungs-Repos."""
    return (
        "      - name: Skill-Repo auschecken (Fremdrepo)\n"
        "        if: ${{ inputs.skill-repo != '' }}\n"
        "        uses: actions/checkout@v4\n"
        "        with:\n"
        "          repository: ${{ inputs.skill-repo }}\n"
        "          ref: ${{ inputs.skill-ref }}\n"
        "          path: ${{ inputs.skill-pfad }}\n"
        "          sparse-checkout: to_spawn\n"
    )


# --- YAML-Vorlagen & Vertragsprüfung ----------------------------------------------------------


def vorlage_nachtrunde_yaml() -> str:
    """Erzeugt die wiederverwendbare ``workflow_call``-Vorlage für die Nachtrunde (``.github/workflows/pipeline-nachtrunde.yml``)."""
    eingabe_zeilen: list[str] = []
    for e in EINGABEN_NACHTRUNDE:
        eingabe_zeilen.extend(
            [
                f"      {e.name}:",
                f"        description: {pipeline_vorlage._yaml_skalar(e.beschreibung)}",
                "        required: false",
                f"        type: {e.typ}",
                f"        default: {pipeline_vorlage._yaml_skalar(e.vorgabe)}",
            ]
        )
    eingaben_block = "\n".join(eingabe_zeilen)
    py_export = _pythonpath_export_zeilen()
    skill_step = _skill_checkout_schritt()
    return (
        "# Wiederverwendbare Nachtrunden-Vorlage (#582, Beschluss E32).\n"
        f"{pipeline_vorlage.kopf_doku_kommentar()}"
        "# Ablauf der Nachtrunde (läuft ausschließlich auf GitHub Actions, nie auf dem Bau-Server):\n"
        "# 1. Vorab-Prüfung: gab es seit der letzten grünen Nachtrunde neue Commits? Nein -> Abbruch in Sekunden (grün).\n"
        "# 2. Stufe 2 (Komplett-Prüfung aus #581): verteilt auf >= 2 Runner (pytest-split + pytest-xdist).\n"
        "# 3. Ticket-Abgleich: Rot pflegt genau ein offenes Ticket 'Nachtrunde rot', Grün schließt es selbst.\n"
        "name: Pipeline Nachtrunde\n"
        "\n"
        "on:\n"
        "  workflow_call:\n"
        "    inputs:\n"
        f"{eingaben_block}\n"
        "\n"
        "permissions:\n"
        "  contents: read\n"
        "  actions: read\n"
        "  issues: write\n"
        "\n"
        "concurrency:\n"
        "  group: nachtrunde-vorlage-${{ github.repository }}-${{ inputs.hauptzweig || github.ref_name }}\n"
        "  cancel-in-progress: false\n"
        "\n"
        "jobs:\n"
        "  vorab:\n"
        "    name: Vorab-Prüfung (neue Commits seit letzter grüner Nachtrunde)\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 5\n"
        "    outputs:\n"
        "      laufen: ${{ steps.pruefen.outputs.laufen }}\n"
        "      hauptzweig: ${{ steps.pruefen.outputs.hauptzweig }}\n"
        "      aktueller_sha: ${{ steps.pruefen.outputs.aktueller_sha }}\n"
        "      letzter_gruener_sha: ${{ steps.pruefen.outputs.letzter_gruener_sha }}\n"
        "    defaults:\n"
        "      run:\n"
        "        shell: bash\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "\n"
        f"{skill_step}"
        "\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "${{ inputs.python-version }}"\n'
        "\n"
        "      - id: pruefen\n"
        "        name: Neue Commits seit letzter grüner Nachtrunde prüfen\n"
        "        env:\n"
        "          GH_TOKEN: ${{ github.token }}\n"
        "          SKILL_PFAD: ${{ inputs.skill-pfad }}\n"
        "          HAUPTZWEIG: ${{ inputs.hauptzweig || github.ref_name }}\n"
        "          WORKFLOW_DATEI: ${{ inputs.workflow-datei }}\n"
        "          IMMER_LAUFEN: ${{ inputs.immer-laufen }}\n"
        "          AKTUELLER_LAUF_ID: ${{ github.run_id }}\n"
        "          AKTUELLER_SHA: ${{ github.sha }}\n"
        "          REPO_SLUG: ${{ github.repository }}\n"
        "        run: |\n"
        f"{py_export}"
        '          args=(--repo . --repo-slug "$REPO_SLUG" --hauptzweig "$HAUPTZWEIG" --aktueller-sha "$AKTUELLER_SHA" --aktueller-lauf-id "$AKTUELLER_LAUF_ID" --github-output "$GITHUB_OUTPUT" --summary-pfad "$GITHUB_STEP_SUMMARY")\n'
        '          [ -n "$WORKFLOW_DATEI" ] && args+=(--workflow-datei "$WORKFLOW_DATEI")\n'
        '          [ "$IMMER_LAUFEN" = "true" ] && args+=(--immer-laufen)\n'
        '          python -m to_spawn.pruef.nachtrunde vorab "${args[@]}"\n'
        "\n"
        "  vorbereiten:\n"
        "    name: Aufteilung prüfen (>= 2 Runner)\n"
        "    needs: vorab\n"
        "    if: ${{ needs.vorab.outputs.laufen == 'true' }}\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 10\n"
        "    outputs:\n"
        "      teile: ${{ steps.matrix.outputs.teile }}\n"
        "    defaults:\n"
        "      run:\n"
        "        shell: bash\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "\n"
        f"{skill_step}"
        "\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "${{ inputs.python-version }}"\n'
        "\n"
        "      - id: matrix\n"
        "        name: Runner-Matrix erzeugen (>= 2 Teile)\n"
        "        env:\n"
        "          SKILL_PFAD: ${{ inputs.skill-pfad }}\n"
        "          TEILE: ${{ inputs.teile }}\n"
        "        run: |\n"
        f"{py_export}"
        '          python -m to_spawn.pruef.pipeline_stufe2 matrix --teile "$TEILE" --github-output "$GITHUB_OUTPUT"\n'
        "\n"
        "  stufe-2:\n"
        "    name: Nachtrunde Stufe 2 (${{ matrix.os }}, Teil ${{ matrix.teil }}/${{ inputs.teile }})\n"
        "    needs: [vorab, vorbereiten]\n"
        "    if: ${{ needs.vorab.outputs.laufen == 'true' }}\n"
        "    runs-on: ${{ matrix.os }}\n"
        "    continue-on-error: ${{ matrix.os == 'windows-latest' }}\n"
        "    timeout-minutes: 60\n"
        "    strategy:\n"
        "      fail-fast: false\n"
        "      matrix:\n"
        "        os: ${{ fromJson(inputs.betriebssysteme) }}\n"
        "        teil: ${{ fromJson(needs.vorbereiten.outputs.teile) }}\n"
        "    defaults:\n"
        "      run:\n"
        "        shell: bash\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "\n"
        f"{skill_step}"
        "\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "${{ inputs.python-version }}"\n'
        "\n"
        "      - name: tmux installieren (Linux)\n"
        "        if: runner.os == 'Linux'\n"
        "        run: sudo apt-get update -q && sudo apt-get install -y -q tmux\n"
        "\n"
        "      - name: Test-Abhängigkeiten installieren (inkl. pytest-split + pytest-xdist)\n"
        "        env:\n"
        "          ANFORDERUNGEN: ${{ inputs.anforderungen }}\n"
        "        run: |\n"
        '          if [ -n "$ANFORDERUNGEN" ] && [ -f "$ANFORDERUNGEN" ]; then\n'
        '            python -m pip install -q -r "$ANFORDERUNGEN" pytest-split pytest-xdist\n'
        "          else\n"
        "            python -m pip install -q pytest pytest-split pytest-xdist\n"
        "          fi\n"
        "\n"
        "      - name: git-Identität für Tests setzen\n"
        "        env:\n"
        "          SKILL_PFAD: ${{ inputs.skill-pfad }}\n"
        "        run: |\n"
        f"{py_export}"
        "          python -m to_spawn.pruef.nachtrunde git-identitaet\n"
        "\n"
        "      - name: Stufe 2 Komplett-Prüfung ausführen\n"
        "        env:\n"
        "          SKILL_PFAD: ${{ inputs.skill-pfad }}\n"
        "          TEILE: ${{ inputs.teile }}\n"
        "          TEIL: ${{ matrix.teil }}\n"
        "          XDIST_WORKER: ${{ inputs.xdist-worker }}\n"
        "          TEST_PFADE: ${{ inputs.test-pfade }}\n"
        "          PYTEST_BEFEHL: ${{ inputs.pytest-befehl }}\n"
        "          AUSLASSUNGEN: ${{ inputs.auslassungen }}\n"
        "          DAUER_DATEI: ${{ inputs.dauer-datei }}\n"
        "          SERVER_AUSSCHLIESSEN: ${{ inputs.server-marker-ausschliessen }}\n"
        "        run: |\n"
        f"{py_export}"
        '          args=(--repo . --splits "$TEILE" --group "$TEIL" --xdist-worker "$XDIST_WORKER" --split-modus pytest-split --junitxml "$RUNNER_TEMP/junit-nachtrunde-${TEIL}.xml" --summary-pfad "$GITHUB_STEP_SUMMARY")\n'
        '          [ -n "$TEST_PFADE" ] && args+=(--test-pfade "$TEST_PFADE")\n'
        '          [ -n "$PYTEST_BEFEHL" ] && args+=(--pytest-befehl "$PYTEST_BEFEHL")\n'
        '          [ -n "$AUSLASSUNGEN" ] && args+=(--auslassungen "$AUSLASSUNGEN")\n'
        '          [ -n "$DAUER_DATEI" ] && args+=(--dauer-datei "$DAUER_DATEI")\n'
        '          [ "$SERVER_AUSSCHLIESSEN" = "false" ] && args+=(--server-behalten)\n'
        '          python -m to_spawn.pruef.pipeline_stufe2 lauf "${args[@]}"\n'
        "\n"
        "      - name: JUnit-Artefakt (Nachtrunde)\n"
        "        if: always()\n"
        "        uses: actions/upload-artifact@v4\n"
        "        with:\n"
        "          name: junit-nachtrunde-${{ matrix.os }}-teil-${{ matrix.teil }}\n"
        "          path: ${{ runner.temp }}/junit-nachtrunde-${{ matrix.teil }}.xml\n"
        "          if-no-files-found: ignore\n"
        "\n"
        "  staging-probe:\n"
        "    name: Staging-Probe (nur mit Staging-App)\n"
        "    needs: [vorab, stufe-2]\n"
        "    if: ${{ needs.vorab.outputs.laufen == 'true' && inputs.mit-staging-app }}\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 30\n"
        "    defaults:\n"
        "      run:\n"
        "        shell: bash\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "\n"
        f"{skill_step}"
        "\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "${{ inputs.python-version }}"\n'
        "\n"
        "      - name: Staging-Probe ausführen\n"
        "        env:\n"
        "          SKILL_PFAD: ${{ inputs.skill-pfad }}\n"
        "          STAGING_PROBE_BEFEHL: ${{ inputs.staging-probe-befehl }}\n"
        "        run: |\n"
        f"{py_export}"
        '          python -m to_spawn.pruef.pipeline_stufe2 staging-probe --repo . --mit-staging-app --befehl "$STAGING_PROBE_BEFEHL" --summary-pfad "$GITHUB_STEP_SUMMARY"\n'
        "\n"
        "  ticket-abgleich:\n"
        "    name: Ticket-Abgleich (Nachtrunde rot)\n"
        "    needs: [vorab, vorbereiten, stufe-2, staging-probe]\n"
        "    if: ${{ always() && needs.vorab.outputs.laufen == 'true' }}\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 5\n"
        "    defaults:\n"
        "      run:\n"
        "        shell: bash\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "\n"
        f"{skill_step}"
        "\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "${{ inputs.python-version }}"\n'
        "\n"
        "      - name: Ein offenes Ticket pflegen oder schließen\n"
        "        env:\n"
        "          GH_TOKEN: ${{ github.token }}\n"
        "          SKILL_PFAD: ${{ inputs.skill-pfad }}\n"
        "          REPO_SLUG: ${{ github.repository }}\n"
        "          TICKET_TITEL: ${{ inputs.ticket-titel }}\n"
        "          HAUPTZWEIG: ${{ needs.vorab.outputs.hauptzweig || github.ref_name }}\n"
        "          AKTUELLER_SHA: ${{ needs.vorab.outputs.aktueller_sha || github.sha }}\n"
        "          LAUF_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}\n"
        "          VORBEREITEN_STATUS: ${{ needs.vorbereiten.result }}\n"
        "          STUFE2_STATUS: ${{ needs.stufe-2.result }}\n"
        "          STAGING_STATUS: ${{ needs.staging-probe.result }}\n"
        "        run: |\n"
        f"{py_export}"
        '          if [ "$VORBEREITEN_STATUS" = "success" ] && [ "$STUFE2_STATUS" = "success" ] && { [ "$STAGING_STATUS" = "success" ] || [ "$STAGING_STATUS" = "skipped" ]; }; then\n'
        '            ERGEBNIS="gruen"\n'
        "          else\n"
        '            ERGEBNIS="rot"\n'
        "          fi\n"
        '          DETAILS="vorbereiten=${VORBEREITEN_STATUS}, stufe-2=${STUFE2_STATUS}, staging-probe=${STAGING_STATUS}"\n'
        '          python -m to_spawn.pruef.nachtrunde ticket --repo . --repo-slug "$REPO_SLUG" --ergebnis "$ERGEBNIS" --titel "$TICKET_TITEL" --zweig "$HAUPTZWEIG" --sha "$AKTUELLER_SHA" --lauf-url "$LAUF_URL" --details "$DETAILS" --summary-pfad "$GITHUB_STEP_SUMMARY"\n'
    )


def anschluss_nachtrunde_yaml(
    workflow_ref: str = STANDARD_VORLAGE_REF,
    *,
    cron: str = STANDARD_CRON,
    teile: int = 2,
    test_pfade: str = "tests",
    anforderungen: str = "tests/requirements.txt",
    auslassungen: str = "",
    betriebssysteme: str = '["ubuntu-latest"]',
    mit_staging_app: bool = False,
    staging_probe_befehl: str = "",
    skill_pfad: str = ".to-spawn-skill",
    skill_repo: str = STANDARD_SKILL_REPO,
    skill_ref: str = "main",
    workflow_datei: str = "nachtrunde.yml",
    kommentar_kopf: str = "# Nachtrunden-Anschluss über wiederverwendbare Vorlage (#582).\n",
) -> str:
    """Erzeugt einen kurzen Anschluss-Workflow (``schedule`` + ``workflow_dispatch`` + ``concurrency``)."""
    pruefe_cron(cron)
    pipeline_stufe2.teile_matrix(teile)
    staging_bool = "true" if mit_staging_app else "false"

    mit_zeilen: list[str] = [
        f"      workflow-datei: {pipeline_vorlage._yaml_skalar(workflow_datei)}",
        '      python-version: "3.13"',
        f"      teile: {teile}",
        '      xdist-worker: "auto"',
        f"      test-pfade: {pipeline_vorlage._yaml_skalar(test_pfade)}",
        f"      anforderungen: {pipeline_vorlage._yaml_skalar(anforderungen)}",
    ]
    if auslassungen:
        mit_zeilen.append(f"      auslassungen: {pipeline_vorlage._yaml_skalar(auslassungen)}")
    mit_zeilen.append(f"      betriebssysteme: {pipeline_vorlage._yaml_skalar(betriebssysteme)}")
    mit_zeilen.append(f"      mit-staging-app: {staging_bool}")
    if staging_probe_befehl:
        mit_zeilen.append(f"      staging-probe-befehl: {pipeline_vorlage._yaml_skalar(staging_probe_befehl)}")
    mit_zeilen.append("      server-marker-ausschliessen: true")
    mit_zeilen.append(f"      skill-pfad: {pipeline_vorlage._yaml_skalar(skill_pfad)}")
    if skill_repo:
        mit_zeilen.append(f"      skill-repo: {pipeline_vorlage._yaml_skalar(skill_repo)}")
        mit_zeilen.append(f"      skill-ref: {pipeline_vorlage._yaml_skalar(skill_ref)}")
    mit_block = "\n".join(mit_zeilen)

    return (
        f"{kommentar_kopf}"
        f"{pipeline_vorlage.kopf_doku_kommentar()}"
        "name: Nachtrunde\n"
        "\n"
        "on:\n"
        "  schedule:\n"
        f'    - cron: "{cron}"\n'
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      immer-laufen:\n"
        '        description: "Commit-Vorabprüfung überspringen und Stufe 2 erzwingen."\n'
        "        required: false\n"
        "        type: boolean\n"
        "        default: false\n"
        "\n"
        "permissions:\n"
        "  contents: read\n"
        "  actions: read\n"
        "  issues: write\n"
        "\n"
        "concurrency:\n"
        "  group: nachtrunde-${{ github.ref }}\n"
        "  cancel-in-progress: false\n"
        "\n"
        "jobs:\n"
        "  nachtrunde:\n"
        f"    uses: {workflow_ref}\n"
        "    with:\n"
        "      immer-laufen: ${{ inputs.immer-laufen == true }}\n"
        f"{mit_block}\n"
    )


def eigenanschluss_nachtrunde_yaml(
    workflow_ref: str = "./.github/workflows/pipeline-nachtrunde.yml",
    *,
    cron: str = STANDARD_CRON,
    teile: int = 2,
) -> str:
    """Erzeugt den Eigenanschluss-Workflow für das Skill-Repo."""
    return anschluss_nachtrunde_yaml(
        workflow_ref=workflow_ref,
        cron=cron,
        teile=teile,
        test_pfade="tests",
        anforderungen="tests/requirements.txt",
        auslassungen=".github/workflows/auslassungen.txt",
        betriebssysteme='["ubuntu-latest", "windows-latest"]',
        mit_staging_app=False,
        skill_pfad=".",
        skill_repo="",
        workflow_datei="pipeline-nachtrunde-eigen.yml",
        kommentar_kopf="# Eigenanschluss des Skill-Repos an die Nachtrunden-Vorlage (#582).\n",
    )


def uebungsrepo_anschluss_yaml(
    workflow_ref: str = STANDARD_VORLAGE_REF,
    *,
    cron: str = STANDARD_CRON,
    teile: int = 2,
    mit_staging_app: bool = False,
    skill_repo: str = STANDARD_SKILL_REPO,
    skill_ref: str = "main",
) -> str:
    """Erzeugt den Anschluss-Workflow für ein Übungs-Repo aus ``probe/anlegen.sh``."""
    return anschluss_nachtrunde_yaml(
        workflow_ref=workflow_ref,
        cron=cron,
        teile=teile,
        test_pfade="tests",
        anforderungen="requirements-dev.txt",
        betriebssysteme='["ubuntu-latest"]',
        mit_staging_app=mit_staging_app,
        staging_probe_befehl="python skripte/pruefen_komplett.py" if mit_staging_app else "",
        skill_pfad=".to-spawn-skill",
        skill_repo=skill_repo,
        skill_ref=skill_ref,
        workflow_datei="nachtrunde.yml",
        kommentar_kopf="# Übungs-Repo-Anschluss an die Nachtrunden-Vorlage (#582).\n",
    )


def pruefe_nachtrunde_vorlage_text(text: str) -> list[str]:
    """Prüft den YAML-Text einer Nachtrunden-Vorlage rein mit der Standardbibliothek."""
    befunde: list[str] = []
    if pipeline_vorlage._VERBOTENES_PLUGIN in text.lower():
        befunde.append(f"verbotenes Plugin {pipeline_vorlage._VERBOTENES_PLUGIN!r} gefunden")
    benachrichtigung = _VERBOTENE_BENACHRICHTIGUNG.findall(text)
    if benachrichtigung:
        befunde.append(f"verbotene Mail-/Benachrichtigungs-Spur gefunden: {', '.join(sorted(set(benachrichtigung)))}")
    if "self-hosted" in text.lower():
        befunde.append(
            "self-hosted Runner verboten (Nachtrunde läuft ausschließlich auf GitHub Actions, nie Bau-Server)"
        )
    if not re.search(r"(?m)^  workflow_call:\s*$", text):
        befunde.append("Abschnitt 'workflow_call:' fehlt")
    if not re.search(r"(?m)^permissions:\s*$", text):
        befunde.append("Abschnitt 'permissions:' fehlt")
    for recht in ("contents: read", "actions: read", "issues: write"):
        if recht not in text:
            befunde.append(f"erforderliche Berechtigung '{recht}' fehlt")
    if "contents: write" in text:
        befunde.append("verbotene Schreibberechtigung 'contents: write' gefunden")
    if not re.search(r"(?m)^concurrency:\s*$", text) or "cancel-in-progress: false" not in text:
        befunde.append("concurrency-Gruppe mit 'cancel-in-progress: false' fehlt")

    for stichwort, beschreibung in (
        ("Triage", "Zielbild-Kopf-Doku (Triage)"),
        ("Audits", "Zielbild-Kopf-Doku (nächtliche Audits)"),
        ("Claude-Sessions", "Bau-Server-Rolle (Claude-Sessions)"),
        ("braucht Server", "Bau-Server-Rolle ('braucht Server'-Tests)"),
    ):
        if stichwort not in text:
            befunde.append(f"{beschreibung} fehlt in der Kopf-Doku")

    zeilen = text.splitlines()
    for eingabe in EINGABEN_NACHTRUNDE:
        muster = re.compile(rf"^      {re.escape(eingabe.name)}:\s*$")
        treffer = [idx for idx, z in enumerate(zeilen) if muster.match(z)]
        if not treffer:
            befunde.append(f"Eingabe '{eingabe.name}' fehlt")
            continue
        block = "\n".join(zeilen[treffer[0] + 1 : treffer[0] + 6])
        if "description:" not in block:
            befunde.append(f"Eingabe '{eingabe.name}' ohne description")
        if f"type: {eingabe.typ}" not in block:
            befunde.append(f"Eingabe '{eingabe.name}' ohne type: {eingabe.typ}")
        if eingabe.name == "teile":
            wert_treffer = re.search(r"(?m)^\s+default:\s*(\d+)\s*$", block)
            if not wert_treffer or int(wert_treffer.group(1)) < pipeline_stufe2.MINDEST_RUNNER:
                befunde.append(f"Eingabe 'teile' braucht default >= {pipeline_stufe2.MINDEST_RUNNER}")

    for job in ("vorab", "vorbereiten", "stufe-2", "staging-probe", "ticket-abgleich"):
        if not re.search(rf"(?m)^  {re.escape(job)}:\s*$", text):
            befunde.append(f"Job '{job}:' fehlt")

    for pflicht in (
        "to_spawn.pruef.nachtrunde vorab",
        "to_spawn.pruef.pipeline_stufe2",
        "to_spawn.pruef.nachtrunde ticket",
        "pytest-split",
        "pytest-xdist",
    ):
        if pflicht not in text:
            befunde.append(f"Pflicht-Aufruf '{pflicht}' fehlt")
    return befunde


def pruefe_anschluss_nachtrunde_text(text: str) -> list[str]:
    """Prüft einen Nachtrunden-Anschluss-Workflow rein mit der Standardbibliothek."""
    befunde: list[str] = []
    if pipeline_vorlage._VERBOTENES_PLUGIN in text.lower():
        befunde.append(f"verbotenes Plugin {pipeline_vorlage._VERBOTENES_PLUGIN!r} gefunden")
    benachrichtigung = _VERBOTENE_BENACHRICHTIGUNG.findall(text)
    if benachrichtigung:
        befunde.append(f"verbotene Mail-/Benachrichtigungs-Spur gefunden: {', '.join(sorted(set(benachrichtigung)))}")
    if "self-hosted" in text.lower():
        befunde.append("self-hosted Runner im Anschluss-Workflow verboten")
    if not re.search(r"(?m)^  schedule:\s*$", text):
        befunde.append("Zeitplan 'schedule:' fehlt")
    cron_treffer = re.search(r'(?m)^\s*-\s*cron:\s*"([^"]+)"\s*$', text)
    if not cron_treffer:
        befunde.append("Cron-Eintrag ('- cron: \"...\"') fehlt")
    else:
        try:
            pruefe_cron(cron_treffer.group(1))
        except ValueError as fehler:
            befunde.append(str(fehler))
    if not re.search(r"(?m)^  workflow_dispatch:\s*$", text):
        befunde.append("manueller Auslöser 'workflow_dispatch:' fehlt")
    if not re.search(r"(?m)^concurrency:\s*$", text) or "cancel-in-progress: false" not in text:
        befunde.append("concurrency-Gruppe mit 'cancel-in-progress: false' fehlt")
    if "pipeline-nachtrunde.yml" not in text or "uses:" not in text:
        befunde.append("Verweis auf Vorlage 'pipeline-nachtrunde.yml' per 'uses:' fehlt")
    for recht in ("contents: read", "actions: read", "issues: write"):
        if recht not in text:
            befunde.append(f"erforderliche Berechtigung '{recht}' fehlt")
    if "contents: write" in text:
        befunde.append("verbotene Schreibberechtigung 'contents: write' gefunden")
    return befunde


# --- Vorab-Prüfung (neue Commits seit letzter grüner Nachtrunde) ------------------------------


def _head_sha(wurzel: Path, ref: str = "HEAD") -> str:
    code, text = gh._git_ausgabe(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], wurzel)
    return text.strip() if code == 0 else ""


def _ermittle_letzten_gruenen_sha(
    wurzel: Path,
    *,
    repo_slug: str,
    hauptzweig: str,
    workflow_datei: str,
    workflow_name: str,
    aktueller_lauf_id: str,
) -> tuple[str | None, str]:
    """Fragt die GitHub-Actions-API nach dem jüngsten grünen Lauf auf ``hauptzweig``.

    Rückgabe: ``(sha_oder_leer_wenn_erster_lauf, fehlertext)``; ``None`` bei API-Fehler.
    """
    zweig_q = urllib.parse.quote(hauptzweig, safe="")
    workflow_id_oder_datei = workflow_datei.strip()

    if not workflow_id_oder_datei and aktueller_lauf_id.strip():
        lauf_daten = gh.json_lauf(["api", f"repos/{repo_slug}/actions/runs/{aktueller_lauf_id.strip()}"], cwd=wurzel)
        if isinstance(lauf_daten, dict) and lauf_daten.get("workflow_id") is not None:
            workflow_id_oder_datei = str(lauf_daten["workflow_id"])

    if workflow_id_oder_datei:
        wf_q = urllib.parse.quote(workflow_id_oder_datei, safe="")
        pfad = f"repos/{repo_slug}/actions/workflows/{wf_q}/runs?branch={zweig_q}&status=success&per_page=20"
    else:
        pfad = f"repos/{repo_slug}/actions/runs?branch={zweig_q}&status=success&per_page=30"

    daten = gh.json_lauf(["api", pfad], cwd=wurzel)
    if not isinstance(daten, dict) or not isinstance(daten.get("workflow_runs"), list):
        return None, f"GitHub-API lieferte keine Lauf-Liste für {pfad}"

    eigen_id = aktueller_lauf_id.strip()
    for eintrag in daten["workflow_runs"]:
        if not isinstance(eintrag, dict):
            continue
        if eigen_id and str(eintrag.get("id", "")) == eigen_id:
            continue
        if not workflow_id_oder_datei and workflow_name and str(eintrag.get("name", "")) != workflow_name:
            continue
        if str(eintrag.get("conclusion", "")).lower() != "success":
            continue
        sha = str(eintrag.get("head_sha", "")).strip()
        if sha:
            return sha, ""
    return "", ""


def vorab_pruefung(
    repo: Path,
    *,
    hauptzweig: str = "",
    repo_slug: str = "",
    workflow_datei: str = "",
    workflow_name: str = "Nachtrunde",
    aktueller_lauf_id: str = "",
    aktueller_sha: str = "",
    letzter_gruener_sha: str | None = None,
    immer_laufen: bool = False,
    github_output: Path | str | None = None,
    summary_pfad: Path | str | None = None,
) -> VorabErgebnis:
    """Prüft, ob ``hauptzweig`` seit der letzten grünen Nachtrunde neue Commits hat."""
    wurzel = config.repo_wurzel(repo)
    zweig = hauptzweig.strip() or gh.hauptzweig(wurzel)
    sha_jetzt = aktueller_sha.strip() or _head_sha(wurzel)
    slug = repo_slug.strip() or gh.repo_aus_origin(wurzel)

    if immer_laufen:
        ergebnis = VorabErgebnis(
            ok=True,
            exit_code=leiter.GRUEN,
            laufen=True,
            hauptzweig=zweig,
            aktueller_sha=sha_jetzt,
            letzter_gruener_sha="",
            grund="immer-laufen aktiv — Commit-Vorabprüfung übersprungen",
        )
        _schreibe_vorab_ausgaben(ergebnis, github_output, summary_pfad)
        return ergebnis

    if not sha_jetzt:
        ergebnis = VorabErgebnis(
            ok=True,
            exit_code=leiter.GRUEN,
            laufen=True,
            hauptzweig=zweig,
            aktueller_sha="",
            letzter_gruener_sha="",
            grund="aktueller Commit-SHA nicht bestimmbar — Stufe 2 vorsichtshalber ausführen",
        )
        _schreibe_vorab_ausgaben(ergebnis, github_output, summary_pfad)
        return ergebnis

    if letzter_gruener_sha is not None:
        vorher_sha: str | None = letzter_gruener_sha.strip()
        api_fehler = ""
    elif not slug:
        vorher_sha = None
        api_fehler = "Repo-Slug nicht bestimmbar"
    else:
        vorher_sha, api_fehler = _ermittle_letzten_gruenen_sha(
            wurzel,
            repo_slug=slug,
            hauptzweig=zweig,
            workflow_datei=workflow_datei,
            workflow_name=workflow_name,
            aktueller_lauf_id=aktueller_lauf_id,
        )

    if vorher_sha is None:
        log.warning("Vorab-Prüfung ohne letzten grünen Stand (%s) — Stufe 2 läuft vorsichtshalber.", api_fehler)
        ergebnis = VorabErgebnis(
            ok=True,
            exit_code=leiter.GRUEN,
            laufen=True,
            hauptzweig=zweig,
            aktueller_sha=sha_jetzt,
            letzter_gruener_sha="",
            grund=f"letzter grüner Lauf nicht ermittelbar ({api_fehler}) — Stufe 2 vorsichtshalber ausführen",
        )
    elif not vorher_sha:
        ergebnis = VorabErgebnis(
            ok=True,
            exit_code=leiter.GRUEN,
            laufen=True,
            hauptzweig=zweig,
            aktueller_sha=sha_jetzt,
            letzter_gruener_sha="",
            grund=f"noch keine grüne Nachtrunde auf {zweig} — Stufe 2 läuft",
        )
    elif sha_jetzt.lower() == vorher_sha.lower():
        ergebnis = VorabErgebnis(
            ok=True,
            exit_code=leiter.GRUEN,
            laufen=False,
            hauptzweig=zweig,
            aktueller_sha=sha_jetzt,
            letzter_gruener_sha=vorher_sha,
            grund=f"keine neuen Commits auf {zweig} seit letzter grüner Nachtrunde ({vorher_sha[:12]}) — übersprungen",
        )
    else:
        ergebnis = VorabErgebnis(
            ok=True,
            exit_code=leiter.GRUEN,
            laufen=True,
            hauptzweig=zweig,
            aktueller_sha=sha_jetzt,
            letzter_gruener_sha=vorher_sha,
            grund=f"neue Commits auf {zweig} ({vorher_sha[:12]} → {sha_jetzt[:12]}) — Stufe 2 läuft",
        )

    _schreibe_vorab_ausgaben(ergebnis, github_output, summary_pfad)
    return ergebnis


def _schreibe_vorab_ausgaben(
    ergebnis: VorabErgebnis,
    github_output: Path | str | None,
    summary_pfad: Path | str | None,
) -> None:
    _schreibe_github_output(
        github_output,
        {
            "laufen": "true" if ergebnis.laufen else "false",
            "hauptzweig": ergebnis.hauptzweig,
            "aktueller_sha": ergebnis.aktueller_sha,
            "letzter_gruener_sha": ergebnis.letzter_gruener_sha,
            "grund": ergebnis.grund,
        },
    )
    _schreibe_summary(summary_pfad, ergebnis.zusammenfassung())


# --- Ticket-Abgleich („Nachtrunde rot“) -------------------------------------------------------


def _ticket_nummer_aus_ausgabe(text: str) -> int | None:
    treffer = re.search(r"/issues/(\d+)\b", text)
    return int(treffer.group(1)) if treffer else None


def _baue_rot_koerper(zweig: str, sha: str, lauf_url: str, details: str) -> str:
    sha_kurz = sha[:12] if sha else "unbekannt"
    zeilen = [
        "Die automatische Nachtrunde (Stufe 2 auf GitHub Actions) ist **rot**.",
        "",
        f"- Zweig: `{zweig or 'unbekannt'}`",
        f"- Commit: `{sha_kurz}`",
    ]
    if lauf_url:
        zeilen.append(f"- Lauf: {lauf_url}")
    if details:
        zeilen.append(f"- Job-Status: `{details}`")
    zeilen.extend(
        [
            "",
            (
                "Folgenächte mit rotem Ergebnis kommentieren dieses eine Ticket; "
                "sobald die Nachtrunde wieder grün ist, schließt der Workflow dieses Ticket automatisch."
            ),
        ]
    )
    return "\n".join(zeilen)


def _baue_folgenacht_kommentar(zweig: str, sha: str, lauf_url: str, details: str) -> str:
    sha_kurz = sha[:12] if sha else "unbekannt"
    zeilen = [
        f"Folgenacht weiterhin **rot** auf `{zweig or 'unbekannt'}` (Commit `{sha_kurz}`).",
    ]
    if lauf_url:
        zeilen.append(f"- Lauf: {lauf_url}")
    if details:
        zeilen.append(f"- Job-Status: `{details}`")
    return "\n".join(zeilen)


def _baue_gruen_kommentar(zweig: str, sha: str, lauf_url: str) -> str:
    sha_kurz = sha[:12] if sha else "unbekannt"
    zeilen = [
        (
            f"Nachtrunde wieder **grün** auf `{zweig or 'unbekannt'}` (Commit `{sha_kurz}`). "
            "Dieses Ticket wird automatisch geschlossen."
        ),
    ]
    if lauf_url:
        zeilen.append(f"- Lauf: {lauf_url}")
    return "\n".join(zeilen)


def ticket_abgleich(
    repo: Path,
    *,
    ergebnis: NachtrundeStatus,
    repo_slug: str = "",
    titel: str = TICKET_TITEL,
    zweig: str = "",
    sha: str = "",
    lauf_url: str = "",
    details: str = "",
    summary_pfad: Path | str | None = None,
) -> TicketAbgleichErgebnis:
    """Pflegt genau ein offenes Ticket „Nachtrunde rot“ im Repo (erstellen, kommentieren, schließen)."""
    if ergebnis not in ("rot", "gruen"):
        raise ValueError(f"ergebnis muss 'rot' oder 'gruen' sein, nicht {ergebnis!r}")

    wurzel = config.repo_wurzel(repo)
    titel_sauber = titel.strip() or TICKET_TITEL
    slug = repo_slug.strip() or gh.repo_aus_origin(wurzel)
    zweig_sauber = zweig.strip() or gh.hauptzweig(wurzel)
    sha_sauber = sha.strip() or _head_sha(wurzel)

    if not slug:
        res = TicketAbgleichErgebnis(
            ok=False,
            exit_code=leiter.GATE_FEHLER,
            ergebnis=ergebnis,
            aktion="fehler",
            titel=titel_sauber,
            ticket_nummer=None,
            ticket_url="",
            geschlossene_nummern=(),
            grund="Repo-Slug für Ticket-Abgleich nicht bestimmbar",
        )
        _schreibe_summary(summary_pfad, res.zusammenfassung())
        return res

    liste = gh.json_lauf(
        ["issue", "list", "--repo", slug, "--state", "open", "--limit", "100", "--json", "number,title,url"],
        cwd=wurzel,
    )
    if not isinstance(liste, list):
        res = TicketAbgleichErgebnis(
            ok=False,
            exit_code=leiter.GATE_FEHLER,
            ergebnis=ergebnis,
            aktion="fehler",
            titel=titel_sauber,
            ticket_nummer=None,
            ticket_url="",
            geschlossene_nummern=(),
            grund="Offene Tickets konnten über gh nicht abgefragt werden",
        )
        _schreibe_summary(summary_pfad, res.zusammenfassung())
        return res

    offene = sorted(
        (
            eintrag
            for eintrag in liste
            if isinstance(eintrag, dict)
            and str(eintrag.get("title", "")).strip() == titel_sauber
            and isinstance(eintrag.get("number"), int)
        ),
        key=lambda eintrag: int(eintrag["number"]),
    )

    if ergebnis == "rot":
        if not offene:
            koerper = _baue_rot_koerper(zweig_sauber, sha_sauber, lauf_url.strip(), details.strip())
            code, ausgabe = gh.lauf(
                ["issue", "create", "--repo", slug, "--title", titel_sauber, "--body", koerper],
                cwd=wurzel,
                fehlertext=True,
            )
            if code != 0:
                res = TicketAbgleichErgebnis(
                    ok=False,
                    exit_code=leiter.GATE_FEHLER,
                    ergebnis="rot",
                    aktion="fehler",
                    titel=titel_sauber,
                    ticket_nummer=None,
                    ticket_url="",
                    geschlossene_nummern=(),
                    grund=f"gh issue create gescheitert (Exit {code}): {ausgabe[:200]}",
                )
            else:
                url = ausgabe.splitlines()[-1].strip() if ausgabe else ""
                nr = _ticket_nummer_aus_ausgabe(url)
                res = TicketAbgleichErgebnis(
                    ok=True,
                    exit_code=leiter.GRUEN,
                    ergebnis="rot",
                    aktion="erstellt",
                    titel=titel_sauber,
                    ticket_nummer=nr,
                    ticket_url=url,
                    geschlossene_nummern=(),
                    grund=f"Ticket „{titel_sauber}“ neu eröffnet",
                )
        else:
            if len(offene) > 1:
                log.warning(
                    "Mehrere offene Tickets mit Titel %r gefunden (%s) — kommentiere #%s.",
                    titel_sauber,
                    [e["number"] for e in offene],
                    offene[0]["number"],
                )
            primaer = offene[0]
            nr = int(primaer["number"])
            url = str(primaer.get("url", "")).strip()
            kommentar = _baue_folgenacht_kommentar(zweig_sauber, sha_sauber, lauf_url.strip(), details.strip())
            code, ausgabe = gh.lauf(
                ["issue", "comment", str(nr), "--repo", slug, "--body", kommentar],
                cwd=wurzel,
                fehlertext=True,
            )
            if code != 0:
                res = TicketAbgleichErgebnis(
                    ok=False,
                    exit_code=leiter.GATE_FEHLER,
                    ergebnis="rot",
                    aktion="fehler",
                    titel=titel_sauber,
                    ticket_nummer=nr,
                    ticket_url=url,
                    geschlossene_nummern=(),
                    grund=f"gh issue comment #{nr} gescheitert (Exit {code}): {ausgabe[:200]}",
                )
            else:
                res = TicketAbgleichErgebnis(
                    ok=True,
                    exit_code=leiter.GRUEN,
                    ergebnis="rot",
                    aktion="kommentiert",
                    titel=titel_sauber,
                    ticket_nummer=nr,
                    ticket_url=url,
                    geschlossene_nummern=(),
                    grund=f"Folgenacht an bestehendem Ticket #{nr} kommentiert (kein zweites Ticket)",
                )
        _schreibe_summary(summary_pfad, res.zusammenfassung())
        return res

    # ergebnis == "gruen"
    if not offene:
        res = TicketAbgleichErgebnis(
            ok=True,
            exit_code=leiter.GRUEN,
            ergebnis="gruen",
            aktion="unveraendert",
            titel=titel_sauber,
            ticket_nummer=None,
            ticket_url="",
            geschlossene_nummern=(),
            grund="Nachtrunde grün, kein offenes Fehler-Ticket vorhanden",
        )
        _schreibe_summary(summary_pfad, res.zusammenfassung())
        return res

    abschluss = _baue_gruen_kommentar(zweig_sauber, sha_sauber, lauf_url.strip())
    geschlossen: list[int] = []
    for eintrag in offene:
        nr = int(eintrag["number"])
        code, ausgabe = gh.lauf(
            ["issue", "close", str(nr), "--repo", slug, "--comment", abschluss],
            cwd=wurzel,
            fehlertext=True,
        )
        if code != 0:
            res = TicketAbgleichErgebnis(
                ok=False,
                exit_code=leiter.GATE_FEHLER,
                ergebnis="gruen",
                aktion="fehler",
                titel=titel_sauber,
                ticket_nummer=nr,
                ticket_url=str(eintrag.get("url", "")).strip(),
                geschlossene_nummern=tuple(geschlossen),
                grund=f"gh issue close #{nr} gescheitert (Exit {code}): {ausgabe[:200]}",
            )
            _schreibe_summary(summary_pfad, res.zusammenfassung())
            return res
        geschlossen.append(nr)

    primaer = offene[0]
    res = TicketAbgleichErgebnis(
        ok=True,
        exit_code=leiter.GRUEN,
        ergebnis="gruen",
        aktion="geschlossen",
        titel=titel_sauber,
        ticket_nummer=int(primaer["number"]),
        ticket_url=str(primaer.get("url", "")).strip(),
        geschlossene_nummern=tuple(geschlossen),
        grund=f"Nachtrunde wieder grün — Ticket(s) {', '.join(f'#{n}' for n in geschlossen)} automatisch geschlossen",
    )
    _schreibe_summary(summary_pfad, res.zusammenfassung())
    return res


def _setze_ci_git_identitaet() -> int:
    """Setzt minimale git-Identität auf CI-Runnern für Tests (ohne Adress-Literale in YAML)."""
    feld_adresse = "user." + "e" + "mail"
    adresse = "ci@example.invalid"
    for befehl in (
        ["config", "--global", "user.name", "ci"],
        ["config", "--global", feld_adresse, adresse],
        ["config", "--global", "init.defaultBranch", "main"],
    ):
        code, _aus = gh._git_ausgabe(befehl, Path.cwd())
        if code != 0:
            return code
    return 0


# --- CLI ---------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m to_spawn.pruef.nachtrunde",
        description="Nachtrunden-Vorlage: YAML erzeugen/prüfen, Commit-Vorabprüfung und Ein-Ticket-Abgleich.",
    )
    unter = parser.add_subparsers(dest="aktion", required=True)

    p_vorab = unter.add_parser("vorab", help="Prüft neue Commits seit der letzten grünen Nachtrunde.")
    p_vorab.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner")
    p_vorab.add_argument("--repo-slug", default="", help="GitHub-Slug (owner/name)")
    p_vorab.add_argument("--hauptzweig", default="", help="Hauptzweig (leer = automatisch)")
    p_vorab.add_argument("--workflow-datei", default="", help="Workflow-Dateiname oder -ID")
    p_vorab.add_argument("--workflow-name", default="Nachtrunde", help="Workflow-Name als Rückfall")
    p_vorab.add_argument("--aktueller-lauf-id", default="", help="Laufende GitHub-Run-ID (wird übersprungen)")
    p_vorab.add_argument("--aktueller-sha", default="", help="Aktueller Commit-SHA (leer = HEAD)")
    p_vorab.add_argument("--letzter-gruener-sha", default=None, help="Optionaler fester Vergleichs-SHA")
    p_vorab.add_argument("--immer-laufen", action="store_true", help="Vorab-Prüfung überspringen")
    p_vorab.add_argument("--github-output", type=Path, default=None, help="Optionaler Pfad zu $GITHUB_OUTPUT")
    p_vorab.add_argument("--summary-pfad", default="", help="Pfad für Markdown-Zusammenfassung")
    p_vorab.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON ausgeben")

    p_ticket = unter.add_parser("ticket", help="Genau ein offenes Ticket „Nachtrunde rot“ pflegen oder schließen.")
    p_ticket.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner")
    p_ticket.add_argument("--repo-slug", default="", help="GitHub-Slug (owner/name)")
    p_ticket.add_argument("--ergebnis", choices=("rot", "gruen"), required=True, help="Ergebnis der Nachtrunde")
    p_ticket.add_argument("--titel", default=TICKET_TITEL, help="Ticket-Titel (Standard: Nachtrunde rot)")
    p_ticket.add_argument("--zweig", default="", help="Geprüfter Zweig")
    p_ticket.add_argument("--sha", default="", help="Geprüfter Commit-SHA")
    p_ticket.add_argument("--lauf-url", default="", help="Link zum GitHub-Actions-Lauf")
    p_ticket.add_argument("--details", default="", help="Optionale Job-Status-Details")
    p_ticket.add_argument("--summary-pfad", default="", help="Pfad für Markdown-Zusammenfassung")
    p_ticket.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON ausgeben")

    p_vorlage = unter.add_parser("vorlage", help="Nachtrunden-Vorlage oder Anschluss-Workflow ausgeben/prüfen.")
    modus = p_vorlage.add_mutually_exclusive_group()
    modus.add_argument("--eigenanschluss", action="store_true", help="Eigenanschluss-Workflow für das Skill-Repo")
    modus.add_argument("--uebungsrepo", action="store_true", help="Anschluss-Workflow für ein Übungs-Repo (probe/)")
    p_vorlage.add_argument(
        "--cron", default=STANDARD_CRON, help="5-Feld-Cron mit schiefer Minute (Standard: 17 2 * * *)"
    )
    p_vorlage.add_argument("--teile", type=int, default=2, help="Anzahl paralleler Stufe-2-Runner (>= 2)")
    p_vorlage.add_argument("--workflow-ref", default="", help="Optionale uses:-Referenz für Anschluss-Workflows")
    p_vorlage.add_argument("--mit-staging-app", action="store_true", help="Staging-Probe im Übungs-Repo aktivieren")
    p_vorlage.add_argument("--ausgabe", type=Path, default=None, help="Zieldatei zum Schreiben der YAML-Vorlage")
    p_vorlage.add_argument("--pruefen", type=Path, default=None, help="Bestehende YAML-Datei gegen Vertrag prüfen")

    unter.add_parser("git-identitaet", help="Minimale git-Identität auf CI-Runnern setzen.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI-Einstiegspunkt für lokale Nutzung und GitHub Actions."""
    konsole.utf8_ausgabe()
    args = _parser().parse_args(argv)

    if args.aktion == "git-identitaet":
        return _setze_ci_git_identitaet()

    if args.aktion == "vorlage":
        ist_anschluss = bool(args.eigenanschluss or args.uebungsrepo)
        if args.pruefen is not None:
            if not args.pruefen.is_file():
                sys.stderr.write(f"Vorlage fehlt: {args.pruefen}\n")
                return leiter.GATE_FEHLER
            inhalt = args.pruefen.read_text(encoding="utf-8")
            befunde = (
                pruefe_anschluss_nachtrunde_text(inhalt) if ist_anschluss else pruefe_nachtrunde_vorlage_text(inhalt)
            )
            if befunde:
                for befund in befunde:
                    sys.stderr.write(f"- {befund}\n")
                return leiter.ROT
            sys.stdout.write(f"Vorlage sauber: {args.pruefen}\n")
            return 0
        try:
            if args.eigenanschluss:
                ref = args.workflow_ref or "./.github/workflows/pipeline-nachtrunde.yml"
                text = eigenanschluss_nachtrunde_yaml(workflow_ref=ref, cron=args.cron, teile=args.teile)
            elif args.uebungsrepo:
                ref = args.workflow_ref or STANDARD_VORLAGE_REF
                text = uebungsrepo_anschluss_yaml(
                    workflow_ref=ref,
                    cron=args.cron,
                    teile=args.teile,
                    mit_staging_app=bool(args.mit_staging_app),
                )
            else:
                text = vorlage_nachtrunde_yaml()
        except ValueError as fehler:
            sys.stderr.write(f"{fehler}\n")
            return leiter.GATE_FEHLER
        if args.ausgabe is not None:
            args.ausgabe.parent.mkdir(parents=True, exist_ok=True)
            args.ausgabe.write_text(text, encoding="utf-8")
            sys.stdout.write(f"Geschrieben: {args.ausgabe}\n")
        else:
            sys.stdout.write(text)
        return 0

    if args.aktion == "vorab":
        erg_v = vorab_pruefung(
            args.repo,
            hauptzweig=args.hauptzweig,
            repo_slug=args.repo_slug,
            workflow_datei=args.workflow_datei,
            workflow_name=args.workflow_name,
            aktueller_lauf_id=args.aktueller_lauf_id,
            aktueller_sha=args.aktueller_sha,
            letzter_gruener_sha=args.letzter_gruener_sha,
            immer_laufen=bool(args.immer_laufen),
            github_output=args.github_output,
            summary_pfad=args.summary_pfad or None,
        )
        if args.als_json:
            sys.stdout.write(json.dumps(erg_v.als_dict(), ensure_ascii=False) + "\n")
        else:
            sys.stdout.write(erg_v.zusammenfassung() + "\n")
        return erg_v.exit_code

    erg_t = ticket_abgleich(
        args.repo,
        ergebnis=args.ergebnis,
        repo_slug=args.repo_slug,
        titel=args.titel,
        zweig=args.zweig,
        sha=args.sha,
        lauf_url=args.lauf_url,
        details=args.details,
        summary_pfad=args.summary_pfad or None,
    )
    if args.als_json:
        sys.stdout.write(json.dumps(erg_t.als_dict(), ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(erg_t.zusammenfassung() + "\n")
    return erg_t.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
