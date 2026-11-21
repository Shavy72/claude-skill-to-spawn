"""#122: Kinder einer Git-Bash-Shell sterben mit der Bau-Session (Windows-Job-Objekt).

Weg-Test ohne Attrappen: ``bau_loop.run_ticket`` startet einen echten Session-Ersatz, der
über Git Bash ``sleep 37`` startet. Die Session wird wie vom Stop-Hook-Nachläufer per
``taskkill /T /F`` beendet. Danach darf ``sleep.exe`` nicht mehr leben. Nur ``gh`` (externer
Dienst) ist gestellt. Unter Linux übersprungen: dort beendet ``prozessbaum`` die Gruppe.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest
from git_bash import git_bash

from to_spawn import bau_loop, gh, pc_fenster, prozessbaum, umzug

HILFE = Path(__file__).resolve().parent / "hilfen" / "session_git_bash_kind_122.py"


@pytest.mark.skipif(sys.platform != "win32", reason="Job-Objekt gibt es nur unter Windows")
def test_git_bash_kind_stirbt_mit_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bash = git_bash()
    if bash is None:
        pytest.skip("Git Bash fehlt")
    (tmp_path / ".git").mkdir()
    datei = tmp_path / "pids.txt"
    monkeypatch.setattr(gh, "ticket_offen", lambda *a, **k: None)
    gefunden: dict[str, int] = {}

    def beende_wie_stop_hook() -> None:
        ende = time.monotonic() + 30
        while time.monotonic() < ende and not datei.exists():
            time.sleep(0.1)
        if not datei.exists():
            return
        session, sleep = (int(x) for x in datei.read_text(encoding="utf-8").split())
        gefunden.update(session=session, sleep=sleep)
        pc_fenster.baum_beenden(session)

    helfer = threading.Thread(target=beende_wie_stop_hook, daemon=True)
    helfer.start()
    try:
        bau_loop.run_ticket("122", [sys.executable, str(HILFE), bash, str(datei)], repo=tmp_path, konfig={}, takt=0.2)
        helfer.join(timeout=35)
        assert "sleep" in gefunden, "Session-Ersatz hat sleep nicht gestartet"
        # 15 s statt weniger: parallele Gates (DEPLOY_WORKERS=8) verzögern das Beenden um Sekunden.
        ende = time.monotonic() + 15
        while time.monotonic() < ende and prozessbaum.lebt(gefunden["sleep"]):
            time.sleep(0.1)
        assert not prozessbaum.lebt(gefunden["sleep"]), "sleep.exe aus Git Bash überlebte die Session"
    finally:
        if gefunden.get("sleep") and prozessbaum.lebt(gefunden["sleep"]):
            os.kill(gefunden["sleep"], 9)  # nur der eigene Test-Prozess


@pytest.mark.skipif(sys.platform != "win32", reason="Job-Objekt gibt es nur unter Windows")
def test_echter_startweg_bau_py_beendet_git_bash_kind(tmp_path: Path) -> None:
    """Echter PC-Weg: wt-Tab → ``bau <N>`` → ``skripte/bau.py`` ``starte_session`` →
    ``umzug.starte_mit_umzug_wache``. Session-Ende per ``taskkill /T /F`` wie der Stop-Hook."""
    bash = git_bash()
    if bash is None:
        pytest.skip("Git Bash fehlt")
    datei = tmp_path / "pids.txt"
    gefunden: dict[str, int] = {}

    def beende_wie_stop_hook() -> None:
        ende = time.monotonic() + 30
        while time.monotonic() < ende and not datei.exists():
            time.sleep(0.1)
        if not datei.exists():
            return
        session, sleep = (int(x) for x in datei.read_text(encoding="utf-8").split())
        gefunden.update(session=session, sleep=sleep)
        pc_fenster.baum_beenden(session)

    helfer = threading.Thread(target=beende_wie_stop_hook, daemon=True)
    helfer.start()
    try:
        umzug.starte_mit_umzug_wache([sys.executable, str(HILFE), bash, str(datei)], tmp_path / "umzug.json", takt=0.2)
        helfer.join(timeout=35)
        assert "sleep" in gefunden, "Session-Ersatz hat sleep nicht gestartet"
        # 15 s: parallele Gates verzögern das Beenden (siehe oben).
        ende = time.monotonic() + 15
        while time.monotonic() < ende and prozessbaum.lebt(gefunden["sleep"]):
            time.sleep(0.1)
        assert not prozessbaum.lebt(gefunden["sleep"]), "sleep.exe aus Git Bash überlebte die Session"
    finally:
        if gefunden.get("sleep") and prozessbaum.lebt(gefunden["sleep"]):
            os.kill(gefunden["sleep"], 9)  # nur der eigene Test-Prozess
