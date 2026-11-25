"""#588: ``zuordnen`` liest auch den Arbeitsordner nicht mehr selbst aus ``/proc``.

Nach #564 prüfte ``zuordnen`` das Leben nur noch aus der übergebenen Liste, fragte aber über
``fremdes_repo`` (#212) weiter ``/proc/<pid>/cwd`` je Nummer. Eine erfundene Nummer, die es auf
dem Rechner zufällig als echten Prozess in einem anderen Ordner gibt, fiel dann still raus.
Jetzt liest ``prozesse_linux`` den Ordner und gibt ihn am ``Prozess`` mit.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from to_spawn import prozessbaum

SKRIPTE = Path(__file__).resolve().parents[1] / "skripte"
START = datetime(2026, 9, 17, 9, 13)


@pytest.fixture
def modul(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    spec = importlib.util.spec_from_file_location("sessions_stand_588", SKRIPTE / "sessions_stand.py")
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m  # dataclass-Auswertung braucht das Modul in sys.modules
    try:
        spec.loader.exec_module(m)
    finally:
        sys.modules.pop(spec.name, None)
    return m


def test_echte_fremde_pid_in_uebergebener_liste_aendert_urteil_nicht(modul: ModuleType, tmp_path: Path) -> None:
    """Eigene PID (läuft wirklich, Ordner außerhalb von ``REPO``) + erfundene: beide zählen."""
    eigene = os.getpid()
    assert Path.cwd().resolve() != tmp_path.resolve()  # Testprozess läuft in fremdem Ordner
    erfunden = 2**22 + 2000
    alle = [
        modul.Prozess(eigene, 1, "python3", "python3 scripts/bau.py 301", START),
        modul.Prozess(erfunden, 1, "python3", "python3 scripts/wache.py 302", START),
        modul.Prozess(erfunden + 1, eigene, "claude", "claude --settings /tmp/x-bau/301-1/settings.json", START),
    ]
    eintraege: dict = {}
    modul.zuordnen(eintraege, alle)
    assert eintraege["301"].pid == eigene
    assert eintraege["301"].zustand == "läuft seit 09:13"
    assert eintraege["302"].zustand == "startet"


def test_mitgegebener_ordner_entscheidet_fremdes_repo(modul: ModuleType, tmp_path: Path) -> None:
    """#212 bleibt: ``ordner`` außerhalb von ``REPO`` zählt nicht, innerhalb und unbekannt schon."""
    fremd = tmp_path.parent / "anderes-repo"
    alle = [
        modul.Prozess(2**22 + 1, 1, "python3", "python3 scripts/bau.py 311", START, fremd),
        modul.Prozess(2**22 + 2, 1, "python3", "python3 scripts/bau.py 312", START, tmp_path.resolve() / "unter"),
        modul.Prozess(2**22 + 3, 1, "python3", "python3 scripts/bau.py 313", START, None),
        modul.Prozess(2**22 + 4, 1, "claude", "claude --settings /tmp/x-bau/314-1/settings.json", START, fremd),
    ]
    eintraege: dict = {}
    modul.zuordnen(eintraege, alle)
    assert set(eintraege) == {"312", "313"}


@pytest.mark.skipif(not Path("/proc/self/cwd").exists(), reason="braucht /proc (Linux)")
def test_prozesse_linux_gibt_arbeitsordner_mit(modul: ModuleType, tmp_path: Path) -> None:
    kind = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(tmp_path))
    try:
        treffer = [p for p in modul.prozesse_linux() if p.pid == kind.pid]
        assert len(treffer) == 1
        assert treffer[0].ordner == tmp_path.resolve()
    finally:
        kind.kill()
        kind.wait()


