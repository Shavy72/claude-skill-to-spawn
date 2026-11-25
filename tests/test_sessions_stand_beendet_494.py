"""#494: Ein Prozess, der zwischen ``ps`` und dem Ordner-Blick endet, ist kein laufender Aufseher.

Wackler ``test_tmux_gesetzt_eigene_sitzung_voll`` (Exit 0 statt 5): ``spawn_srv.sh`` fragt bei
vollem Rechner ``sessions_stand.py``, ob ein Aufseher ``wache.py <S>`` lebt. Parallele Nachbar-Tests
starten Aufseher-Attrappen für dieselbe Spec 900 in ihrem eigenen Repo; endet so eine Attrappe
genau zwischen ``ps`` und ``/proc/<pid>/cwd``, war der Ordner „unlesbar“ und
``fremdes_repo`` zählte den toten Prozess als eigenen Aufseher → „wartet auf Platz“, Exit 0,
obwohl niemand nachstartet. Hier wird dieses Fenster gezielt hergestellt: Prozess steht in der
Prozessliste, ist beim Ordner-Blick aber schon beendet und abgeholt.

Seit #564 sitzt die Prüfung beim Lesen der echten Liste (``prozesse_linux``), nicht in ``zuordnen``:
die Tests geben ``ps`` deshalb eine Zeile mit der echten Prozess-Nummer vor.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SKRIPTE = Path(__file__).resolve().parents[1] / "skripte"


def _sessions_stand(repo: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Frisches ``sessions_stand`` mit ``REPO`` = ``repo`` (wird beim Import gelesen)."""
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location("sessions_stand_494", SKRIPTE / "sessions_stand.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul  # dataclass-Auswertung braucht das Modul in sys.modules
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(spec.name, None)
    return modul


def _ps_meldet(modul: ModuleType, monkeypatch: pytest.MonkeyPatch, pid: int, name: str, cmd: str) -> list:
    """``prozesse_linux()``, wenn ``ps`` genau den Prozess ``pid`` mit ``name``/``cmd`` gemeldet hat."""
    zeile = f"{pid} 1 Wed Sep 17 09:12:33 2026 {name} {cmd}\n"
    monkeypatch.setattr(modul, "subprocess", SimpleNamespace(run=lambda *a, **k: SimpleNamespace(stdout=zeile)))
    return modul.prozesse_linux()


def _aufseher_zustand(modul: ModuleType, monkeypatch: pytest.MonkeyPatch, pid: int) -> str:
    """Zustand der Spec 900, wenn ``ps`` den Prozess ``pid`` als ``wache.py 900`` gemeldet hat."""
    eintraege = {"900": modul.Eintrag("900", "spec", "Aufseher Spec #900")}
    modul.zuordnen(eintraege, _ps_meldet(modul, monkeypatch, pid, "python3", "python3 /x/skripte/wache.py 900"))
    return eintraege["900"].zustand


@pytest.mark.skipif(not Path("/proc/self/cwd").exists(), reason="braucht /proc (Linux)")
def test_beendeter_prozess_ist_kein_aufseher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    modul = _sessions_stand(tmp_path, monkeypatch)
    beendet = subprocess.Popen([sys.executable, "-c", "pass"], cwd=tmp_path)
    beendet.wait()  # beendet und abgeholt: /proc/<pid> ist weg
    assert not Path(f"/proc/{beendet.pid}").exists()
    assert _aufseher_zustand(modul, monkeypatch, beendet.pid) == "aus"


@pytest.mark.skipif(not Path("/proc/self/cwd").exists(), reason="braucht /proc (Linux)")
def test_lebender_aufseher_im_repo_zaehlt_weiter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gegenprobe: derselbe Aufbau mit lebendem Prozess im Repo bleibt ein Aufseher."""
    modul = _sessions_stand(tmp_path, monkeypatch)
    lebt = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path)
    try:
        assert _aufseher_zustand(modul, monkeypatch, lebt.pid) == "startet"
    finally:
        lebt.kill()
        lebt.wait()


@pytest.mark.skipif(not Path("/proc/self/cwd").exists(), reason="braucht /proc (Linux)")
def test_zombie_prozess_ist_kein_aufseher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Zombie: beendet, aber nicht abgeholt. ``/proc/<pid>`` da, ``cwd`` aber ENOENT."""
    modul = _sessions_stand(tmp_path, monkeypatch)
    zombie = subprocess.Popen([sys.executable, "-c", "pass"], cwd=tmp_path)
    try:
        for _ in range(100):  # nicht wait()/poll(): sonst wird er abgeholt
            if Path(f"/proc/{zombie.pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                break
            time.sleep(0.05)
        assert Path(f"/proc/{zombie.pid}").exists()
        assert _aufseher_zustand(modul, monkeypatch, zombie.pid) == "aus"
    finally:
        zombie.wait()


@pytest.mark.skipif(not Path("/proc/self/cwd").exists(), reason="braucht /proc (Linux)")
def test_beendete_claude_waise_wird_nicht_gemeldet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Waisen-Zweig: beendeter ``claude``-Prozess mit passender Kommandozeile ist keine Waise."""
    modul = _sessions_stand(tmp_path, monkeypatch)
    beendet = subprocess.Popen([sys.executable, "-c", "pass"], cwd=tmp_path)
    beendet.wait()
    cmd = "claude --settings /h/x-bau/777-20260101-000000/settings.json"
    eintraege: dict = {}
    modul.zuordnen(eintraege, _ps_meldet(modul, monkeypatch, beendet.pid, "claude", cmd))
    assert eintraege == {}
