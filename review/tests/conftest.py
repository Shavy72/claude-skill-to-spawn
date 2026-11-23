"""Fixtures: echte temporäre Git-Repos und eigene Config/Log je Test.

Helfer (``git``, ``lauf``, ``route``) liegen in ``review_hilfen.py`` (#329).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

# Als Paket (``review/tests/__init__.py``) fügt pytest diesen Ordner nicht in
# ``sys.path`` ein; die Tests importieren ``review_hilfen`` aber direkt.
_HIER = str(Path(__file__).resolve().parent)
if _HIER not in sys.path:
    sys.path.insert(0, _HIER)

from review_hilfen import BASIS_CONFIG, git


@pytest.fixture
def umgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], Path]:
    """Setzt REVIEW_CONFIG/REVIEW_LOG; Aufruf mit modus schreibt die Config neu."""
    config = tmp_path / "config.json"
    monkeypatch.setenv("REVIEW_CONFIG", str(config))
    monkeypatch.setenv("REVIEW_LOG", str(tmp_path / "ausloeser.jsonl"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    def setzen(modus: str = "hinweis", profil: str | None = None) -> Path:
        inhalt = {**BASIS_CONFIG, "modus": modus}
        if profil is not None:
            inhalt["profil"] = profil
        config.write_text(json.dumps(inhalt), encoding="utf-8")
        return config

    setzen()
    return setzen


@pytest.fixture
def repo(tmp_path: Path, umgebung: Callable[[str], Path]) -> Path:
    pfad = tmp_path / "repo"
    pfad.mkdir()
    git(pfad, "init", "-q")
    git(pfad, "config", "user.name", "Test")
    git(pfad, "config", "user.email", "test@example.invalid")
    git(pfad, "config", "core.autocrlf", "false")
    (pfad / "README.md").write_text("# Test\n", encoding="utf-8")
    (pfad / "rechnen.py").write_text("def eins():\n    return 1\n", encoding="utf-8")
    git(pfad, "add", "-A")
    git(pfad, "commit", "-q", "-m", "start")
    return pfad
