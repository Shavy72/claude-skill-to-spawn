"""#494: Ein Prozess, der zwischen ``ps`` und dem Ordner-Blick endet, ist kein laufender Aufseher.

Wackler ``test_tmux_gesetzt_eigene_sitzung_voll`` (Exit 0 statt 5): ``spawn_srv.sh`` fragt bei
vollem Rechner ``sessions_stand.py``, ob ein Aufseher ``wache.py <S>`` lebt. Parallele Nachbar-Tests
starten Aufseher-Attrappen für dieselbe Spec 900 in ihrem eigenen Repo; endet so eine Attrappe
genau zwischen ``ps`` und ``/proc/<pid>/cwd``, war der Ordner „unlesbar“ und
``fremdes_repo`` zählte den toten Prozess als eigenen Aufseher → „wartet auf Platz“, Exit 0,
obwohl niemand nachstartet. Hier wird dieses Fenster gezielt hergestellt: Prozess steht in der
Prozessliste, ist beim Ordner-Blick aber schon beendet und abgeholt.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

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


def _aufseher_zustand(modul: ModuleType, pid: int) -> str:
    """Zustand der Spec 900, wenn ``ps`` den Prozess ``pid`` als ``wache.py 900`` gemeldet hat."""
    eintraege = {"900": modul.Eintrag("900", "spec", "Aufseher Spec #900")}
    prozess = modul.Prozess(pid, 1, "python3", f"{sys.executable} /x/skripte/wache.py 900", None)
    modul.zuordnen(eintraege, [prozess])
    return eintraege["900"].zustand


@pytest.mark.skipif(not Path("/proc/self/cwd").exists(), reason="braucht /proc (Linux)")
def test_beendeter_prozess_ist_kein_aufseher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    modul = _sessions_stand(tmp_path, monkeypatch)
    beendet = subprocess.Popen([sys.executable, "-c", "pass"], cwd=tmp_path)
    beendet.wait()  # beendet und abgeholt: /proc/<pid> ist weg
    assert not Path(f"/proc/{beendet.pid}").exists()
    assert _aufseher_zustand(modul, beendet.pid) == "aus"


@pytest.mark.skipif(not Path("/proc/self/cwd").exists(), reason="braucht /proc (Linux)")
def test_lebender_aufseher_im_repo_zaehlt_weiter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gegenprobe: derselbe Aufbau mit lebendem Prozess im Repo bleibt ein Aufseher."""
    modul = _sessions_stand(tmp_path, monkeypatch)
    lebt = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path)
    try:
        assert _aufseher_zustand(modul, lebt.pid) == "startet"
    finally:
        lebt.kill()
        lebt.wait()
