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

from to_spawn import bau_loop, pc_fenster, probesitz, prozessbaum, sitzungsjob, ticket_zustand, umzug, waechter_lauf, waechter_takt

HILFE = Path(__file__).resolve().parent / "hilfen" / "session_git_bash_kind_122.py"


def _warte_tot(pid: int, frist: float = 15.0) -> bool:
    ende = time.monotonic() + frist
    while time.monotonic() < ende and prozessbaum.lebt(pid):
        time.sleep(0.1)
    return not prozessbaum.lebt(pid)


def _beende_sobald_bereit(datei: Path, gefunden: dict[str, int]) -> threading.Thread:
    def laufen() -> None:
        ende = time.monotonic() + 30
        while time.monotonic() < ende and not datei.exists():
            time.sleep(0.1)
        if not datei.exists():
            return
        session, sleep = (int(x) for x in datei.read_text(encoding="utf-8").split())
        gefunden.update(session=session, sleep=sleep)
        pc_fenster.baum_beenden(session)

    faden = threading.Thread(target=laufen, daemon=True)
    faden.start()
    return faden


@pytest.mark.skipif(sys.platform != "win32", reason="Job-Objekt gibt es nur unter Windows")
def test_git_bash_kind_stirbt_mit_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bash = git_bash()
    if bash is None:
        pytest.skip("Git Bash fehlt")
    (tmp_path / ".git").mkdir()
    datei = tmp_path / "pids.txt"
    monkeypatch.setattr(ticket_zustand, "lesen", lambda *a, **k: None)  # #279: Leseweg von bau_loop
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


@pytest.mark.skipif(sys.platform != "win32", reason="Job-Objekt gibt es nur unter Windows")
def test_probesitz_beendet_git_bash_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#122: ``probesitz.session_fahren`` bindet die Session ans Job-Objekt."""
    bash = git_bash()
    if bash is None:
        pytest.skip("Git Bash fehlt")
    monkeypatch.setattr(probesitz, "BEOBACHTUNGS_TAKT_S", 0.2)
    monkeypatch.setattr(probesitz, "BEENDEN_FRIST_S", 1.0)
    datei = tmp_path / "pids.txt"
    gefunden: dict[str, int] = {}
    helfer = _beende_sobald_bereit(datei, gefunden)
    try:
        probesitz.session_fahren(
            [sys.executable, str(HILFE), bash, str(datei)],
            cwd=tmp_path,
            env=dict(os.environ),
            zeitlimit_s=30.0,
            beobachter=lambda _t: None,
            ticket="122",
        )
        helfer.join(timeout=35)
        assert "sleep" in gefunden, "Session-Ersatz hat sleep nicht gestartet"
        assert _warte_tot(gefunden["sleep"]), "sleep.exe aus Git Bash überlebte probesitz.session_fahren"
    finally:
        if gefunden.get("sleep") and prozessbaum.lebt(gefunden["sleep"]):
            os.kill(gefunden["sleep"], 9)


@pytest.mark.skipif(sys.platform != "win32", reason="Job-Objekt gibt es nur unter Windows")
def test_takt_claude_beendet_git_bash_kind(tmp_path: Path) -> None:
    """#122: ``waechter_takt.starte_claude`` bindet die Session ans Job-Objekt."""
    bash = git_bash()
    if bash is None:
        pytest.skip("Git Bash fehlt")
    datei = tmp_path / "pids.txt"
    gefunden: dict[str, int] = {}
    helfer = _beende_sobald_bereit(datei, gefunden)
    beginn = time.monotonic()
    try:
        waechter_takt.starte_claude([sys.executable, str(HILFE), bash, str(datei)], tmp_path, "prompt")
        helfer.join(timeout=35)
        assert "sleep" in gefunden, "Session-Ersatz hat sleep nicht gestartet"
        assert time.monotonic() - beginn < 15, "starte_claude hing an offenem stdout-Pipe des Kinds"
        assert _warte_tot(gefunden["sleep"]), "sleep.exe aus Git Bash überlebte waechter_takt.starte_claude"
    finally:
        if gefunden.get("sleep") and prozessbaum.lebt(gefunden["sleep"]):
            os.kill(gefunden["sleep"], 9)


@pytest.mark.skipif(sys.platform != "win32", reason="Job-Objekt gibt es nur unter Windows")
def test_aufseher_lauf_beendet_git_bash_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#122: ``waechter_lauf.fahre`` bindet die Aufseher-Session ans Job-Objekt."""
    bash = git_bash()
    if bash is None:
        pytest.skip("Git Bash fehlt")
    datei = tmp_path / "pids.txt"
    gefunden: dict[str, int] = {}
    helfer = _beende_sobald_bereit(datei, gefunden)
    monkeypatch.setattr(waechter_lauf, "befehl", lambda *a, **k: [sys.executable, str(HILFE), bash, str(datei)])
    try:
        waechter_lauf.fahre(
            claude=sys.executable,
            spec=122,
            prompt="prompt",
            modell="test",
            ausweich="",
            remote_control=False,
            repo=tmp_path,
            cwd=tmp_path,
            takt=0.2,
        )
        helfer.join(timeout=35)
        assert "sleep" in gefunden, "Session-Ersatz hat sleep nicht gestartet"
        assert _warte_tot(gefunden["sleep"]), "sleep.exe aus Git Bash überlebte waechter_lauf.fahre"
    finally:
        if gefunden.get("sleep") and prozessbaum.lebt(gefunden["sleep"]):
            os.kill(gefunden["sleep"], 9)


@pytest.mark.skipif(sys.platform != "win32", reason="Job-Objekt gibt es nur unter Windows")
def test_leitstand_sitzung_beendet_git_bash_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#122: ``leitstand_fenster.sitzung`` bindet die Leitstand-Session ans Job-Objekt."""
    import importlib.util

    bash = git_bash()
    if bash is None:
        pytest.skip("Git Bash fehlt")
    skill = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("leitstand_fenster_122", skill / "skripte" / "leitstand_fenster.py")
    assert spec and spec.loader
    lf = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = lf
    spec.loader.exec_module(lf)

    repo = tmp_path / "repo"
    repo.mkdir()
    datei = tmp_path / "pids.txt"
    gefunden: dict[str, int] = {}
    helfer = _beende_sobald_bereit(datei, gefunden)
    monkeypatch.setattr(lf.starter, "finde", lambda _name: sys.executable)
    monkeypatch.setattr(lf, "sitzung_befehl", lambda *a, **k: [sys.executable, str(HILFE), bash, str(datei)])
    try:
        lf.sitzung(lf.Ablage(repo=repo, spec="122"))
        helfer.join(timeout=35)
        assert "sleep" in gefunden, "Session-Ersatz hat sleep nicht gestartet"
        assert _warte_tot(gefunden["sleep"]), "sleep.exe aus Git Bash überlebte leitstand_fenster.sitzung"
    finally:
        if gefunden.get("sleep") and prozessbaum.lebt(gefunden["sleep"]):
            os.kill(gefunden["sleep"], 9)


def test_gebunden_ohne_pid_attribut_wirft_nicht(caplog: pytest.LogCaptureFixture) -> None:
    """Fehlt ``prozess.pid``, bricht ``gebunden`` den Block nicht ab (auch nicht beim Loggen)."""
    with caplog.at_level("WARNING", logger=sitzungsjob.log.name):
        with sitzungsjob.gebunden(object()):
            erreicht = True
    assert erreicht
