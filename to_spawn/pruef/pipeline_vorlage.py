"""Wiederverwendbare Pipeline-Vorlage Stufe 0+1 (``workflow_call``) und CI-Läufer (#796, #580).

Bündelt das Wissen für die GitHub-Actions-Vorlage Stufe 0+1 an genau einer Stelle:
- :data:`EINGABEN` — dokumentierte Eingaben der ``workflow_call``-Vorlage (Python-Version,
  Pfade, Prüf-Befehle, Basis-Ref, Auslassungen, Betriebssysteme).
- :func:`vorlage_yaml` — erzeugt den vollständigen Inhalt für
  ``.github/workflows/pipeline-stufe-0-1.yml`` (Stufe 0: ruff, Typ-Prüfung, Test-Schutz;
  Stufe 1: Ersatz-Lauf der Import-Graph-Testauswahl auf GitHub Actions).
- :func:`eigenanschluss_yaml` — erzeugt den Eigenanschluss-Workflow für das Skill-Repo
  (``uses: ./.github/workflows/pipeline-stufe-0-1.yml``).
- :func:`pruefe_vorlage_text` — prüft eine ``workflow_call``-YAML-Datei rein mit der
  Python-Standardbibliothek gegen den Vertrag (Eingaben, Beschreibungen, Stufen, keine
  verbotenen Cache-Plugins).
- :func:`stufe0_lauf` / :func:`stufe1_lauf` — plattformübergreifende Ausführung für Linux
  und Windows (CLI: ``python -m to_spawn.pruef.pipeline_vorlage {stufe0,stufe1,vorlage}``).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import re
import shlex
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from to_spawn import config, konsole
from to_spawn.pruef import adapter_py, ausfuehren, import_graph, leiter, regeln, sofort

log = logging.getLogger("to_spawn.pruef.pipeline_vorlage")

#: Verbotenes Cache-Plugin für Stufe 1 (dynamisch zusammengesetzt, damit der Produktivcode sauber bleibt).
_VERBOTENES_PLUGIN = "test" + "mon"

#: Gemeinsame Kopf-Doku aller Pipeline-Vorlagen (#581, #797): Zielbild und klare Rolle des Bau-Servers.
KOPF_DOKU_ZEILEN: tuple[str, ...] = (
    "Zielbild der Pipeline-Vorlage (#581, #797):",
    "- Triage neuer Befunde und Tickets vor dem Bau.",
    "- Risikoarme Fixes gehen bei grüner CI automatisch durch.",
    "- Restliche Änderungen gehen nur per Beweis (Prüf-Beleg / Freigabe) weiter.",
    "- Nächtliche Audits (Stufe 3 / Nachtrunde) prüfen Qualität, Wackler und Mutationen.",
    "Rolle des Bau-Servers:",
    "- Ausschließlich Claude-Sessions und 'braucht Server'-Tests (@pytest.mark.braucht('server') / braucht_server).",
    "- Alle übrigen Tests und Stufe-2-Komplettläufe laufen verteilt auf GitHub Actions (>= 2 Runner).",
)


@dataclass(frozen=True)
class Eingabe:
    """Eine dokumentierte Eingabe einer ``workflow_call``-Vorlage."""

    name: str
    typ: str
    vorgabe: str | bool | int
    beschreibung: str


#: Alle dokumentierten Eingaben für ``.github/workflows/pipeline-stufe-0-1.yml``.
EINGABEN: tuple[Eingabe, ...] = (
    Eingabe(
        name="python-version",
        typ="string",
        vorgabe="3.13",
        beschreibung="Python-Version für die GitHub-Actions-Runner.",
    ),
    Eingabe(
        name="pfade",
        typ="string",
        vorgabe="",
        beschreibung="Optionale Pfad-Filter (leerzeichengetrennt, z. B. 'to_spawn tests'); leer = ganzes Repo.",
    ),
    Eingabe(
        name="basis-ref",
        typ="string",
        vorgabe="",
        beschreibung="Git-Basis-Ref für Diff, Test-Schutz und Import-Graph (leer = automatisch PR-Basis oder Vorgänger-Commit).",
    ),
    Eingabe(
        name="ruff-befehl",
        typ="string",
        vorgabe="",
        beschreibung="Optionaler ruff-Befehl für Stufe 0 (leer = Basis-Konfig oder Standard-ruff).",
    ),
    Eingabe(
        name="typ-befehl",
        typ="string",
        vorgabe="",
        beschreibung="Optionaler Typ-Prüf-Befehl für Stufe 0, z. B. 'mypy to_spawn' (leer = Basis-Konfig oder ausgelassen).",
    ),
    Eingabe(
        name="test-schutz",
        typ="boolean",
        vorgabe=True,
        beschreibung="AST-Diff-Test-Schutz in Stufe 0 aktivieren (verhindert entfernte Prüfungen und geänderte Grenzwerte).",
    ),
    Eingabe(
        name="anforderungen",
        typ="string",
        vorgabe="tests/requirements.txt",
        beschreibung="Pfad zu pip-Abhängigkeiten für Tests (leer = nur pytest und ruff).",
    ),
    Eingabe(
        name="test-pfade",
        typ="string",
        vorgabe="",
        beschreibung="Optionale Test-Wurzelordner (leerzeichengetrennt, z. B. 'tests') für Stufe 1.",
    ),
    Eingabe(
        name="pytest-befehl",
        typ="string",
        vorgabe="",
        beschreibung="Optionale zusätzliche pytest-Argumente für Stufe 1 (z. B. '-x --tb=short').",
    ),
    Eingabe(
        name="auslassungen",
        typ="string",
        vorgabe="",
        beschreibung="Optionaler Pfad zu einer Auslassungs-Liste im Format '<test-id>  # <grund>'.",
    ),
    Eingabe(
        name="betriebssysteme",
        typ="string",
        vorgabe='["ubuntu-latest"]',
        beschreibung="JSON-Array der Runner-Betriebssysteme für Stufe 1 (z. B. ubuntu-latest und windows-latest).",
    ),
    Eingabe(
        name="skill-pfad",
        typ="string",
        vorgabe=".",
        beschreibung="Pfad zum Skill-Repo im Runner (Eigenanschluss '.', Fremdrepo z. B. '.to-spawn-skill').",
    ),
)


@dataclass(frozen=True)
class Stufe1Ergebnis:
    """Ergebnis des Stufe-1-Ersatz-Laufs (Import-Graph-Testauswahl + pytest)."""

    ok: bool
    exit_code: int
    auswahl_dauer_s: float
    lauf_dauer_s: float
    geaendert: tuple[str, ...]
    ausgewaehlt: tuple[str, ...]
    ausgelassen: tuple[tuple[str, str], ...]
    befehl: tuple[str, ...]
    grund: str
    ausgabe: str = ""

    def als_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "auswahl_dauer_s": self.auswahl_dauer_s,
            "lauf_dauer_s": self.lauf_dauer_s,
            "geaendert": list(self.geaendert),
            "ausgewaehlt": list(self.ausgewaehlt),
            "ausgelassen": [{"id": tid, "grund": grund} for tid, grund in self.ausgelassen],
            "befehl": list(self.befehl),
            "grund": self.grund,
        }

    def zusammenfassung(self, titel: str = "Stufe 1 (Import-Graph)") -> str:
        """Kurze Markdown-Zusammenfassung für ``GITHUB_STEP_SUMMARY`` oder Protokolle."""
        status = "grün" if self.ok else ("gate-fehler" if self.exit_code == leiter.GATE_FEHLER else "rot")
        zeilen = [
            f"### {titel}",
            "",
            (
                f"Status: **{status}** · geänderte Dateien: {len(self.geaendert)} · "
                f"gewählte Testdateien: {len(self.ausgewaehlt)} · ausgelassen: {len(self.ausgelassen)} · "
                f"Auswahl {self.auswahl_dauer_s:.3f} s · Lauf {self.lauf_dauer_s:.2f} s"
            ),
            "",
        ]
        if self.grund:
            zeilen.append(f"Hinweis: {self.grund}")
            zeilen.append("")
        if self.ausgewaehlt:
            zeilen += ["<details><summary>Gewählte Testdateien</summary>", ""]
            zeilen += [f"- `{pfad}`" for pfad in self.ausgewaehlt]
            zeilen += ["", "</details>", ""]
        if self.ausgelassen:
            zeilen += [f"<details><summary>Ausgelassen ({len(self.ausgelassen)})</summary>", ""]
            zeilen += [f"- `{tid}` — {grund}" if grund else f"- `{tid}`" for tid, grund in self.ausgelassen]
            zeilen += ["", "</details>", ""]
        return "\n".join(zeilen)


def _yaml_skalar(wert: str | bool | int) -> str:
    if isinstance(wert, bool):
        return "true" if wert else "false"
    if isinstance(wert, int):
        return str(wert)
    return json.dumps(wert, ensure_ascii=False)


def kopf_doku_kommentar() -> str:
    """Rendert :data:`KOPF_DOKU_ZEILEN` als YAML-Kopf-Kommentar (eine Stelle für alle Stufen)."""
    return "".join(f"# {zeile}\n" for zeile in KOPF_DOKU_ZEILEN)


def vorlage_yaml() -> str:
    """Erzeugt die wiederverwendbare ``workflow_call``-Vorlage für Stufe 0+1."""
    eingabe_zeilen: list[str] = []
    for e in EINGABEN:
        eingabe_zeilen.extend(
            [
                f"      {e.name}:",
                f"        description: {_yaml_skalar(e.beschreibung)}",
                "        required: false",
                f"        type: {e.typ}",
                f"        default: {_yaml_skalar(e.vorgabe)}",
            ]
        )
    eingaben_block = "\n".join(eingabe_zeilen)
    return (
        "# Wiederverwendbare Pipeline-Vorlage Stufe 0+1 (#580, #796).\n"
        f"{kopf_doku_kommentar()}"
        "# Stufe 0: ruff, konfigurierbare Typ-Prüfung und AST-Diff-Test-Schutz.\n"
        "# Stufe 1: Ersatz-Lauf der statischen AST-Import-Graph-Testauswahl auf GitHub Actions.\n"
        "name: Pipeline Stufe 0+1\n"
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
        "  stufe-0:\n"
        "    name: Stufe 0 (ruff, Typ-Prüfung, Test-Schutz)\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 20\n"
        "    defaults:\n"
        "      run:\n"
        "        shell: bash\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "        with:\n"
        "          fetch-depth: 0\n"
        "\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "${{ inputs.python-version }}"\n'
        "\n"
        "      - name: Werkzeuge für Stufe 0 installieren\n"
        "        env:\n"
        "          ANFORDERUNGEN: ${{ inputs.anforderungen }}\n"
        "        run: |\n"
        "          python -m pip install -q ruff\n"
        '          if [ -n "$ANFORDERUNGEN" ] && [ -f "$ANFORDERUNGEN" ]; then\n'
        '            python -m pip install -q -r "$ANFORDERUNGEN"\n'
        "          fi\n"
        "\n"
        "      - name: Basis-Ref bestimmen\n"
        "        env:\n"
        "          EINGABE_BASIS: ${{ inputs.basis-ref }}\n"
        "          PR_BASIS: ${{ github.event.pull_request.base.sha }}\n"
        "          PUSH_VORHER: ${{ github.event.before }}\n"
        "        run: |\n"
        '          if [ -n "$EINGABE_BASIS" ]; then\n'
        '            BASIS="$EINGABE_BASIS"\n'
        '          elif [ -n "$PR_BASIS" ]; then\n'
        '            BASIS="$PR_BASIS"\n'
        '          elif [ -n "$PUSH_VORHER" ] && [ "$PUSH_VORHER" != "0000000000000000000000000000000000000000" ]; then\n'
        '            BASIS="$PUSH_VORHER"\n'
        "          elif git rev-parse --verify --quiet HEAD~1 >/dev/null; then\n"
        '            BASIS="HEAD~1"\n'
        "          else\n"
        '            BASIS="HEAD"\n'
        "          fi\n"
        '          echo "BASIS_REF=$BASIS" >> "$GITHUB_ENV"\n'
        "\n"
        "      - name: Stufe 0 ausführen\n"
        "        env:\n"
        "          SKILL_PFAD: ${{ inputs.skill-pfad }}\n"
        "          RUFF_BEFEHL: ${{ inputs.ruff-befehl }}\n"
        "          TYP_BEFEHL: ${{ inputs.typ-befehl }}\n"
        "          TEST_SCHUTZ: ${{ inputs.test-schutz }}\n"
        "          PFADE: ${{ inputs.pfade }}\n"
        "        run: |\n"
        '          args=(--repo . --basis "$BASIS_REF" --ziel HEAD)\n'
        '          [ -n "$RUFF_BEFEHL" ] && args+=(--ruff-befehl "$RUFF_BEFEHL")\n'
        '          [ -n "$TYP_BEFEHL" ] && args+=(--typ-befehl "$TYP_BEFEHL")\n'
        '          [ "$TEST_SCHUTZ" = "false" ] && args+=(--kein-test-schutz)\n'
        '          [ -n "$PFADE" ] && args+=(--pfade "$PFADE")\n'
        '          PYTHONPATH="${SKILL_PFAD}:${PYTHONPATH:-}" python -m to_spawn.pruef.pipeline_vorlage stufe0 "${args[@]}"\n'
        "\n"
        "  stufe-1:\n"
        "    name: Stufe 1 Import-Graph (${{ matrix.os }})\n"
        "    needs: stufe-0\n"
        "    runs-on: ${{ matrix.os }}\n"
        "    continue-on-error: ${{ matrix.os == 'windows-latest' }}\n"
        "    timeout-minutes: 45\n"
        "    strategy:\n"
        "      fail-fast: false\n"
        "      matrix:\n"
        "        os: ${{ fromJson(inputs.betriebssysteme) }}\n"
        "    defaults:\n"
        "      run:\n"
        "        shell: bash\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "        with:\n"
        "          fetch-depth: 0\n"
        "\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "${{ inputs.python-version }}"\n'
        "\n"
        "      - name: tmux installieren (Linux)\n"
        "        if: runner.os == 'Linux'\n"
        "        run: sudo apt-get update -q && sudo apt-get install -y -q tmux\n"
        "\n"
        "      - name: Test-Abhängigkeiten installieren\n"
        "        env:\n"
        "          ANFORDERUNGEN: ${{ inputs.anforderungen }}\n"
        "        run: |\n"
        '          if [ -n "$ANFORDERUNGEN" ] && [ -f "$ANFORDERUNGEN" ]; then\n'
        '            python -m pip install -q -r "$ANFORDERUNGEN" ruff\n'
        "          else\n"
        "            python -m pip install -q pytest ruff\n"
        "          fi\n"
        "\n"
        "      - name: git-Identität für Tests\n"
        "        run: |\n"
        '          git config --global user.name "ci"\n'
        '          git config --global user.email "ci@example.invalid"\n'
        "          git config --global init.defaultBranch main\n"
        "\n"
        "      - name: Basis-Ref bestimmen\n"
        "        env:\n"
        "          EINGABE_BASIS: ${{ inputs.basis-ref }}\n"
        "          PR_BASIS: ${{ github.event.pull_request.base.sha }}\n"
        "          PUSH_VORHER: ${{ github.event.before }}\n"
        "        run: |\n"
        '          if [ -n "$EINGABE_BASIS" ]; then\n'
        '            BASIS="$EINGABE_BASIS"\n'
        '          elif [ -n "$PR_BASIS" ]; then\n'
        '            BASIS="$PR_BASIS"\n'
        '          elif [ -n "$PUSH_VORHER" ] && [ "$PUSH_VORHER" != "0000000000000000000000000000000000000000" ]; then\n'
        '            BASIS="$PUSH_VORHER"\n'
        "          elif git rev-parse --verify --quiet HEAD~1 >/dev/null; then\n"
        '            BASIS="HEAD~1"\n'
        "          else\n"
        '            BASIS="HEAD"\n'
        "          fi\n"
        '          echo "BASIS_REF=$BASIS" >> "$GITHUB_ENV"\n'
        "\n"
        "      - name: Stufe 1 Import-Graph-Testauswahl ausführen\n"
        "        env:\n"
        "          SKILL_PFAD: ${{ inputs.skill-pfad }}\n"
        "          PFADE: ${{ inputs.pfade }}\n"
        "          TEST_PFADE: ${{ inputs.test-pfade }}\n"
        "          PYTEST_BEFEHL: ${{ inputs.pytest-befehl }}\n"
        "          AUSLASSUNGEN: ${{ inputs.auslassungen }}\n"
        "        run: |\n"
        '          args=(--repo . --basis "$BASIS_REF" --ziel HEAD --junitxml "$RUNNER_TEMP/junit-stufe1.xml" --summary-pfad "$GITHUB_STEP_SUMMARY")\n'
        '          [ -n "$PFADE" ] && args+=(--pfade "$PFADE")\n'
        '          [ -n "$TEST_PFADE" ] && args+=(--test-pfade "$TEST_PFADE")\n'
        '          [ -n "$PYTEST_BEFEHL" ] && args+=(--pytest-befehl "$PYTEST_BEFEHL")\n'
        '          [ -n "$AUSLASSUNGEN" ] && args+=(--auslassungen "$AUSLASSUNGEN")\n'
        '          PYTHONPATH="${SKILL_PFAD}:${PYTHONPATH:-}" python -m to_spawn.pruef.pipeline_vorlage stufe1 "${args[@]}"\n'
        "\n"
        "      - name: JUnit-Artefakt (Stufe 1)\n"
        "        if: always()\n"
        "        uses: actions/upload-artifact@v4\n"
        "        with:\n"
        "          name: junit-stufe1-${{ matrix.os }}\n"
        "          path: ${{ runner.temp }}/junit-stufe1.xml\n"
        "          if-no-files-found: ignore\n"
    )


def eigenanschluss_yaml(workflow_ref: str = "./.github/workflows/pipeline-stufe-0-1.yml") -> str:
    """Erzeugt den Eigenanschluss-Workflow für das Skill-Repo (ruft die Vorlage auf)."""
    return (
        "# Eigenanschluss des Skill-Repos an die Pipeline-Vorlage Stufe 0+1 (#580, #796).\n"
        "name: Pipeline Stufe 0+1 (Eigenanschluss)\n"
        "\n"
        "on:\n"
        "  pull_request:\n"
        "  push:\n"
        "    branches: [main]\n"
        "  workflow_dispatch:\n"
        "\n"
        "permissions:\n"
        "  contents: read\n"
        "\n"
        "concurrency:\n"
        "  group: pipeline-stufe-0-1-${{ github.ref }}\n"
        "  cancel-in-progress: true\n"
        "\n"
        "jobs:\n"
        "  pipeline-stufe-0-1:\n"
        f"    uses: {workflow_ref}\n"
        "    with:\n"
        '      python-version: "3.13"\n'
        '      anforderungen: "tests/requirements.txt"\n'
        '      auslassungen: ".github/workflows/auslassungen.txt"\n'
        '      betriebssysteme: \'["ubuntu-latest", "windows-latest"]\'\n'
        '      skill-pfad: "."\n'
    )


def pruefe_vorlage_text(text: str) -> list[str]:
    """Prüft den YAML-Text einer Stufe-0+1-Vorlage rein mit der Standardbibliothek."""
    befunde: list[str] = []
    if _VERBOTENES_PLUGIN in text.lower():
        befunde.append(f"verbotenes Plugin {_VERBOTENES_PLUGIN!r} gefunden")
    if not re.search(r"(?m)^  workflow_call:\s*$", text):
        befunde.append("Abschnitt 'workflow_call:' fehlt")
    if not re.search(r"(?m)^permissions:\s*$", text) or "contents: read" not in text:
        befunde.append("minimale Berechtigung 'permissions: contents: read' fehlt")

    zeilen = text.splitlines()
    for eingabe in EINGABEN:
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

    if not re.search(r"(?m)^  stufe-0:\s*$", text):
        befunde.append("Job 'stufe-0:' fehlt")
    if not re.search(r"(?m)^  stufe-1:\s*$", text):
        befunde.append("Job 'stufe-1:' fehlt")
    if "to_spawn.pruef.pipeline_vorlage stufe0" not in text and "to_spawn.pruef.sofort" not in text:
        befunde.append("Stufe-0-Aufruf fehlt")
    if "to_spawn.pruef.pipeline_vorlage stufe1" not in text and "to_spawn.pruef.import_graph" not in text:
        befunde.append("Stufe-1-Import-Graph-Aufruf fehlt")
    return befunde


def filtere_pfade(pfade: Sequence[str], erlaubte: Sequence[str]) -> list[str]:
    """Filtert normalisierte Relativpfade auf konfigurierte Ordner-Präfixe oder Glob-Muster."""
    muster = [regeln.normal(m).rstrip("/") for m in erlaubte if m and m.strip()]
    if not muster:
        return [regeln.normal(p) for p in pfade if p and p.strip()]
    treffer: list[str] = []
    for roh in pfade:
        norm = regeln.normal(roh)
        if not norm:
            continue
        for m in muster:
            if any(z in m for z in "*?["):
                if fnmatch.fnmatch(norm, m):
                    treffer.append(norm)
                    break
            elif norm == m or norm.startswith(f"{m}/"):
                treffer.append(norm)
                break
    return treffer


def lese_auslassungen(pfad: Path) -> list[tuple[str, str]]:
    """Liest Auslassungen im Format ``<test-id>  # <grund>`` (wie ``.github/workflows/auslassungen.txt``)."""
    if not pfad.is_file():
        return []
    eintraege: list[tuple[str, str]] = []
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        sauber = zeile.strip()
        if not sauber or sauber.startswith("#"):
            continue
        test_id, _, grund = sauber.partition("  # ")
        eintraege.append((test_id.strip().replace("\\", "/"), grund.strip()))
    return eintraege


