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

SKRIPT = Path(__file__).resolve().parent.parent / "probe" / "gemeinsam" / "skripte" / "ausrollen.py"

pytestmark = pytest.mark.skipif(not Path("/proc/self/cwd").exists(), reason="braucht /proc")


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
        eigener = _schlaefer((wt2 / "staging").mkdir() or wt2 / "staging")  # verwaist, anderer Worktree
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
        assert not _lebt(eigener), "Staging-Prozess aus fremdem Worktree des Repos blieb stehen"
        assert not (wt2 / "staging").exists()
        assert _lebt(fremder), "Fremder Prozess darf nie beendet werden"
    finally:
        for p in prozesse:
            p.kill()
            p.wait()


# --- Fix-Runde 2: Symlink, tote PID, PID wiederverwendet, Prozess bleibt trotz Signal ------


def _modul(repo: Path):
    spec = importlib.util.spec_from_file_location("ausrollen_t2", SKRIPT)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    modul.REPO = repo
    modul.WARTE_S = 0.5
    return modul


def _zwei_worktrees(tmp_path: Path) -> tuple[Path, Path]:
    haupt = tmp_path / "haupt"
    haupt.mkdir()
    _git(haupt, "init", "-q", "-b", "main")
    _git(haupt, "commit", "-q", "--allow-empty", "-m", "x")
    wt1, wt2 = tmp_path / "wt-1", tmp_path / "wt-2"
    for i, wt in enumerate((wt1, wt2)):
        _git(haupt, "worktree", "add", "-q", "-b", f"c{i}", str(wt))
    return wt1, wt2


def test_symlink_staging_wird_nicht_angefasst(tmp_path: Path) -> None:
    wt1, wt2 = _zwei_worktrees(tmp_path)
    fremd = tmp_path / "fremd"
    fremd.mkdir()
    (wt2 / "staging").symlink_to(fremd)
    prozess = _schlaefer(fremd)  # cwd passt zum Symlink-Ziel
    (fremd / "server.pid").write_text(str(prozess.pid), encoding="utf-8")
    try:
        _modul(wt1).aufraeumen("staging", 1)
        assert _lebt(prozess), "Prozess hinter Symlink darf nie beendet werden"
        assert (fremd / "server.pid").exists() and (wt2 / "staging").is_symlink()
    finally:
        prozess.kill()
        prozess.wait()


def test_tote_pid_ordner_wird_trotzdem_abgebaut(tmp_path: Path) -> None:
    wt1, wt2 = _zwei_worktrees(tmp_path)
    (wt2 / "staging").mkdir()
    tot = _schlaefer(wt2 / "staging")
    tot.kill()
    tot.wait()
    (wt2 / "staging" / "server.pid").write_text(str(tot.pid), encoding="utf-8")
    assert _modul(wt1).aufraeumen("staging", 1) is True
    assert not (wt2 / "staging").exists()


def test_wiederverwendete_pid_prozess_und_ordner_bleiben(tmp_path: Path) -> None:
    wt1, wt2 = _zwei_worktrees(tmp_path)
    (wt2 / "staging").mkdir()
    fremd = tmp_path / "fremd"
    fremd.mkdir()
    prozess = _schlaefer(fremd)  # andere cwd als der Staging-Ordner
    (wt2 / "staging" / "server.pid").write_text(str(prozess.pid), encoding="utf-8")
    try:
        _modul(wt1).aufraeumen("staging", 1)
        assert _lebt(prozess)
        assert (wt2 / "staging").exists()
    finally:
        prozess.kill()
        prozess.wait()


def test_prozess_bleibt_trotz_signal_ordner_bleibt_und_rueckgabe_false(tmp_path: Path) -> None:
    wt1, wt2 = _zwei_worktrees(tmp_path)
    (wt2 / "staging").mkdir()
    code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    prozess = subprocess.Popen([sys.executable, "-c", code], cwd=wt2 / "staging", start_new_session=True)
    (wt2 / "staging" / "server.pid").write_text(str(prozess.pid), encoding="utf-8")
    time.sleep(0.5)  # Signal-Handler muss gesetzt sein
    try:
        assert _modul(wt1).aufraeumen("staging", 1) is False
        assert (wt2 / "staging").exists()
        assert _lebt(prozess)
    finally:
        prozess.kill()
        prozess.wait()


@pytest.mark.parametrize("pid", ["0", "-1"])
def test_pid_null_oder_negativ_ordner_bleibt_kein_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pid: str
) -> None:
    wt1, wt2 = _zwei_worktrees(tmp_path)
    (wt2 / "staging").mkdir()
    (wt2 / "staging" / "server.pid").write_text(pid, encoding="utf-8")
    modul = _modul(wt1)
    signale: list[tuple[int, int]] = []
    monkeypatch.setattr(modul.os, "kill", lambda p, s: signale.append((p, s)))
    assert modul._beende(wt2 / "staging", 1, True) is False
    assert (wt2 / "staging").exists()
    assert signale == []
