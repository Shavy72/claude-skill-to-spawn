"""#542: ``ausrollen.py staging --aufraeumen`` baut alle Staging-Instanzen des Repos ab.

Echte Prozesse und echte Ordner (git-Worktrees im tmp-Ordner), keine Attrappen. Nur Instanzen,
deren Prozess nachweislich im ``staging/``-Ordner eines Worktrees dieses Repos läuft, werden beendet.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKRIPT = (
    Path(__file__).resolve().parent.parent
    / "probe"
    / "gemeinsam"
    / "skripte"
    / "ausrollen.py"
)

pytestmark = pytest.mark.skipif(
    not Path("/proc/self/cwd").exists(), reason="braucht /proc"
)


def _git(ort: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=ort,
        check=True,
        capture_output=True,
    )


def _schlaefer(cwd: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=cwd,
        start_new_session=True,
    )


def _lebt(p: subprocess.Popen[bytes]) -> bool:
    return p.poll() is None


def test_staging_aufraeumen_trifft_alle_worktrees_aber_keine_fremden(
    tmp_path: Path,
) -> None:
    haupt = tmp_path / "haupt"
    haupt.mkdir()
    _git(haupt, "init", "-q", "-b", "main")
    _git(haupt, "commit", "-q", "--allow-empty", "-m", "x")
    wt1, wt2, wt3 = (tmp_path / n for n in ("wt-1", "wt-2", "wt-3"))
    for i, wt in enumerate((wt1, wt2, wt3)):
        _git(haupt, "worktree", "add", "-q", "-b", f"b{i}", str(wt))
    fremd = tmp_path / "fremd"
    fremd.mkdir()
    prozesse = []
    try:
        eigener = _schlaefer(
            (wt2 / "staging").mkdir() or wt2 / "staging"
        )  # verwaist, anderer Worktree
        # PID-Datei zeigt auf einen Prozess, der NICHT in einem Worktree-Staging läuft
        fremder = _schlaefer(fremd)
        (wt3 / "staging").mkdir()
        (wt3 / "staging" / "server.pid").write_text(str(fremder.pid), encoding="utf-8")
        (wt2 / "staging" / "server.pid").write_text(str(eigener.pid), encoding="utf-8")
        prozesse = [eigener, fremder]

        spec = importlib.util.spec_from_file_location("ausrollen_t", SKRIPT)
        assert spec and spec.loader
        modul = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modul)
        modul.REPO = wt1
        modul.aufraeumen("staging", 1)

        deadline = time.monotonic() + 5
        while _lebt(eigener) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _lebt(eigener), (
            "Staging-Prozess aus fremdem Worktree des Repos blieb stehen"
        )
        assert not (wt2 / "staging").exists()
        assert _lebt(fremder), "Fremder Prozess darf nie beendet werden"
    finally:
        for p in prozesse:
            p.kill()
            p.wait()
