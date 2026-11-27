"""Unit-Tests für Probesitz Punkt 6 bei ausgeschalteter Staffel (#886)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from to_spawn import probesitz


def test_werte_bau_log_punkt_6_uebersprungen_bei_staffel_aktiv_false() -> None:
    konfig = {"staffel": {"aktiv": False, "max_staffeln": 3}}
    zeilen = [
        {"typ": "session_ende", "modell": "claude-sonnet-5", "tokens": {"gesamt": 1200}, "session_id": "s1"},
    ]
    p5, p6 = probesitz.werte_bau_log(zeilen, konfig=konfig)
    assert p5.ok is True
    assert p6.ok is True
    assert "übersprungen" in p6.grund.lower()
    assert "staffel.aktiv" in p6.grund
    assert "Staffel-Hook" not in p6.fehlt_noch


def test_werte_bau_log_punkt_6_uebersprungen_bei_max_staffeln_eins() -> None:
    konfig = {"staffel": {"aktiv": True, "max_staffeln": 1}}
    p5, p6 = probesitz.werte_bau_log([], konfig=konfig)
    assert p5.ok is False
    assert p6.ok is True
    assert "übersprungen" in p6.grund.lower()
    assert "max_staffeln" in p6.grund


def test_wegwerf_lauf_punkt_6_keine_staffel_hook_meldung_wenn_staffel_aus(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    skill = tmp_path / "skill_ohne_hook"
    skill.mkdir()
    konfig: dict[str, Any] = {
        "runner": "claude",
        "staffel": {"aktiv": False, "max_staffeln": 3},
        "worktree_basis": str(tmp_path / "wt"),
    }

    def fake_laeufer(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[0] == "gh" and argv[1:3] == ["issue", "create"]:
            return subprocess.CompletedProcess(argv, 0, "https://github.com/org/repo/issues/42\n", "")
        if argv[0] == "git" and "ls-remote" in argv:
            return subprocess.CompletedProcess(argv, 0, "abc123\trefs/heads/probesitz-42\n", "")
        if argv[0] == "git" and "log" in argv:
            return subprocess.CompletedProcess(argv, 0, "abc123 chore(probesitz): Runde 1 (#42)\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def fake_session_starter(*_args: Any, **_kwargs: Any) -> probesitz.SessionLauf:
        return probesitz.SessionLauf(exit_code=0)

    def fake_worktree(worktree: Path, _repo: Path, _ticket: str) -> None:
        worktree.mkdir(parents=True, exist_ok=True)

    ergebnisse = probesitz.wegwerf_lauf(
        repo,
        konfig,
        laeufer=fake_laeufer,
        which=lambda name: name,
        session_starter=fake_session_starter,
        worktree_anlegen=fake_worktree,
        skill=skill,
    )
    p6 = ergebnisse[6]
    assert p6.ok is True
    assert "übersprungen" in p6.grund.lower()
    assert "Staffel-Hook" not in p6.fehlt_noch
