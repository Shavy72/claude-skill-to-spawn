# ruff: noqa: F811  (Fixtures aus test_hooks importiert)
"""Zusatz-Test Runde 3 (VERTRAG.md §8): Befund mittel — bekannte Datei im Repo-Wurzelordner
ergab Elternordner "." und git status scannte damit rekursiv die ganze Repo-Wurzel, statt
nur die Wurzel-Ebene. Fremde Änderungen in Unterordnern landeten so im Review."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_hooks import Umgebung, repo, u  # noqa: F401, E402

import stop  # noqa: E402


def test_nach_repo_meldet_keine_fremde_datei_aus_unterordner_bei_wurzeldatei(
    u: Umgebung, repo: Path
) -> None:
    (repo / "README.md").write_text("neu\n", encoding="utf-8")
    (repo / "andere").mkdir()
    (repo / "andere" / "x.py").write_text("y = 1\n", encoding="utf-8")

    gruppen = stop.nach_repo([str((repo / "README.md").resolve())], sitzungsstart=0.0)

    behalten = gruppen[repo.resolve()]
    assert "README.md" in behalten
    assert "andere/x.py" not in behalten
