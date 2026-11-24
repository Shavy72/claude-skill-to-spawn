"""#556: Staging-Abbau unter Windows (kein ``/proc``): Identität über die Startzeit des Prozesses.

Echte Prozesse und echte Ordner. Gegenstück zu ``test_aufraeumen_staging_alle_542.py`` (Linux).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKRIPT = Path(__file__).resolve().parent.parent / "probe" / "gemeinsam" / "skripte" / "ausrollen.py"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-Weg ohne /proc")


def _git(ort: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=ort, check=True, capture_output=True
    )


def _modul(repo: Path):
    spec = importlib.util.spec_from_file_location("ausrollen_w556", SKRIPT)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    modul.REPO = repo
    modul.WARTE_S = 5.0
    return modul


def _welt(tmp_path: Path) -> tuple[Path, Path]:
    haupt = tmp_path / "haupt"
    haupt.mkdir()
    _git(haupt, "init", "-q", "-b", "main")
    _git(haupt, "commit", "-q", "--allow-empty", "-m", "x")
    wt1, wt2 = tmp_path / "wt-1", tmp_path / "wt-2"
    for i, wt in enumerate((wt1, wt2)):
        _git(haupt, "worktree", "add", "-q", "-b", f"w{i}", str(wt))
    (wt2 / "staging").mkdir()
    return wt1, wt2


def _schlaefer(cwd: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=cwd,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore[attr-defined]
    )


def _lebt(p: subprocess.Popen[bytes]) -> bool:
    return p.poll() is None


def _beenden(p: subprocess.Popen[bytes]) -> None:
    p.kill()
    p.wait()


def test_server_aus_fremdem_worktree_wird_beendet_und_ordner_abgebaut(tmp_path: Path) -> None:
    wt1, wt2 = _welt(tmp_path)
    prozess = _schlaefer(wt2 / "staging")
    pid_datei = wt2 / "staging" / "server.pid"
    pid_datei.write_text(str(prozess.pid), encoding="utf-8")  # wie ausrollen: direkt nach dem Start
    try:
        assert _modul(wt1).aufraeumen("staging", 1) is True
        assert not _lebt(prozess), "Server aus fremdem Worktree blieb stehen"
        assert not (wt2 / "staging").exists()
    finally:
        _beenden(prozess)


@pytest.mark.parametrize("versatz_s", [-300, 300], ids=["datei_aelter_als_start", "datei_viel_juenger"])
def test_wiederverwendete_pid_prozess_und_ordner_bleiben(tmp_path: Path, versatz_s: int) -> None:
    wt1, wt2 = _welt(tmp_path)
    fremd = tmp_path / "fremd"
    fremd.mkdir()
    prozess = _schlaefer(fremd)
    pid_datei = wt2 / "staging" / "server.pid"
    pid_datei.write_text(str(prozess.pid), encoding="utf-8")
    jetzt = time.time()
    os.utime(pid_datei, (jetzt + versatz_s, jetzt + versatz_s))
    try:
        assert _modul(wt1).aufraeumen("staging", 1) is False
        assert _lebt(prozess), "Fremder Prozess darf nie beendet werden"
        assert (wt2 / "staging").exists()
    finally:
        _beenden(prozess)


def test_tote_pid_ordner_wird_abgebaut(tmp_path: Path) -> None:
    wt1, wt2 = _welt(tmp_path)
    tot = _schlaefer(wt2 / "staging")
    _beenden(tot)
    (wt2 / "staging" / "server.pid").write_text(str(tot.pid), encoding="utf-8")
    assert _modul(wt1).aufraeumen("staging", 1) is True
    assert not (wt2 / "staging").exists()


def test_alte_pid_datei_im_eigenen_ordner_fremder_prozess_lebt_abbau_laeuft(tmp_path: Path) -> None:
    wt1, _ = _welt(tmp_path)
    (wt1 / "staging").mkdir()
    fremd = tmp_path / "fremd"
    fremd.mkdir()
    prozess = _schlaefer(fremd)
    pid_datei = wt1 / "staging" / "server.pid"
    pid_datei.write_text(str(prozess.pid), encoding="utf-8")
    alt = time.time() - 300  # Datei älter als der Start: PID wurde neu vergeben
    os.utime(pid_datei, (alt, alt))
    try:
        assert _modul(wt1).aufraeumen("staging", 1) is True
        assert _lebt(prozess), "Fremder Prozess darf nie beendet werden"
        assert not (wt1 / "staging").exists()
    finally:
        _beenden(prozess)