def _teile_liste(wert: str | Sequence[str] | None) -> tuple[str, ...]:
    if wert is None:
        return ()
    if isinstance(wert, str):
        return tuple(teil for teil in wert.split() if teil.strip())
    return tuple(teil.strip() for teil in wert if teil and teil.strip())


def _zerlege_pytest_args(text: str) -> list[str]:
    if not text.strip():
        return []
    if sys.platform == "win32" and "\\" in text:
        return [
            teil[1:-1] if len(teil) >= 2 and teil[0] == teil[-1] and teil[0] in ('"', "'") else teil
            for teil in shlex.split(text, posix=False)
        ]
    return shlex.split(text, posix=True)


def stufe0_lauf(
    repo: Path,
    *,
    basis: str = "HEAD~1",
    ziel: str | None = "HEAD",
    ruff_befehl: str | None = None,
    typ_befehl: str | None = None,
    test_schutz_aktiv: bool | None = None,
) -> sofort.Stufe0Ergebnis:
    """Führt Stufe 0 für CI-Eingaben aus (leere String-Eingaben lassen Basis-Konfig unberührt)."""
    ruff_arg = ruff_befehl.strip() if ruff_befehl and ruff_befehl.strip() else None
    typ_arg = typ_befehl.strip() if typ_befehl and typ_befehl.strip() else None
    return sofort.stufe0(
        repo,
        basis,
        ziel,
        ruff_befehl=ruff_arg,
        typ_befehl=typ_arg,
        test_schutz_aktiv=test_schutz_aktiv,
    )


