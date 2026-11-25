# ruff: noqa: F811  (Fixtures aus test_hooks importiert)
"""Fix1: `-S`/`-u` mit angehängtem Wert (-S0a1b, -uall) dürfen nicht als -a gelten."""

from __future__ import annotations

from pathlib import Path

from test_hooks import Umgebung, py_aendern, repo, u  # noqa: F401 (Fixtures)


def test_commit_s_angehaengter_wert_bleibt_bei_staged_still(u: Umgebung, repo: Path) -> None:
    # Negativfall: -S0a1b — das "a" steckt im angehängten Signier-Schlüssel, kein Flag.
    py_aendern(repo)  # nicht gestaged
    assert u.commit(repo, "git commit -S0a1b -m x") is None


def test_commit_u_angehaengter_wert_bleibt_bei_staged_still(u: Umgebung, repo: Path) -> None:
    # Negativfall: -uall — das "a" steckt im angehängten Modus, kein Flag.
    py_aendern(repo)  # nicht gestaged
    assert u.commit(repo, "git commit -uall -m x") is None


def test_commit_s_getrennt_mit_a_hinweis_kontext(u: Umgebung, repo: Path) -> None:
    # Positivfall: -S ohne angehängten Wert nimmt NICHT das nächste Token — -a bleibt -a.
    py_aendern(repo)  # nicht gestaged, aber -a sieht Arbeitsbaum-Diff
    aus = u.commit(repo, "git commit -S -a -m x")
    assert aus is not None
    assert "Review fällig (commit, Stufe klein)" in aus["hookSpecificOutput"]["additionalContext"]
