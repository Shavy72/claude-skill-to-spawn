"""Tests für Merge-Zug-Workflow-Regeln (#927): Sammel-Zweig `zug/*` + Skip für Takt-PRs."""

from __future__ import annotations

import re
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
TESTS_YML = SKILL / ".github" / "workflows" / "tests.yml"
EIGEN_YML = SKILL / ".github" / "workflows" / "pipeline-stufe-0-1-eigen.yml"
ERWARTETE_BEDINGUNG = (
    "${{ github.event_name != 'pull_request' "
    "|| !(startsWith(github.head_ref, 'ticket/') && contains(github.head_ref, '-welle')) }}"
)


def _lade_yaml_optional(pfad: Path) -> dict | None:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return None
    daten = yaml.safe_load(pfad.read_text(encoding="utf-8"))
    assert isinstance(daten, dict), f"Ungültiges YAML: {pfad}"
    return daten


def test_tests_yml_loest_auf_main_und_zug_aus() -> None:
    """`tests.yml` läuft bei Push auf `main` und `zug/*` (Linux + Windows in einem Lauf)."""
    text = TESTS_YML.read_text(encoding="utf-8")
    assert re.search(r"(?m)^  pull_request:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    assert re.search(r'(?m)^    branches:\s*\[main,\s*"zug/\*"\]\s*$', text)
    assert re.search(r"(?m)^permissions:\s*\n  contents: read\s*$", text)
    assert "ubuntu-latest" in text and "windows-latest" in text

    daten = _lade_yaml_optional(TESTS_YML)
    if daten is not None:
        on_block = daten.get("on") or daten.get(True)
        assert isinstance(on_block, dict)
        assert "pull_request" in on_block
        assert "workflow_dispatch" in on_block
        push_zweige = on_block["push"]["branches"]
        assert "main" in push_zweige
        assert "zug/*" in push_zweige
        matrix_os = daten["jobs"]["pytest"]["strategy"]["matrix"]["os"]
        assert "ubuntu-latest" in matrix_os
        assert "windows-latest" in matrix_os
        assert daten["permissions"] == {"contents": "read"}


def test_takt_prs_ueberspringen_einzel_ci_in_beiden_workflows() -> None:
    """Takt-PRs (`ticket/*-welle*`) laufen gebündelt über `zug/*`, nicht als Einzel-PR-Lauf."""
    tests_text = TESTS_YML.read_text(encoding="utf-8")
    eigen_text = EIGEN_YML.read_text(encoding="utf-8")

    assert f"    if: {ERWARTETE_BEDINGUNG}\n" in tests_text
    assert f"    if: {ERWARTETE_BEDINGUNG}\n" in eigen_text
    # `zug/*` läuft bewusst nur einmal in `tests.yml` (nicht doppelt über Stufe 0+1).
    assert re.search(r"(?m)^    branches:\s*\[main\]\s*$", eigen_text)
    assert "zug/*" not in eigen_text

    tests_daten = _lade_yaml_optional(TESTS_YML)
    if tests_daten is not None:
        assert tests_daten["jobs"]["pytest"]["if"] == ERWARTETE_BEDINGUNG
    eigen_daten = _lade_yaml_optional(EIGEN_YML)
    if eigen_daten is not None:
        assert eigen_daten["jobs"]["pipeline-stufe-0-1"]["if"] == ERWARTETE_BEDINGUNG
        on_eigen = eigen_daten.get("on") or eigen_daten.get(True)
        assert on_eigen["push"]["branches"] == ["main"]