def _wmi_lauf(eintraege: list[dict[str, object]] | dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(stdout=json.dumps(eintraege))


def test_prozesse_lesen_windows_filtert_fremdes_repo_und_beendete(
    modul: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows-Prozessliste setzt ``ordner`` per :func:`prozessbaum.arbeitsordner_in_windows`
    und wirft bereits beendete Prozesse heraus (fremde ``wache.py 900``/``bau.py 901`` stören nicht).
    """
    fremd = tmp_path.parent / "anderes-repo"
    wt = tmp_path.parent / "wt-901"
    wmi = [
        {
            "ProcessId": 5001,
            "ParentProcessId": 1,
            "Name": "python.exe",
            "CommandLine": r"python.exe D:\skill\skripte\wache.py 900",
            "Start": "2026-09-17T09:10:00",
        },
        {
            "ProcessId": 5002,
            "ParentProcessId": 1,
            "Name": "python.exe",
            "CommandLine": r"python.exe D:\skill\skripte\bau.py 901",
            "Start": "2026-09-17T09:11:00",
        },
        {
            "ProcessId": 5003,
            "ParentProcessId": 1,
            "Name": "python.exe",
            "CommandLine": r"python.exe D:\skill\skripte\wache.py 902",
            "Start": "2026-09-17T09:12:00",
        },
        {
            "ProcessId": 5004,
            "ParentProcessId": 1,
            "Name": "python.exe",
            "CommandLine": r"python.exe D:\skill\skripte\bau.py 903",
            "Start": "2026-09-17T09:12:00",
        },
        {
            "ProcessId": 5005,
            "ParentProcessId": 5002,
            "Name": "claude.exe",
            "CommandLine": r"claude.exe --settings C:\tmp\x-bau\901-20260917-091300\settings.json",
            "Start": "2026-09-17T09:13:00",
        },
    ]
    befunde = {
        5001: prozessbaum.ArbeitsordnerBefund(str(fremd), False),
        5002: prozessbaum.ArbeitsordnerBefund(str(tmp_path), True),
        5004: prozessbaum.ArbeitsordnerBefund(None, None, "cwd unlesbar"),
        5005: prozessbaum.ArbeitsordnerBefund(str(wt), False),
    }
    monkeypatch.setattr(modul.sys, "platform", "win32")
    monkeypatch.setattr(modul.subprocess, "run", lambda *a, **k: _wmi_lauf(wmi))
    monkeypatch.setattr(modul.prozessbaum, "arbeitsordner_in_windows", lambda _ordner: befunde)
    monkeypatch.setattr(modul.prozessbaum, "lebt", lambda pid: pid != 5003)

    alle = modul.prozesse_lesen()
    eintraege = {
        "900": modul.Eintrag("900", "spec", "Aufseher Spec #900"),
        "901": modul.Eintrag("901", "ticket", "Ticket 901"),
        "902": modul.Eintrag("902", "spec", "Aufseher Spec #902"),
        "903": modul.Eintrag("903", "ticket", "Ticket 903"),
    }
    modul.zuordnen(eintraege, alle)
    assert eintraege["900"].zustand == "aus"
    assert eintraege["901"].zustand == "läuft seit 09:13"
    assert eintraege["902"].zustand == "aus"
    assert eintraege["903"].zustand == "wartet"


def test_prozesse_lesen_windows_drin_false_mit_unbrauchbarem_text_bleibt_fremd(
    modul: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auch wenn der PEB-Text leer ist oder auf eine umgebogene Junction zeigt: ``drin=False`` ist fremd."""
    wmi = [
        {
            "ProcessId": 6001,
            "ParentProcessId": 1,
            "Name": "python.exe",
            "CommandLine": r"python.exe D:\skill\skripte\wache.py 900",
            "Start": "",
        },
        {
            "ProcessId": 6002,
            "ParentProcessId": 1,
            "Name": "python.exe",
            "CommandLine": r"python.exe D:\skill\skripte\bau.py 901",
            "Start": "",
        },
    ]
    befunde = {
        6001: prozessbaum.ArbeitsordnerBefund(None, False),
        6002: prozessbaum.ArbeitsordnerBefund(str(tmp_path), False),
    }
    monkeypatch.setattr(modul.sys, "platform", "win32")
    monkeypatch.setattr(modul.subprocess, "run", lambda *a, **k: _wmi_lauf(wmi))
    monkeypatch.setattr(modul.prozessbaum, "arbeitsordner_in_windows", lambda _ordner: befunde)
    monkeypatch.setattr(modul.prozessbaum, "lebt", lambda _pid: True)

    alle = modul.prozesse_lesen()
    eintraege = {
        "900": modul.Eintrag("900", "spec", "Aufseher Spec #900"),
        "901": modul.Eintrag("901", "ticket", "Ticket 901"),
    }
    modul.zuordnen(eintraege, alle)
    assert eintraege["900"].zustand == "aus"
    assert eintraege["901"].zustand == "aus"


def test_prozesse_lesen_windows_rueckfall_bei_oserror_und_einzelobjekt(
    modul: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ist der PEB-Ordnerblick nicht möglich (``OSError``), stürzt ``prozesse_lesen`` nicht ab."""
    wmi = {
        "ProcessId": 7001,
        "ParentProcessId": 1,
        "Name": "python.exe",
        "CommandLine": r"python.exe D:\skill\skripte\bau.py 901",
        "Start": "",
    }
    monkeypatch.setattr(modul.sys, "platform", "win32")
    monkeypatch.setattr(modul.subprocess, "run", lambda *a, **k: _wmi_lauf(wmi))

    def _kaputt(_ordner: Path) -> dict[int, prozessbaum.ArbeitsordnerBefund]:
        raise OSError("PEB nicht lesbar")

    monkeypatch.setattr(modul.prozessbaum, "arbeitsordner_in_windows", _kaputt)

    alle = modul.prozesse_lesen()
    eintraege = {"901": modul.Eintrag("901", "ticket", "Ticket 901")}
    modul.zuordnen(eintraege, alle)
    assert eintraege["901"].zustand == "wartet"


@pytest.mark.skipif(sys.platform != "win32", reason="braucht Windows-PEB")
def test_prozesse_lesen_windows_echte_kinder_trennen_repos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Echter Windows-Nachweis: Kind im eigenen Repo zählt, Kind im fremden Repo ist fremd."""
    eigen = tmp_path / "eigen"
    fremd = tmp_path / "fremd"
    eigen.mkdir()
    fremd.mkdir()
    monkeypatch.setenv("TO_SPAWN_REPO", str(eigen))
    spec = importlib.util.spec_from_file_location("sessions_stand_588_win", SKRIPTE / "sessions_stand.py")
    assert spec is not None and spec.loader is not None
    win_modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = win_modul
    try:
        spec.loader.exec_module(win_modul)
    finally:
        sys.modules.pop(spec.name, None)

    eigen_kind = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(eigen))
    fremd_kind = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(fremd))
    try:
        nach_pid = {p.pid: p for p in win_modul.prozesse_lesen()}
        assert eigen_kind.pid in nach_pid and fremd_kind.pid in nach_pid
        assert not win_modul.ordner_fremd(nach_pid[eigen_kind.pid].ordner)
        assert win_modul.ordner_fremd(nach_pid[fremd_kind.pid].ordner)
    finally:
        for kind in (eigen_kind, fremd_kind):
            kind.kill()
            kind.wait(timeout=10)
