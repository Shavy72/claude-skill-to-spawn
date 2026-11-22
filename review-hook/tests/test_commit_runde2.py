# ruff: noqa: F811  (Fixtures aus test_hooks importiert)
"""Runde 2: `git commit -a`/`--all`/`-am` muss Arbeitsbaum-Diff sehen, nicht nur --staged."""

from __future__ import annotations

from pathlib import Path

from test_hooks import Umgebung, py_aendern, repo, u  # noqa: F401 (Fixtures)


def test_commit_a_langflag_hinweis_kontext(u: Umgebung, repo: Path) -> None:
    py_aendern(repo)  # bewusst NICHT gestaged
    aus = u.commit(repo, 'git commit -a -m "code"')
    assert aus is not None
    spezifisch = aus["hookSpecificOutput"]
    assert "Review fällig (commit, Stufe klein)" in spezifisch["additionalContext"]
    assert "permissionDecision" not in spezifisch


def test_commit_a_kombiniertes_kurzflag_hinweis_kontext(u: Umgebung, repo: Path) -> None:
    py_aendern(repo)
    aus = u.commit(repo, 'git commit -am "code"')
    assert aus is not None
    assert "Review fällig (commit, Stufe klein)" in aus["hookSpecificOutput"]["additionalContext"]


def test_commit_all_langform_hinweis_kontext(u: Umgebung, repo: Path) -> None:
    py_aendern(repo)
    aus = u.commit(repo, 'git commit --all -m "code"')
    assert aus is not None
    assert "Review fällig (commit, Stufe klein)" in aus["hookSpecificOutput"]["additionalContext"]


def test_commit_a_scharf_ohne_beleg_deny(u: Umgebung, repo: Path) -> None:
    u.modus("scharf")
    py_aendern(repo)
    spezifisch = u.commit(repo, 'git commit -a -m "code"')["hookSpecificOutput"]
    assert spezifisch["permissionDecision"] == "deny"
    assert "Beleg fehlt" in spezifisch["permissionDecisionReason"]


def test_commit_amend_bleibt_bei_staged_still(u: Umgebung, repo: Path) -> None:
    # Negativfall: --amend darf NIE als -a gewertet werden.
    py_aendern(repo)  # nicht gestaged
    assert u.commit(repo, 'git commit --amend -m "x"') is None


def test_commit_m_mit_a_wert_bleibt_bei_staged_still(u: Umgebung, repo: Path) -> None:
    # Negativfall: `-m "-a"` — das -a steckt im Nachrichtentext, kein Flag.
    py_aendern(repo)  # nicht gestaged
    assert u.commit(repo, 'git commit -m "-a"') is None


def test_commit_a_mit_nachricht_die_minus_a_enthaelt(u: Umgebung, repo: Path) -> None:
    # -a als echtes Flag VOR -m, dessen Wert selbst "-a" enthält.
    py_aendern(repo)
    aus = u.commit(repo, 'git commit -a -m "-a"')
    assert aus is not None
    assert "Review fällig (commit, Stufe klein)" in aus["hookSpecificOutput"]["additionalContext"]
