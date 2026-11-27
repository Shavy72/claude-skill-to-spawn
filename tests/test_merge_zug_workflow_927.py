"""Tests für Merge-Zug-Workflow-Regeln (#927): Sammel-Zweig `zug/*` + Skip für Takt-PRs."""

from __future__ import annotations

from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent
TESTS_YML = SKILL / ".github" / "workflows" / "tests.yml"
EIGEN_YML = SKILL / ".github" / "workflows" / "pipeline-stufe-0-1-eigen.yml"


def _lade_yaml(pfad: Path) -> dict:
    daten = yaml.safe_load(pfad.read_text(encoding="utf-8"))
    assert isinstance(daten, dict), f"Ungültiges YAML: {pfad}"
    return daten


def test_tests_yml_loest_auf_main_und_zug_aus() -> None:
    """`tests.yml` läuft bei Push auf `main` und `zug/*` (Linux + Windows in einem Lauf)."""
    daten = _lade_yaml(TESTS_YML)
    on_block = daten.get("on") or daten.get(True)
    assert isinstance(on_block, dict)
    assert "pull_request" in on_block
    assert "workflow_dispatch" in on_block
    push_zweige = on_block["push"]["branches"]
    assert "main" in push_zweige
    assert "zug/*" in push_zweige

    job = daten["jobs"]["pytest"]
    matrix_os = job["strategy"]["matrix"]["os"]
    assert "ubuntu-latest" in matrix_os
    assert "windows-latest" in matrix_os
    assert daten["permissions"] == {"contents": "read"}


def test_takt_prs_ueberspringen_einzel_ci_in_beiden_workflows() -> None:
    """Takt-PRs (`ticket/*-welle*`) laufen gebündelt über `zug/*`, nicht als Einzel-PR-Lauf."""
    erwartete_bedingung = (
        "${{ github.event_name != 'pull_request' "
        "|| !(startsWith(github.head_ref, 'ticket/') && contains(github.head_ref, '-welle')) }}"
    )
    tests_daten = _lade_yaml(TESTS_YML)
    assert tests_daten["jobs"]["pytest"]["if"] == erwartete_bedingung

    eigen_daten = _lade_yaml(EIGEN_YML)
    assert eigen_daten["jobs"]["pipeline-stufe-0-1"]["if"] == erwartete_bedingung
    on_eigen = eigen_daten.get("on") or eigen_daten.get(True)
    # `zug/*` läuft bewusst nur einmal in `tests.yml` (nicht doppelt über Stufe 0+1).
    assert on_eigen["push"]["branches"] == ["main"]
