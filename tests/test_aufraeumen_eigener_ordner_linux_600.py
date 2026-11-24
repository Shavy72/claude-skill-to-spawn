"""#600: Abbau im eigenen Staging-Ordner prüft auch unter Linux, ob die PID aus ``server.pid`` uns gehört.

Echte Prozesse, echte Ordner. Nur selbst gestartete Kurz-Prozesse werden angefasst.
Gegenstück zu ``test_aufraeumen_staging_windows_556.py`` (Windows, Startzeit).
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


def _modul(repo: Path):
    spec = importlib.util.spec_from_file_location("ausrollen_l600", SKRIPT)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    modul.REPO = repo
    modul.WARTE_S = 2.0
    modul._fremde_staging_ordner = lambda: []  # nur der eigene Ordner zählt hier
    return modul


def _schlaefer(cwd: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=cwd, start_new_session=True)


def test_alte_pid_datei_im_eigenen_ordner_fremder_prozess_lebt_ordner_weg(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "staging").mkdir(parents=True)
    fremd = tmp_path / "fremd"
    fremd.mkdir()
    prozess = _schlaefer(fremd)  # PID neu vergeben: Prozess läuft NICHT im Staging-Ordner
    (repo / "staging" / "server.pid").write_text(str(prozess.pid), encoding="utf-8")
    try:
        assert _modul(repo).aufraeumen("staging", 1) is True
        time.sleep(0.3)
        assert prozess.poll() is None, "Fremder Prozess aus alter server.pid wurde beendet"
        assert not (repo / "staging").exists(), "veraltete server.pid: Ordner wird trotzdem abgebaut"
    finally:
        prozess.kill()
        prozess.wait()


def test_eigener_server_im_eigenen_ordner_wird_beendet(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "staging").mkdir(parents=True)
    prozess = _schlaefer(repo / "staging")
    (repo / "staging" / "server.pid").write_text(str(prozess.pid), encoding="utf-8")
    try:
        assert _modul(repo).aufraeumen("staging", 1) is True
        prozess.wait(timeout=5)
        assert not (repo / "staging").exists()
    finally:
        if prozess.poll() is None:
            prozess.kill()
            prozess.wait()
