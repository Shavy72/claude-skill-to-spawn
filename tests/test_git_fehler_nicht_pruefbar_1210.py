"""Tests für #1210 — gescheitertes ``git show`` in ``_test_verluste`` ist „nicht prüfbar“.

Befund (Prüfpanel #1205): Scheiterte ``git show`` (Zeitablauf, kaputter Commit-Ref),
gab ``_test_verluste`` still ``[]`` zurück. ``regel_tests`` wertete das als „kein
Verlust“, ein echter Testverlust fiel weg und das Ticket blieb zu.

Soll: Warnung loggen und den Commit als „nicht prüfbar“ nach oben melden.

Echt laufen: ein temporäres Git-Repo und ``capo.regel_tests`` samt ``git show``.
Der kaputte Commit-Ref ist ein SHA, den es im Repo nicht gibt.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import pytest

from to_spawn import capo

N = 1210
KAPUTT = "0123456789abcdef0123456789abcdef01234567"


def _git(repo: Path, *args: str) -> str:
    umgebung = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    fertig = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        env=umgebung,
        timeout=60,
    )
    return fertig.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    datei = repo / "tests" / "test_a.py"
    datei.parent.mkdir(parents=True)
    datei.write_text("def test_a():\n    assert True\n", encoding="utf-8")
    _git(repo, "add", "tests/test_a.py")
    _git(repo, "commit", "-q", "-m", f"test: a angelegt (#{N})")
    return repo


def test_kaputter_commit_ref_ist_nicht_pruefbar_statt_sauber(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    repo = _repo(tmp_path)
    eigene = [capo.Commit(KAPUTT, f"fix: kaputt (#{N})", 2_000_000_000)]
    with caplog.at_level(logging.WARNING, logger="to_spawn.capo"):
        verstoss = capo.regel_tests(repo, N, eigene)
    assert verstoss is not None, "gescheitertes git show wurde still als sauber gewertet"
    assert verstoss.regel == "test_ersetzt"
    assert "nicht prüfbar" in verstoss.text
    assert KAPUTT[:7] in verstoss.text
    assert any("nicht prüfbar" in r.getMessage() and KAPUTT[:7] in r.getMessage() for r in caplog.records)


def test_lesbarer_commit_ohne_verlust_bleibt_sauber(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    eigene = capo.ticket_commits(capo.alle_commits(repo, "HEAD"), N)
    assert eigene
    assert capo.regel_tests(repo, N, eigene) is None


def test_echter_verlust_und_nicht_pruefbar_werden_beide_gemeldet(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "tests" / "test_a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", f"test: a entfernt (#{N})")
    eigene = capo.ticket_commits(capo.alle_commits(repo, "HEAD"), N)
    eigene.append(capo.Commit(KAPUTT, f"fix: kaputt (#{N})", 1))
    verstoss = capo.regel_tests(repo, N, eigene)
    assert verstoss is not None
    assert "test_a" in verstoss.text
    assert "nicht prüfbar" in verstoss.text
    assert KAPUTT[:7] in verstoss.text