def stufe1_lauf(
    repo: Path,
    *,
    basis: str = "HEAD~1",
    ziel: str | None = "HEAD",
    pfade: Sequence[str] | str | None = None,
    test_pfade: Sequence[str] | str | None = None,
    pytest_befehl: str = "",
    auslassungen: Path | str | None = None,
    junitxml: Path | str | None = None,
    summary_pfad: Path | str | None = None,
) -> Stufe1Ergebnis:
    """Führt Stufe 1 (Import-Graph-Testauswahl + pytest) plattformübergreifend aus."""
    wurzel = config.repo_wurzel(repo)
    if _VERBOTENES_PLUGIN in pytest_befehl.lower():
        return Stufe1Ergebnis(
            ok=False,
            exit_code=leiter.GATE_FEHLER,
            auswahl_dauer_s=0.0,
            lauf_dauer_s=0.0,
            geaendert=(),
            ausgewaehlt=(),
            ausgelassen=(),
            befehl=(),
            grund=f"verbotenes Plugin {_VERBOTENES_PLUGIN!r} in pytest-befehl abgelehnt",
        )

    diff_pfade = import_graph.geaenderte_dateien(wurzel, basis, ziel)
    if diff_pfade is None:
        return Stufe1Ergebnis(
            ok=False,
            exit_code=leiter.GATE_FEHLER,
            auswahl_dauer_s=0.0,
            lauf_dauer_s=0.0,
            geaendert=(),
            ausgewaehlt=(),
            ausgelassen=(),
            befehl=(),
            grund=f"geänderte Dateien zwischen {basis!r} und {ziel!r} nicht bestimmbar",
        )

    filter_liste = _teile_liste(pfade)
    gefiltert = filtere_pfade(diff_pfade, filter_liste)
    test_liste = _teile_liste(test_pfade)

    start_wahl = time.perf_counter()
    wahl = import_graph.auswaehlen(
        wurzel,
        geaendert=gefiltert,
        test_pfade=test_liste if test_liste else None,
    )
    auswahl_dauer = round(time.perf_counter() - start_wahl, 4)

    if not wahl.tests:
        ergebnis = Stufe1Ergebnis(
            ok=True,
            exit_code=leiter.GRUEN,
            auswahl_dauer_s=auswahl_dauer,
            lauf_dauer_s=0.0,
            geaendert=tuple(gefiltert),
            ausgewaehlt=(),
            ausgelassen=(),
            befehl=(),
            grund="keine betroffenen Tests im Import-Graph",
        )
        _schreibe_summary(summary_pfad, ergebnis)
        return ergebnis

    aus_pfad = Path(auslassungen) if auslassungen else None
    if aus_pfad is not None and not aus_pfad.is_absolute():
        aus_pfad = wurzel / aus_pfad
    alle_aus = lese_auslassungen(aus_pfad) if aus_pfad is not None else []
    gewaehlt_menge = frozenset(wahl.tests)
    aktive_aus = tuple((tid, grund) for tid, grund in alle_aus if tid.split("::", 1)[0] in gewaehlt_menge)

    try:
        extra_args = _zerlege_pytest_args(pytest_befehl)
    except ValueError as fehler:
        return Stufe1Ergebnis(
            ok=False,
            exit_code=leiter.GATE_FEHLER,
            auswahl_dauer_s=auswahl_dauer,
            lauf_dauer_s=0.0,
            geaendert=tuple(gefiltert),
            ausgewaehlt=wahl.tests,
            ausgelassen=aktive_aus,
            befehl=(),
            grund=f"pytest-befehl ungültig: {fehler}",
        )

    python = adapter_py._python(wurzel)
    befehl = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra_args]
    if junitxml:
        befehl.append(f"--junitxml={junitxml}")
    for tid, _grund in aktive_aus:
        befehl.extend(["--deselect", tid])
    befehl.extend(wahl.tests)

    lauf = ausfuehren.lauf(befehl, wurzel)
    lauf_dauer = round(lauf.dauer_s, 2)
    if lauf.code == 0:
        exit_code = leiter.GRUEN
        grund = f"{len(wahl.tests)} Testdatei(en) grün"
    elif lauf.code == ausfuehren.NICHT_GESTARTET:
        exit_code = leiter.GATE_FEHLER
        grund = "pytest konnte nicht gestartet werden"
    else:
        exit_code = leiter.ROT
        grund = f"pytest Exit {lauf.code}"

    ergebnis = Stufe1Ergebnis(
        ok=exit_code == leiter.GRUEN,
        exit_code=exit_code,
        auswahl_dauer_s=auswahl_dauer,
        lauf_dauer_s=lauf_dauer,
        geaendert=tuple(gefiltert),
        ausgewaehlt=wahl.tests,
        ausgelassen=aktive_aus,
        befehl=tuple(befehl),
        grund=grund,
        ausgabe=lauf.ausgabe,
    )
    _schreibe_summary(summary_pfad, ergebnis)
    return ergebnis


