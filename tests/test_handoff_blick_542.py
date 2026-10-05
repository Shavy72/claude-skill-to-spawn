"""#542: Handoff-Blick — Erkennung per Autor-Zeit, ``aktuell`` / ``pruefe`` getrennt.

Gleiche Welt wie ``test_eigener_handoff_542.py``: echtes git-Repo (Worktree) und echtes
Bau-Log in ``tmp_path``. Der Rebase läuft als echter ``git rebase`` — nur
``GIT_COMMITTER_DATE`` ist gesetzt, die Autor-Zeit bleibt die des Original-Commits.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem Erst-Test und wird als Parameter genannt.
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from test_eigener_handoff_542 import (  # noqa: F401 — ``welt`` ist ein Fixture
    HANDOFF,
    JETZT,
    MINUTE,
    TICKET,
    _git,
    _handoff,
    welt,
)

from to_spawn import eigener_handoff


def _zeiten(wt: Path) -> tuple[int, int]:
    """(Autor-Zeit, Committer-Zeit) des letzten Commits, der den Handoff berührt."""
    roh = subprocess.run(
        ["git", "-C", str(wt), "log", "-1", "--format=%at %ct", "--", HANDOFF],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    return int(roh[0]), int(roh[1])


def _rebase_auf_neue_basis(wt: Path, committer_zeit: float) -> None:
    """Echter ``git rebase`` des Handoff-Commits auf einen neuen Basis-Commit.

    Nur ``GIT_COMMITTER_DATE`` wird gesetzt — wie bei einem Rebase in der Bau-Session:
    die Committer-Zeit springt auf „jetzt“, die Autor-Zeit bleibt.
    """
    zweig = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _git(wt, "checkout", "-q", "-b", "basis", "HEAD~1")
    (wt / "basis.txt").write_text("neue Basis\n", encoding="utf-8")
    _git(wt, "add", "basis.txt")
    _git(wt, "commit", "-q", "-m", "chore: neue Basis", zeit=committer_zeit)
    _git(wt, "checkout", "-q", zweig)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")
    }
    env["GIT_COMMITTER_DATE"] = f"@{int(committer_zeit)} +0000"
    subprocess.run(
        ["git", "-C", str(wt), "rebase", "-q", "basis"],
        check=True,
        capture_output=True,
        env=env,
    )


def test_rebase_alter_handoff_zaehlt_nicht(welt: dict) -> None:
    """Rot vor Fix: Rebase setzt die Committer-Zeit neu — alter Handoff sah frisch aus."""
    _handoff(welt["wt"], JETZT - 90 * MINUTE)  # vor dem Sitzungsstart (JETZT - 60 min)
    _rebase_auf_neue_basis(welt["wt"], JETZT - 5 * MINUTE)
    autor, committer = _zeiten(welt["wt"])
    assert autor == int(JETZT - 90 * MINUTE)  # Rebase lässt die Autor-Zeit gleich
    assert committer == int(JETZT - 5 * MINUTE)  # … und setzt die Committer-Zeit neu
    assert eigener_handoff.pruefe(welt["haupt"], TICKET, JETZT) is None
    assert eigener_handoff.aktuell(welt["haupt"], TICKET) is None


def test_rebase_frischer_handoff_zaehlt_weiter(welt: dict) -> None:
    """Ein Handoff aus dieser Sitzung bleibt nach dem Rebase gültig (Autor-Zeit zählt)."""
    _handoff(welt["wt"], JETZT - 10 * MINUTE)
    _rebase_auf_neue_basis(welt["wt"], JETZT - 1 * MINUTE)
    treffer = eigener_handoff.pruefe(welt["haupt"], TICKET, JETZT)
    assert treffer == eigener_handoff.Treffer(HANDOFF, float(int(JETZT - 10 * MINUTE)))


def test_aktuell_vor_faelligkeit_pruefe_erst_ab_faellig_ab(welt: dict) -> None:
    """``aktuell`` liefert den Handoff schon vor der Fälligkeit, ``pruefe`` erst ab
    ``faellig_ab`` (= Handoff-Zeit + ``STILL_MIN`` Minuten)."""
    _handoff(welt["wt"], JETZT - 1 * MINUTE)
    treffer = eigener_handoff.aktuell(welt["haupt"], TICKET)
    assert treffer == eigener_handoff.Treffer(HANDOFF, float(int(JETZT - MINUTE)))
    assert treffer.faellig_ab == treffer.commit_zeit + eigener_handoff.STILL_MIN * 60
    assert eigener_handoff.pruefe(welt["haupt"], TICKET, JETZT) is None
    assert eigener_handoff.pruefe(welt["haupt"], TICKET, treffer.faellig_ab - 1) is None
    assert eigener_handoff.pruefe(welt["haupt"], TICKET, treffer.faellig_ab) == treffer


def test_aktuell_ohne_handoff_none(welt: dict) -> None:
    assert eigener_handoff.aktuell(welt["haupt"], TICKET) is None
