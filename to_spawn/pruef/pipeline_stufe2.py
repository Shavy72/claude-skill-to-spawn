"""Wiederverwendbare Pipeline-Vorlage Stufe 2 (``workflow_call``), Staging-Probe-Schalter und CI-Läufer (#797, #581).

Ergänzt die Stufe-0+1-Vorlage (:mod:`to_spawn.pruef.pipeline_vorlage`) im selben
``workflow_call``-Rahmen an genau einer Stelle:
- :data:`EINGABEN_STUFE2` — dokumentierte Eingaben für ``.github/workflows/pipeline-stufe-2.yml``
  (u. a. ``teile >= 2``, ``xdist-worker``, ``mit-staging-app``, ``staging-probe-befehl``,
  ``server-marker-ausschliessen``).
- :func:`vorlage_stufe2_yaml` — erzeugt die wiederverwendbare ``workflow_call``-Vorlage für Stufe 2
  inkl. gemeinsamer Kopf-Doku (Zielbild + Rolle des Bau-Servers), Aufteilung auf ``>= 2`` Runner
  (``pytest-split`` + ``pytest-xdist``), Ausschluss von Bau-Server-Tests auf GitHub-Runnern und
  bedingtem Staging-Probe-Job.
- :func:`eigenanschluss_stufe2_yaml` — erzeugt den Eigenanschluss-Workflow für das Skill-Repo mit
  Aufteilung auf ``>= 2`` Runner.
- :func:`pruefe_stufe2_vorlage_text` — prüft eine Stufe-2-Vorlage rein mit der Standardbibliothek.
- :func:`stufe2_lauf` / :func:`staging_probe_lauf` — plattformübergreifende Ausführung für Linux
  und Windows (CLI: ``python -m to_spawn.pruef.pipeline_stufe2 {lauf,staging-probe,matrix,vorlage}``).
- :func:`ist_stufe2_dran` — delegiert an :func:`to_spawn.pruefplan.welche` und
  :func:`to_spawn.config.hat_staging` (keine doppelte Regel-Logik).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from to_spawn import config, konsole, pruefplan
from to_spawn.pruef import adapter_py, ausfuehren, leiter, pipeline_vorlage

log = logging.getLogger("to_spawn.pruef.pipeline_stufe2")

#: Mindestanzahl paralleler Runner-Teile für Stufe 2 (#797).
MINDEST_RUNNER = 2
#: Dedizierter pytest-Marker für Bau-Server-Tests (zusätzlich zu ``@pytest.mark.braucht("server")``).
SERVER_MARKER = "braucht_server"
#: Umgebungsvariable, mit der GitHub-Runner Bau-Server-Tests in ``tests/conftest.py`` ausschließen.
SERVER_ENV = "TO_SPAWN_BAU_SERVER"
#: Pytest-Exit-Code bei 0 ausgewählten Tests (z. B. wenn eine Teilmenge leer ist).
_PYTEST_KEINE_TESTS = 5

SplitModus = Literal["auto", "pytest-split", "eingebaut"]

#: Alle dokumentierten Eingaben für ``.github/workflows/pipeline-stufe-2.yml``.
EINGABEN_STUFE2: tuple[pipeline_vorlage.Eingabe, ...] = (
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
        beschreibung="Anzahl paralleler Runner-Teile für pytest-split (mindestens 2).",
    ),
    pipeline_vorlage.Eingabe(
        name="xdist-worker",
        typ="string",
        vorgabe="auto",
        beschreibung="Anzahl pytest-xdist-Worker je Runner (z. B. 'auto' oder '2'; '0' deaktiviert xdist).",
    ),
    pipeline_vorlage.Eingabe(
        name="test-pfade",
        typ="string",
        vorgabe="tests",
        beschreibung="Test-Wurzelordner oder Test-Pfade für die Komplett-Prüfung (leerzeichengetrennt).",
    ),
    pipeline_vorlage.Eingabe(
        name="anforderungen",
        typ="string",
        vorgabe="tests/requirements.txt",
        beschreibung="Pfad zu pip-Abhängigkeiten für Tests (pytest-split und pytest-xdist werden immer sichergestellt).",
    ),
    pipeline_vorlage.Eingabe(
        name="pytest-befehl",
        typ="string",
        vorgabe="",
        beschreibung="Optionale zusätzliche pytest-Argumente (z. B. '--dist worksteal --tb=short').",
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
        beschreibung="Schalter 'mit Staging-App': aktiviert den bedingten Staging-Probe-Schritt (wie config.hat_staging).",
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
        name="skill-pfad",
        typ="string",
        vorgabe=".",
        beschreibung="Pfad zum Skill-Repo im Runner (Eigenanschluss '.', Fremdrepo z. B. '.to-spawn-skill').",
    ),
)


@dataclass(frozen=True)
class Stufe2Ergebnis:
    """Ergebnis eines verteilten Stufe-2-Teillaufs (``group`` von ``splits``)."""

    ok: bool
    exit_code: int
    splits: int
    group: int
    split_modus: str
    xdist_worker: str
    server_ausgeschlossen: bool
    lauf_dauer_s: float
    ausgelassen: tuple[tuple[str, str], ...]
    befehl: tuple[str, ...]
    grund: str
    ausgabe: str = ""

    def als_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "splits": self.splits,
            "group": self.group,
            "split_modus": self.split_modus,
            "xdist_worker": self.xdist_worker,
            "server_ausgeschlossen": self.server_ausgeschlossen,
            "lauf_dauer_s": self.lauf_dauer_s,
            "ausgelassen": [{"id": tid, "grund": grund} for tid, grund in self.ausgelassen],
            "befehl": list(self.befehl),
            "grund": self.grund,
        }

    def zusammenfassung(self, titel: str = "Stufe 2 (Komplett-Prüfung)") -> str:
        """Kurze Markdown-Zusammenfassung für ``GITHUB_STEP_SUMMARY`` oder Protokolle."""
        status = "grün" if self.ok else ("gate-fehler" if self.exit_code == leiter.GATE_FEHLER else "rot")
        server_text = "ausgeschlossen" if self.server_ausgeschlossen else "aktiv"
        zeilen = [
            f"### {titel} — Teil {self.group}/{self.splits}",
            "",
            (
                f"Status: **{status}** · Split: `{self.split_modus}` · xdist: `{self.xdist_worker or 'aus'}` · "
                f"Bau-Server-Tests: {server_text} · ausgelassen: {len(self.ausgelassen)} · "
                f"Lauf {self.lauf_dauer_s:.2f} s"
            ),
            "",
        ]
        if self.grund:
            zeilen.append(f"Hinweis: {self.grund}")
            zeilen.append("")
        if self.ausgelassen:
            zeilen += [f"<details><summary>Ausgelassen ({len(self.ausgelassen)})</summary>", ""]
            zeilen += [f"- `{tid}` — {grund}" if grund else f"- `{tid}`" for tid, grund in self.ausgelassen]
            zeilen += ["", "</details>", ""]
        return "\n".join(zeilen)


@dataclass(frozen=True)
class StagingProbeErgebnis:
    """Ergebnis des bedingten Staging-Probe-Schritts in Stufe 2."""

    ok: bool
    exit_code: int
    mit_staging_app: bool
    ausgefuehrt: bool
    uebersprungen: bool
    lauf_dauer_s: float
    befehl: tuple[str, ...]
    grund: str
    ausgabe: str = ""

    def als_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "mit_staging_app": self.mit_staging_app,
            "ausgefuehrt": self.ausgefuehrt,
            "uebersprungen": self.uebersprungen,
            "lauf_dauer_s": self.lauf_dauer_s,
            "befehl": list(self.befehl),
            "grund": self.grund,
        }

    def zusammenfassung(self, titel: str = "Stufe 2 — Staging-Probe") -> str:
        """Kurze Markdown-Zusammenfassung für ``GITHUB_STEP_SUMMARY`` oder Protokolle."""
        if self.uebersprungen:
            status = "übersprungen (ohne Staging-App)"
        elif self.ok:
            status = "grün"
        elif self.exit_code == leiter.GATE_FEHLER:
            status = "gate-fehler"
        else:
            status = "rot"
        zeilen = [
            f"### {titel}",
            "",
            (
                f"Status: **{status}** · mit Staging-App: `{'ja' if self.mit_staging_app else 'nein'}` · "
                f"Lauf {self.lauf_dauer_s:.2f} s"
            ),
            "",
        ]
        if self.grund:
            zeilen.append(f"Hinweis: {self.grund}")
            zeilen.append("")
        return "\n".join(zeilen)


def teile_matrix(teile: int) -> list[int]:
    """Erzeugt die 1-basierte Runner-Matrix ``[1, ..., teile]`` und verlangt mindestens 2 Runner."""
    if isinstance(teile, bool) or not isinstance(teile, int) or teile < MINDEST_RUNNER:
        raise ValueError(f"Stufe 2 verlangt mindestens {MINDEST_RUNNER} Runner-Teile (erhalten: {teile!r})")
    return list(range(1, teile + 1))


def ist_stufe2_dran(konfig: dict[str, Any], spec_stand: pruefplan.SpecStand) -> tuple[bool, bool]:
    """Fragt allein :func:`pruefplan.welche` und :func:`config.hat_staging` (eine Stelle)."""
    stufe2_dran = pruefplan.welche(konfig, spec_stand) == "komplett"
    return stufe2_dran, config.hat_staging(konfig)


def vorlage_stufe2_yaml() -> str:
    """Erzeugt die wiederverwendbare ``workflow_call``-Vorlage für Stufe 2 (Komplett-Prüfung)."""
    eingabe_zeilen: list[str] = []
    for e in EINGABEN_STUFE2:
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
    return (
        "# Wiederverwendbare Pipeline-Vorlage Stufe 2 (#581, #797).\n"
        f"{pipeline_vorlage.kopf_doku_kommentar()}"
        "# Stufe 2 (Komplett-Prüfung genau einmal je Spec vor Live):\n"
        "# - Verteilte Ausführung auf >= 2 Runner (pytest-split + pytest-xdist).\n"
        "# - Bau-Server-Tests ('braucht Server' / Marker braucht_server) laufen nur auf dem Bau-Server.\n"
        "# - Bedingter Staging-Probe-Schritt läuft ausschließlich bei inputs.mit-staging-app == true.\n"
        "name: Pipeline Stufe 2 (Komplett-Prüfung)\n"
        "\n"
        "on:\n"
        "  workflow_call:\n"
        "    inputs:\n"
        f"{eingaben_block}\n"
        "\n"
        "permissions:\n"
        "  contents: read\n"
        "\n"
        "jobs:\n"
        "  vorbereiten:\n"
        "    name: Aufteilung prüfen (>= 2 Runner)\n"
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
        '          PYTHONPATH="${SKILL_PFAD}:${PYTHONPATH:-}" python -m to_spawn.pruef.pipeline_stufe2 matrix --teile "$TEILE" --github-output "$GITHUB_OUTPUT"\n'
        "\n"
        "  stufe-2:\n"
        "    name: Stufe 2 Komplett (${{ matrix.os }}, Teil ${{ matrix.teil }}/${{ inputs.teile }})\n"
        "    needs: vorbereiten\n"
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
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "${{ inputs.python-version }}"\n'
        "\n"
        "      - name: tmux installieren (Linux)\n"
        "        if: runner.os == 'Linux'\n"
        "        run: sudo apt-get update -q && sudo apt-get install -y -q tmux\n"
        "\n"
        "      - name: Test-Abhängigkeiten installieren (inkl. pytest-split, pytest-xdist + ruff)\n"
        "        env:\n"
        "          ANFORDERUNGEN: ${{ inputs.anforderungen }}\n"
        "        run: |\n"
        '          if [ -n "$ANFORDERUNGEN" ] && [ -f "$ANFORDERUNGEN" ]; then\n'
        '            python -m pip install -q -r "$ANFORDERUNGEN" pytest-split pytest-xdist ruff\n'
        "          else\n"
        "            python -m pip install -q pytest pytest-split pytest-xdist ruff\n"
        "          fi\n"
        "\n"
        "      - name: git-Identität für Tests\n"
        "        run: |\n"
        '          git config --global user.name "ci"\n'
        '          git config --global user.email "ci@example.invalid"\n'
        "          git config --global init.defaultBranch main\n"
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
        '          args=(--repo . --splits "$TEILE" --group "$TEIL" --xdist-worker "$XDIST_WORKER" --split-modus pytest-split --junitxml "$RUNNER_TEMP/junit-stufe2-${TEIL}.xml" --summary-pfad "$GITHUB_STEP_SUMMARY")\n'
        '          [ -n "$TEST_PFADE" ] && args+=(--test-pfade "$TEST_PFADE")\n'
        '          [ -n "$PYTEST_BEFEHL" ] && args+=(--pytest-befehl "$PYTEST_BEFEHL")\n'
        '          [ -n "$AUSLASSUNGEN" ] && args+=(--auslassungen "$AUSLASSUNGEN")\n'
        '          [ -n "$DAUER_DATEI" ] && args+=(--dauer-datei "$DAUER_DATEI")\n'
        '          [ "$SERVER_AUSSCHLIESSEN" = "false" ] && args+=(--server-behalten)\n'
        '          PYTHONPATH="${SKILL_PFAD}:${PYTHONPATH:-}" python -m to_spawn.pruef.pipeline_stufe2 lauf "${args[@]}"\n'
        "\n"
        "      - name: JUnit-Artefakt (Stufe 2)\n"
        "        if: always()\n"
        "        uses: actions/upload-artifact@v4\n"
        "        with:\n"
        "          name: junit-stufe2-${{ matrix.os }}-teil-${{ matrix.teil }}\n"
        "          path: ${{ runner.temp }}/junit-stufe2-${{ matrix.teil }}.xml\n"
        "          if-no-files-found: ignore\n"
        "\n"
        "  staging-probe:\n"
        "    name: Staging-Probe (nur mit Staging-App)\n"
        "    needs: stufe-2\n"
        "    if: ${{ inputs.mit-staging-app }}\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 30\n"
        "    defaults:\n"
        "      run:\n"
        "        shell: bash\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
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
        '          PYTHONPATH="${SKILL_PFAD}:${PYTHONPATH:-}" python -m to_spawn.pruef.pipeline_stufe2 staging-probe --repo . --mit-staging-app --befehl "$STAGING_PROBE_BEFEHL" --summary-pfad "$GITHUB_STEP_SUMMARY"\n'
    )


def eigenanschluss_stufe2_yaml(
    workflow_ref: str = "./.github/workflows/pipeline-stufe-2.yml",
    *,
    teile: int = 2,
    mit_staging_app: bool = False,
) -> str:
    """Erzeugt den Eigenanschluss-Workflow für das Skill-Repo (Stufe 2 mit >= 2 Runnern)."""
    teile_matrix(teile)
    staging_bool = "true" if mit_staging_app else "false"
    return (
        "# Eigenanschluss des Skill-Repos an die Pipeline-Vorlage Stufe 2 (#581, #797).\n"
        f"{pipeline_vorlage.kopf_doku_kommentar()}"
        "name: Pipeline Stufe 2 (Eigenanschluss)\n"
        "\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "  pull_request:\n"
        "    types: [opened, synchronize, reopened, ready_for_review]\n"
        "  push:\n"
        "    branches: [main]\n"
        "\n"
        "permissions:\n"
        "  contents: read\n"
        "\n"
        "concurrency:\n"
        "  group: pipeline-stufe-2-${{ github.ref }}\n"
        "  cancel-in-progress: true\n"
        "\n"
        "jobs:\n"
        "  pipeline-stufe-2:\n"
        f"    uses: {workflow_ref}\n"
        "    with:\n"
        '      python-version: "3.13"\n'
        f"      teile: {teile}\n"
        '      xdist-worker: "auto"\n'
        '      test-pfade: "tests"\n'
        '      anforderungen: "tests/requirements.txt"\n'
        '      auslassungen: ".github/workflows/auslassungen.txt"\n'
        '      betriebssysteme: \'["ubuntu-latest", "windows-latest"]\'\n'
        f"      mit-staging-app: {staging_bool}\n"
        "      server-marker-ausschliessen: true\n"
        '      skill-pfad: "."\n'
    )


def pruefe_stufe2_vorlage_text(text: str) -> list[str]:
    """Prüft den YAML-Text einer Stufe-2-Vorlage rein mit der Standardbibliothek."""
    befunde: list[str] = []
    if pipeline_vorlage._VERBOTENES_PLUGIN in text.lower():
        befunde.append(f"verbotenes Plugin {pipeline_vorlage._VERBOTENES_PLUGIN!r} gefunden")
    if not re.search(r"(?m)^  workflow_call:\s*$", text):
        befunde.append("Abschnitt 'workflow_call:' fehlt")
    if not re.search(r"(?m)^permissions:\s*$", text) or "contents: read" not in text:
        befunde.append("minimale Berechtigung 'permissions: contents: read' fehlt")

    for stichwort, beschreibung in (
        ("Triage", "Zielbild-Kopf-Doku (Triage)"),
        ("Audits", "Zielbild-Kopf-Doku (nächtliche Audits)"),
        ("Claude-Sessions", "Bau-Server-Rolle (Claude-Sessions)"),
        ("braucht Server", "Bau-Server-Rolle ('braucht Server'-Tests)"),
    ):
        if stichwort not in text:
            befunde.append(f"{beschreibung} fehlt in der Kopf-Doku")

    zeilen = text.splitlines()
    for eingabe in EINGABEN_STUFE2:
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
            if not wert_treffer or int(wert_treffer.group(1)) < MINDEST_RUNNER:
                befunde.append(f"Eingabe 'teile' braucht default >= {MINDEST_RUNNER}")

    for job in ("vorbereiten", "stufe-2", "staging-probe"):
        if not re.search(rf"(?m)^  {re.escape(job)}:\s*$", text):
            befunde.append(f"Job '{job}:' fehlt")

    if "pytest-split" not in text:
        befunde.append("Aufteilung per 'pytest-split' fehlt")
    if "pytest-xdist" not in text:
        befunde.append("Parallelisierung per 'pytest-xdist' fehlt")
    if "inputs.mit-staging-app" not in text:
        befunde.append("Bedingung 'inputs.mit-staging-app' für Job 'staging-probe' fehlt")
    return befunde


def pytest_split_verfuegbar(python: str, wurzel: Path) -> bool:
    """Prüft ohne Import im Leitprozess, ob ``pytest_split`` im Ziel-Interpreter installiert ist."""
    probe = ausfuehren.lauf(
        [python, "-c", "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pytest_split') else 1)"],
        wurzel,
    )
    return probe.code == 0


def _marker_args_einbauen(extra_args: list[str], server_marker: str) -> list[str]:
    """Ergänzt ``-m "not <server_marker>"`` oder kombiniert sauber mit bestehendem ``-m``."""
    ausschluss = f"not {server_marker}"
    ergebnis = list(extra_args)
    for idx, arg in enumerate(ergebnis):
        if arg == "-m" and idx + 1 < len(ergebnis):
            ergebnis[idx + 1] = f"({ergebnis[idx + 1]}) and {ausschluss}"
            return ergebnis
        if arg.startswith("-m") and len(arg) > 2:
            ergebnis[idx] = f"-m({arg[2:]}) and {ausschluss}"
            return ergebnis
    ergebnis.extend(["-m", ausschluss])
    return ergebnis


def stufe2_befehl(
    *,
    python: str,
    wurzel: Path,
    splits: int,
    group: int,
    test_pfade: Sequence[str],
    xdist_worker: str = "auto",
    extra_args: Sequence[str] = (),
    dauer_datei: Path | str | None = None,
    auslassungen: Sequence[tuple[str, str]] = (),
    server_ausschliessen: bool = True,
    server_marker: str = SERVER_MARKER,
    junitxml: Path | str | None = None,
    split_modus: SplitModus = "pytest-split",
    ausgewaehlte_ids: Sequence[str] | None = None,
) -> list[str]:
    """Baut den vollständigen pytest-Befehl für Stufe 2 (``pytest-split`` + ``xdist`` + Ausschlüsse)."""
    teile_matrix(splits)
    if isinstance(group, bool) or not isinstance(group, int) or not 1 <= group <= splits:
        raise ValueError(f"group muss zwischen 1 und {splits} liegen (erhalten: {group!r})")

    args_liste = list(extra_args)
    if any(pipeline_vorlage._VERBOTENES_PLUGIN in a.lower() for a in args_liste):
        raise ValueError(f"verbotenes Plugin {pipeline_vorlage._VERBOTENES_PLUGIN!r} abgelehnt")

    if server_ausschliessen and server_marker:
        args_liste = _marker_args_einbauen(args_liste, server_marker)

    befehl = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider"]

    if split_modus == "pytest-split":
        befehl.extend(["--splits", str(splits), "--group", str(group)])
        if dauer_datei:
            dauer_pfad = Path(dauer_datei)
            if not dauer_pfad.is_absolute():
                dauer_pfad = wurzel / dauer_pfad
            if dauer_pfad.is_file():
                befehl.extend(["--durations-path", str(dauer_pfad), "--splitting-algorithm", "least_duration"])

    worker_sauber = xdist_worker.strip()
    if worker_sauber and worker_sauber != "0":
        befehl.extend(["-n", worker_sauber])
        if not any(a == "--dist" or a.startswith("--dist=") for a in args_liste):
            befehl.extend(["--dist", "worksteal"])

    befehl.extend(args_liste)
    if junitxml:
        befehl.append(f"--junitxml={junitxml}")
    for tid, _grund in auslassungen:
        befehl.extend(["--deselect", tid])

    if ausgewaehlte_ids is not None:
        befehl.extend(ausgewaehlte_ids)
    else:
        befehl.extend(test_pfade)
    return befehl


def _sammle_node_ids(
    python: str,
    wurzel: Path,
    test_pfade: Sequence[str],
    *,
    extra_args: Sequence[str],
    auslassungen: Sequence[tuple[str, str]],
    server_ausschliessen: bool,
    server_marker: str,
    extra_env: dict[str, str | None],
) -> tuple[list[str] | None, str]:
    """Sammelt Test-Node-IDs per ``pytest --collect-only -q`` für den eingebauten Split-Rückfall."""
    args_liste = list(extra_args)
    if server_ausschliessen and server_marker:
        args_liste = _marker_args_einbauen(args_liste, server_marker)
    sammel_befehl = [python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args_liste]
    for tid, _grund in auslassungen:
        sammel_befehl.extend(["--deselect", tid])
    sammel_befehl.extend(test_pfade)
    lauf = ausfuehren.lauf(sammel_befehl, wurzel, extra_env=extra_env)
    if lauf.code not in (0, _PYTEST_KEINE_TESTS):
        return None, lauf.ausgabe
    ids = [
        zeile.strip()
        for zeile in lauf.ausgabe.splitlines()
        if "::" in zeile and not zeile.lstrip().startswith(("<", "=", "-", "WARN", "ERROR"))
    ]
    return ids, lauf.ausgabe


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


def stufe2_lauf(
    repo: Path,
    *,
    splits: int = 2,
    group: int = 1,
    test_pfade: Sequence[str] | str = ("tests",),
    xdist_worker: str = "auto",
    pytest_befehl: str = "",
    auslassungen: Path | str | None = None,
    dauer_datei: Path | str | None = ".test_durations",
    server_ausschliessen: bool = True,
    server_marker: str = SERVER_MARKER,
    junitxml: Path | str | None = None,
    summary_pfad: Path | str | None = None,
    split_modus: SplitModus = "auto",
) -> Stufe2Ergebnis:
    """Führt einen verteilten Stufe-2-Teillauf (``group`` von ``splits``) plattformübergreifend aus."""
    wurzel = config.repo_wurzel(repo)
    try:
        teile_matrix(splits)
        if isinstance(group, bool) or not isinstance(group, int) or not 1 <= group <= splits:
            raise ValueError(f"group muss zwischen 1 und {splits} liegen (erhalten: {group!r})")
    except ValueError as fehler:
        return Stufe2Ergebnis(
            ok=False,
            exit_code=leiter.GATE_FEHLER,
            splits=splits if isinstance(splits, int) else 0,
            group=group if isinstance(group, int) else 0,
            split_modus=split_modus,
            xdist_worker=xdist_worker,
            server_ausgeschlossen=server_ausschliessen,
            lauf_dauer_s=0.0,
            ausgelassen=(),
            befehl=(),
            grund=str(fehler),
        )

    if pipeline_vorlage._VERBOTENES_PLUGIN in pytest_befehl.lower():
        return Stufe2Ergebnis(
            ok=False,
            exit_code=leiter.GATE_FEHLER,
            splits=splits,
            group=group,
            split_modus=split_modus,
            xdist_worker=xdist_worker,
            server_ausgeschlossen=server_ausschliessen,
            lauf_dauer_s=0.0,
            ausgelassen=(),
            befehl=(),
            grund=f"verbotenes Plugin {pipeline_vorlage._VERBOTENES_PLUGIN!r} in pytest-befehl abgelehnt",
        )

    try:
        extra_args = pipeline_vorlage._zerlege_pytest_args(pytest_befehl)
    except ValueError as fehler:
        return Stufe2Ergebnis(
            ok=False,
            exit_code=leiter.GATE_FEHLER,
            splits=splits,
            group=group,
            split_modus=split_modus,
            xdist_worker=xdist_worker,
            server_ausgeschlossen=server_ausschliessen,
            lauf_dauer_s=0.0,
            ausgelassen=(),
            befehl=(),
            grund=f"pytest-befehl ungültig: {fehler}",
        )

    ziele = pipeline_vorlage._teile_liste(test_pfade) or ("tests",)
    aus_pfad = Path(auslassungen) if auslassungen else None
    if aus_pfad is not None and not aus_pfad.is_absolute():
        aus_pfad = wurzel / aus_pfad
    alle_aus = tuple(pipeline_vorlage.lese_auslassungen(aus_pfad)) if aus_pfad is not None else ()

    python = adapter_py._python(wurzel)
    extra_env: dict[str, str | None] = {SERVER_ENV: "0"} if server_ausschliessen else {}

    effektiv_modus: SplitModus = split_modus
    if split_modus == "auto":
        effektiv_modus = "pytest-split" if pytest_split_verfuegbar(python, wurzel) else "eingebaut"
    elif split_modus == "pytest-split" and not pytest_split_verfuegbar(python, wurzel):
        ergebnis = Stufe2Ergebnis(
            ok=False,
            exit_code=leiter.GATE_FEHLER,
            splits=splits,
            group=group,
            split_modus=split_modus,
            xdist_worker=xdist_worker,
            server_ausgeschlossen=server_ausschliessen,
            lauf_dauer_s=0.0,
            ausgelassen=alle_aus,
            befehl=(),
            grund="pytest-split nicht installiert (erforderlich bei --split-modus pytest-split)",
        )
        _schreibe_summary(summary_pfad, ergebnis.zusammenfassung())
        return ergebnis

    ausgewaehlte_ids: list[str] | None = None
    if effektiv_modus == "eingebaut":
        gesammelt, sammel_ausgabe = _sammle_node_ids(
            python,
            wurzel,
            ziele,
            extra_args=extra_args,
            auslassungen=alle_aus,
            server_ausschliessen=server_ausschliessen,
            server_marker=server_marker,
            extra_env=extra_env,
        )
        if gesammelt is None:
            ergebnis = Stufe2Ergebnis(
                ok=False,
                exit_code=leiter.GATE_FEHLER,
                splits=splits,
                group=group,
                split_modus=effektiv_modus,
                xdist_worker=xdist_worker,
                server_ausgeschlossen=server_ausschliessen,
                lauf_dauer_s=0.0,
                ausgelassen=alle_aus,
                befehl=(),
                grund="Testsammlung für Stufe-2-Aufteilung gescheitert",
                ausgabe=sammel_ausgabe,
            )
            _schreibe_summary(summary_pfad, ergebnis.zusammenfassung())
            return ergebnis
        ausgewaehlte_ids = [nid for idx, nid in enumerate(gesammelt) if idx % splits == (group - 1)]
        if not ausgewaehlte_ids:
            ergebnis = Stufe2Ergebnis(
                ok=True,
                exit_code=leiter.GRUEN,
                splits=splits,
                group=group,
                split_modus=effektiv_modus,
                xdist_worker=xdist_worker,
                server_ausgeschlossen=server_ausschliessen,
                lauf_dauer_s=0.0,
                ausgelassen=alle_aus,
                befehl=(),
                grund=f"keine Tests in Teil {group}/{splits}",
            )
            _schreibe_summary(summary_pfad, ergebnis.zusammenfassung())
            return ergebnis

    befehl = stufe2_befehl(
        python=python,
        wurzel=wurzel,
        splits=splits,
        group=group,
        test_pfade=ziele,
        xdist_worker=xdist_worker,
        extra_args=extra_args,
        dauer_datei=dauer_datei,
        auslassungen=() if ausgewaehlte_ids is not None else alle_aus,
        server_ausschliessen=server_ausschliessen,
        server_marker=server_marker,
        junitxml=junitxml,
        split_modus=effektiv_modus,
        ausgewaehlte_ids=ausgewaehlte_ids,
    )

    lauf = ausfuehren.lauf(befehl, wurzel, extra_env=extra_env)
    lauf_dauer = round(lauf.dauer_s, 2)
    if lauf.code == 0:
        exit_code = leiter.GRUEN
        grund = f"Teil {group}/{splits} grün"
    elif lauf.code == _PYTEST_KEINE_TESTS:
        exit_code = leiter.GRUEN
        grund = f"keine Tests in Teil {group}/{splits}"
    elif lauf.code == ausfuehren.NICHT_GESTARTET:
        exit_code = leiter.GATE_FEHLER
        grund = "pytest konnte nicht gestartet werden"
    else:
        exit_code = leiter.ROT
        grund = f"pytest Exit {lauf.code}"

    ergebnis = Stufe2Ergebnis(
        ok=exit_code == leiter.GRUEN,
        exit_code=exit_code,
        splits=splits,
        group=group,
        split_modus=effektiv_modus,
        xdist_worker=xdist_worker,
        server_ausgeschlossen=server_ausschliessen,
        lauf_dauer_s=lauf_dauer,
        ausgelassen=alle_aus,
        befehl=tuple(befehl),
        grund=grund,
        ausgabe=lauf.ausgabe,
    )
    _schreibe_summary(summary_pfad, ergebnis.zusammenfassung())
    return ergebnis


def staging_probe_lauf(
    repo: Path,
    *,
    mit_staging_app: bool | None = None,
    befehl: str = "",
    summary_pfad: Path | str | None = None,
) -> StagingProbeErgebnis:
    """Führt die Staging-Probe ausschließlich aus, wenn der Schalter ``mit_staging_app`` aktiv ist.

    Ist ``mit_staging_app`` ``None``, entscheidet allein :func:`to_spawn.config.hat_staging` auf
    Basis der Repo-Konfiguration (``.to-spawn/config.json``).
    """
    wurzel = config.repo_wurzel(repo)
    aktiv = config.hat_staging(config.lade(wurzel)) if mit_staging_app is None else bool(mit_staging_app)

    if not aktiv:
        ergebnis = StagingProbeErgebnis(
            ok=True,
            exit_code=leiter.GRUEN,
            mit_staging_app=False,
            ausgefuehrt=False,
            uebersprungen=True,
            lauf_dauer_s=0.0,
            befehl=(),
            grund="ohne Staging-App — Staging-Probe übersprungen",
        )
        _schreibe_summary(summary_pfad, ergebnis.zusammenfassung())
        return ergebnis

    if not befehl or not befehl.strip():
        ergebnis = StagingProbeErgebnis(
            ok=False,
            exit_code=leiter.GATE_FEHLER,
            mit_staging_app=True,
            ausgefuehrt=False,
            uebersprungen=False,
            lauf_dauer_s=0.0,
            befehl=(),
            grund="mit Staging-App aktiv, aber kein staging-probe-befehl angegeben",
        )
        _schreibe_summary(summary_pfad, ergebnis.zusammenfassung())
        return ergebnis

    try:
        argv = pipeline_vorlage._zerlege_pytest_args(befehl)
    except ValueError as fehler:
        ergebnis = StagingProbeErgebnis(
            ok=False,
            exit_code=leiter.GATE_FEHLER,
            mit_staging_app=True,
            ausgefuehrt=False,
            uebersprungen=False,
            lauf_dauer_s=0.0,
            befehl=(),
            grund=f"staging-probe-befehl ungültig: {fehler}",
        )
        _schreibe_summary(summary_pfad, ergebnis.zusammenfassung())
        return ergebnis

    lauf = ausfuehren.lauf(argv, wurzel)
    lauf_dauer = round(lauf.dauer_s, 2)
    if lauf.code == 0:
        exit_code = leiter.GRUEN
        grund = "Staging-Probe grün"
    elif lauf.code == ausfuehren.NICHT_GESTARTET:
        exit_code = leiter.GATE_FEHLER
        grund = "Staging-Probe-Befehl konnte nicht gestartet werden"
    else:
        exit_code = leiter.ROT
        grund = f"Staging-Probe Exit {lauf.code}"

    ergebnis = StagingProbeErgebnis(
        ok=exit_code == leiter.GRUEN,
        exit_code=exit_code,
        mit_staging_app=True,
        ausgefuehrt=True,
        uebersprungen=False,
        lauf_dauer_s=lauf_dauer,
        befehl=tuple(argv),
        grund=grund,
        ausgabe=lauf.ausgabe,
    )
    _schreibe_summary(summary_pfad, ergebnis.zusammenfassung())
    return ergebnis


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m to_spawn.pruef.pipeline_stufe2",
        description="Pipeline-Vorlage Stufe 2: YAML erzeugen/prüfen, verteilten Lauf und Staging-Probe ausführen.",
    )
    unter = parser.add_subparsers(dest="aktion", required=True)

    pl = unter.add_parser("lauf", help="Verteilten Stufe-2-Teillauf (pytest-split + xdist) ausführen.")
    pl.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner")
    pl.add_argument("--splits", type=int, default=2, help="Gesamtanzahl paralleler Runner-Teile (>= 2)")
    pl.add_argument("--group", type=int, default=1, help="1-basierter Teil-Index dieses Runners")
    pl.add_argument("--xdist-worker", default="auto", help="xdist-Worker je Runner ('auto', Zahl oder '0')")
    pl.add_argument("--test-pfade", default="tests", help="Test-Pfade (leerzeichengetrennt)")
    pl.add_argument("--pytest-befehl", default="", help="Optionale zusätzliche pytest-Argumente")
    pl.add_argument("--auslassungen", default="", help="Pfad zur Auslassungs-Liste")
    pl.add_argument("--dauer-datei", default=".test_durations", help="Pfad zur Laufzeit-Datei für pytest-split")
    pl.add_argument(
        "--split-modus",
        choices=("auto", "pytest-split", "eingebaut"),
        default="auto",
        help="Aufteilungs-Verfahren (Standard: auto)",
    )
    pl.add_argument(
        "--server-behalten",
        action="store_true",
        help="Bau-Server-Tests nicht ausschließen (nur auf dem Bau-Server verwenden)",
    )
    pl.add_argument("--junitxml", default="", help="Pfad für JUnit-XML")
    pl.add_argument("--summary-pfad", default="", help="Pfad für Markdown-Zusammenfassung")
    pl.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON ausgeben")

    ps = unter.add_parser("staging-probe", help="Bedingten Staging-Probe-Schritt ausführen.")
    ps.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner")
    schalter = ps.add_mutually_exclusive_group()
    schalter.add_argument("--mit-staging-app", dest="mit_staging", action="store_true", default=None)
    schalter.add_argument("--ohne-staging-app", dest="mit_staging", action="store_false")
    ps.add_argument("--befehl", dest="probe_befehl", default="", help="Befehl für die Staging-Probe")
    ps.add_argument("--summary-pfad", default="", help="Pfad für Markdown-Zusammenfassung")
    ps.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON ausgeben")

    pm = unter.add_parser("matrix", help="Runner-Matrix [1..N] für GitHub Actions erzeugen (N >= 2).")
    pm.add_argument("--teile", type=int, required=True, help="Anzahl paralleler Runner-Teile (>= 2)")
    pm.add_argument("--github-output", type=Path, default=None, help="Optionaler Pfad zu $GITHUB_OUTPUT")

    pv = unter.add_parser("vorlage", help="Stufe-2-workflow_call-Vorlage oder Eigenanschluss ausgeben/prüfen.")
    pv.add_argument("--eigenanschluss", action="store_true", help="Eigenanschluss-Workflow statt Vorlage ausgeben")
    pv.add_argument("--teile", type=int, default=2, help="Anzahl Runner-Teile für den Eigenanschluss (>= 2)")
    pv.add_argument("--ausgabe", type=Path, default=None, help="Zieldatei zum Schreiben der Vorlage")
    pv.add_argument("--pruefen", type=Path, default=None, help="Bestehende YAML-Vorlage gegen Vertrag prüfen")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI-Einstiegspunkt für lokale Nutzung und GitHub Actions."""
    konsole.utf8_ausgabe()
    args = _parser().parse_args(argv)

    if args.aktion == "matrix":
        try:
            werte = teile_matrix(args.teile)
        except ValueError as fehler:
            sys.stderr.write(f"{fehler}\n")
            return leiter.GATE_FEHLER
        serialisiert = json.dumps(werte)
        if args.github_output is not None:
            args.github_output.parent.mkdir(parents=True, exist_ok=True)
            with args.github_output.open("a", encoding="utf-8") as handle:
                handle.write(f"teile={serialisiert}\n")
        sys.stdout.write(serialisiert + "\n")
        return 0

    if args.aktion == "vorlage":
        if args.pruefen is not None:
            if not args.pruefen.is_file():
                sys.stderr.write(f"Vorlage fehlt: {args.pruefen}\n")
                return leiter.GATE_FEHLER
            befunde = pruefe_stufe2_vorlage_text(args.pruefen.read_text(encoding="utf-8"))
            if befunde:
                for befund in befunde:
                    sys.stderr.write(f"- {befund}\n")
                return leiter.ROT
            sys.stdout.write(f"Vorlage sauber: {args.pruefen}\n")
            return 0
        try:
            text = eigenanschluss_stufe2_yaml(teile=args.teile) if args.eigenanschluss else vorlage_stufe2_yaml()
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

    if args.aktion == "staging-probe":
        erg_s = staging_probe_lauf(
            args.repo,
            mit_staging_app=args.mit_staging,
            befehl=args.probe_befehl,
            summary_pfad=args.summary_pfad or None,
        )
        if args.als_json:
            sys.stdout.write(json.dumps(erg_s.als_dict(), ensure_ascii=False) + "\n")
        else:
            if erg_s.ausgabe:
                sys.stdout.write(erg_s.ausgabe.rstrip() + "\n")
            sys.stdout.write(erg_s.zusammenfassung() + "\n")
        return erg_s.exit_code

    erg2 = stufe2_lauf(
        args.repo,
        splits=args.splits,
        group=args.group,
        test_pfade=args.test_pfade,
        xdist_worker=args.xdist_worker,
        pytest_befehl=args.pytest_befehl,
        auslassungen=args.auslassungen or None,
        dauer_datei=args.dauer_datei or None,
        server_ausschliessen=not args.server_behalten,
        junitxml=args.junitxml or None,
        summary_pfad=args.summary_pfad or None,
        split_modus=args.split_modus,
    )
    if args.als_json:
        sys.stdout.write(json.dumps(erg2.als_dict(), ensure_ascii=False) + "\n")
    else:
        if erg2.ausgabe:
            sys.stdout.write(erg2.ausgabe.rstrip() + "\n")
        sys.stdout.write(erg2.zusammenfassung() + "\n")
    return erg2.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