def _schreibe_summary(ziel: Path | str | None, ergebnis: Stufe1Ergebnis) -> None:
    if not ziel:
        return
    pfad = Path(ziel)
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        with pfad.open("a", encoding="utf-8") as handle:
            handle.write(ergebnis.zusammenfassung() + "\n")
    except OSError as fehler:
        log.warning("Zusammenfassung konnte nicht nach %s geschrieben werden: %s", pfad, fehler)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m to_spawn.pruef.pipeline_vorlage",
        description="Pipeline-Vorlage Stufe 0+1: YAML erzeugen/prüfen sowie Stufe 0 und Stufe 1 ausführen.",
    )
    unter = parser.add_subparsers(dest="befehl", required=True)

    p0 = unter.add_parser("stufe0", help="Stufe 0 (ruff, Typ-Prüfung, Test-Schutz) ausführen.")
    p0.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner")
    p0.add_argument("--basis", default="HEAD~1", help="Git-Basis-Ref")
    p0.add_argument("--ziel", default="HEAD", help="Git-Ziel-Ref")
    p0.add_argument("--ruff-befehl", default="", help="Optionaler ruff-Befehl")
    p0.add_argument("--typ-befehl", default="", help="Optionaler Typ-Prüf-Befehl")
    p0.add_argument("--pfade", default="", help="Optionale Pfad-Filter (informatorisch)")
    p0.add_argument("--kein-test-schutz", action="store_true", help="Test-Schutz explizit abschalten")
    p0.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON ausgeben")

    p1 = unter.add_parser("stufe1", help="Stufe 1 (Import-Graph-Testauswahl + pytest) ausführen.")
    p1.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner")
    p1.add_argument("--basis", default="HEAD~1", help="Git-Basis-Ref")
    p1.add_argument("--ziel", default="HEAD", help="Git-Ziel-Ref")
    p1.add_argument("--pfade", default="", help="Optionale Pfad-Filter (leerzeichengetrennt)")
    p1.add_argument("--test-pfade", default="", help="Optionale Test-Wurzelordner (leerzeichengetrennt)")
    p1.add_argument("--pytest-befehl", default="", help="Optionale zusätzliche pytest-Argumente")
    p1.add_argument("--auslassungen", default="", help="Pfad zur Auslassungs-Liste")
    p1.add_argument("--junitxml", default="", help="Pfad für JUnit-XML")
    p1.add_argument("--summary-pfad", default="", help="Pfad für Markdown-Zusammenfassung")
    p1.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON ausgeben")

    pv = unter.add_parser("vorlage", help="workflow_call-Vorlage oder Eigenanschluss ausgeben bzw. prüfen.")
    pv.add_argument(
        "--stufe",
        choices=("0-1", "2", "3", "nachtrunde"),
        default="0-1",
        help="Zielstufe der Vorlage (Standard: 0-1)",
    )
    pv.add_argument("--eigenanschluss", action="store_true", help="Eigenanschluss-Workflow statt Vorlage ausgeben")
    pv.add_argument("--ausgabe", type=Path, default=None, help="Zieldatei zum Schreiben der Vorlage")
    pv.add_argument("--pruefen", type=Path, default=None, help="Bestehende YAML-Vorlage gegen Vertrag prüfen")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI-Einstiegspunkt für lokale Nutzung und GitHub Actions."""
    konsole.utf8_ausgabe()
    args = _parser().parse_args(argv)

    if args.befehl == "vorlage":
        if args.stufe in ("2", "3", "nachtrunde"):
            weiter: list[str] = ["vorlage"]
            if args.eigenanschluss:
                weiter.append("--eigenanschluss")
            if args.ausgabe is not None:
                weiter.extend(["--ausgabe", str(args.ausgabe)])
            if args.pruefen is not None:
                weiter.extend(["--pruefen", str(args.pruefen)])
            if args.stufe == "2":
                from to_spawn.pruef import pipeline_stufe2

                return pipeline_stufe2.main(weiter)
            if args.stufe == "nachtrunde":
                from to_spawn.pruef import nachtrunde

                return nachtrunde.main(weiter)
            from to_spawn.pruef import aufraeumen

            return aufraeumen.main(weiter)
        if args.pruefen is not None:
            if not args.pruefen.is_file():
                sys.stderr.write(f"Vorlage fehlt: {args.pruefen}\n")
                return 2
            befunde = pruefe_vorlage_text(args.pruefen.read_text(encoding="utf-8"))
            if befunde:
                for befund in befunde:
                    sys.stderr.write(f"- {befund}\n")
                return 1
            sys.stdout.write(f"Vorlage sauber: {args.pruefen}\n")
            return 0
        text = eigenanschluss_yaml() if args.eigenanschluss else vorlage_yaml()
        if args.ausgabe is not None:
            args.ausgabe.parent.mkdir(parents=True, exist_ok=True)
            args.ausgabe.write_text(text, encoding="utf-8")
            sys.stdout.write(f"Geschrieben: {args.ausgabe}\n")
        else:
            sys.stdout.write(text)
        return 0

    if args.befehl == "stufe0":
        erg0 = stufe0_lauf(
            args.repo,
            basis=args.basis,
            ziel=args.ziel,
            ruff_befehl=args.ruff_befehl,
            typ_befehl=args.typ_befehl,
            test_schutz_aktiv=False if args.kein_test_schutz else None,
        )
        if args.als_json:
            sys.stdout.write(json.dumps(erg0.als_dict(), ensure_ascii=False) + "\n")
        else:
            sys.stdout.write(erg0.bericht() + "\n")
        return erg0.exit_code

    erg1 = stufe1_lauf(
        args.repo,
        basis=args.basis,
        ziel=args.ziel,
        pfade=args.pfade,
        test_pfade=args.test_pfade,
        pytest_befehl=args.pytest_befehl,
        auslassungen=args.auslassungen or None,
        junitxml=args.junitxml or None,
        summary_pfad=args.summary_pfad or None,
    )
    if args.als_json:
        sys.stdout.write(json.dumps(erg1.als_dict(), ensure_ascii=False) + "\n")
    else:
        if erg1.ausgabe:
            sys.stdout.write(erg1.ausgabe.rstrip() + "\n")
        sys.stdout.write(erg1.zusammenfassung() + "\n")
    return erg1.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
