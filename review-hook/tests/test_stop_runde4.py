# ruff: noqa: F811  (Fixtures aus test_hooks importiert)
"""Runde 4 (VERTRAG.md §8): (1) Bau-Sessions committen vor Zugende — stop.py muss dann
gegen die Start-SHA der Sitzung prüfen (`--base`), sonst ist der Diff gegen HEAD leer.
(2) „Mensch nötig“ wird zusätzlich als JSON-Zeile festgehalten, damit es nachts nicht
verloren geht."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_hooks import Umgebung, git, py_aendern, repo, u  # noqa: F401, E402


def test_stop_nach_commit_prueft_gegen_start_sha(u: Umgebung, repo: Path) -> None:
    start = git(repo, "rev-parse", "HEAD").strip()
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    git(repo, "commit", "-q", "-am", "bau")

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"
    assert "Stufe keine" not in aus["reason"]
    assert f"--base {start}" in aus["reason"]
    assert "--files rechnen.py" in aus["reason"]


def test_stop_ohne_commit_bleibt_ohne_base(u: Umgebung, repo: Path) -> None:
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    aus = u.stop(repo)
    assert aus is not None and aus["decision"] == "block"
    assert "--base" not in aus["reason"]


def test_mensch_noetig_schreibt_jsonl_zeile(u: Umgebung, repo: Path) -> None:
    log = u.tmp / "mensch_noetig.jsonl"
    u.env["REVIEW_MENSCH_NOETIG"] = str(log)
    u.env["TO_SPAWN_SPEC"] = "S9"
    u.env.pop("TO_SPAWN_TICKET", None)
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    assert u.stop(repo)["decision"] == "block"
    zweit = u.stop(repo)
    assert zweit is not None and "Mensch nötig" in zweit["systemMessage"]

    zeilen = log.read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 1
    eintrag = json.loads(zeilen[0])
    assert eintrag["session_id"] == "s1"
    assert eintrag["repo"] == repo.resolve().as_posix()
    assert eintrag["fingerprint"]
    assert eintrag["spec"] == "S9" and eintrag["ticket"] is None
    assert "zeit" in eintrag and "fix_runde" in eintrag
