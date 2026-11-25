"""#588: ``zuordnen`` liest auch den Arbeitsordner nicht mehr selbst aus ``/proc``.

Nach #564 prüfte ``zuordnen`` das Leben nur noch aus der übergebenen Liste, fragte aber über
``fremdes_repo`` (#212) weiter ``/proc/<pid>/cwd`` je Nummer. Eine erfundene Nummer, die es auf
dem Rechner zufällig als echten Prozess in einem anderen Ordner gibt, fiel dann still raus.
Jetzt liest ``prozesse_linux`` den Ordner und gibt ihn am ``Prozess`` mit.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest

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


def test_prozesse_lesen_windows_filtert_fremdes_repo_und_beendete(
    modul: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows: ``prozesse_lesen`` liest den Arbeitsordner über den PEB und filtert beendete Prozesse.

    Sonst zählt unter Windows ein paralleler ``bau.py 901`` aus einem anderen Repo (oder einem
    parallelen pytest-Worker) als laufend im eigenen Repo und ``spawn_srv.sh`` überspringt ihn.
    """
    import json
    from types import SimpleNamespace

    fremd = tmp_path.parent / "anderes-repo"
    fremd.mkdir(exist_ok=True)
    rohe = [
        {"ProcessId": 1001, "ParentProcessId": 1, "Name": "python.exe", "CommandLine": "python.exe skripte/bau.py 901", "Start": "2026-11-25T09:00:00+00:00"},
        {"ProcessId": 1002, "ParentProcessId": 1, "Name": "python.exe", "CommandLine": "python.exe skripte/bau.py 902", "Start": "2026-11-25T09:00:00+00:00"},
        {"ProcessId": 1003, "ParentProcessId": 1, "Name": "python.exe", "CommandLine": "python.exe skripte/bau.py 903", "Start": "2026-11-25T09:00:00+00:00"},
        {"ProcessId": 1004, "ParentProcessId": 1, "Name": "python.exe", "CommandLine": "python.exe skripte/bau.py 904", "Start": "2026-11-25T09:00:00+00:00"},
    ]
    cwds: dict[int, str | None] = {1001: str(fremd), 1002: str(tmp_path), 1003: None}
    weg = {1003}

    def fake_cwd(pid: int) -> str | None:
        if pid == 1004:
            raise OSError("unlesbar")
        return cwds.get(pid)

    monkeypatch.setattr(modul.sys, "platform", "win32")
    monkeypatch.setattr(modul.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=json.dumps(rohe)))
    monkeypatch.setattr(modul.prozessbaum, "_arbeitsordner_windows", fake_cwd)
    monkeypatch.setattr(modul.prozessbaum, "_ist_weg_windows", lambda pid: pid in weg)

    alle = modul.prozesse_lesen()
    nach_pid = {p.pid: p for p in alle}
    assert set(nach_pid) == {1001, 1002, 1004}
    assert nach_pid[1001].ordner == fremd.resolve()
    assert nach_pid[1002].ordner == tmp_path.resolve()
    assert nach_pid[1004].ordner is None

    eintraege = {nr: modul.Eintrag(nr, "ticket", f"T{nr}") for nr in ("901", "902", "903", "904")}
    modul.zuordnen(eintraege, alle)
    assert eintraege["901"].zustand == "aus"  # fremdes Repo: zählt nicht
    assert eintraege["902"].zustand == "wartet"  # eigenes Repo: zählt
    assert eintraege["903"].zustand == "aus"  # beendet: herausgefiltert
    assert eintraege["904"].zustand == "wartet"  # lebender Prozess mit unlesbarer cwd: sicherheitshalber gezählt


def test_prozesse_lesen_windows_einzelnes_json_objekt(
    modul: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PowerShell ``ConvertTo-Json`` liefert bei genau einem Objekt ein Dict statt einer Liste."""
    import json
    from types import SimpleNamespace

    einzeln = {"ProcessId": 2001, "ParentProcessId": 1, "Name": "python.exe", "CommandLine": "python.exe skripte/bau.py 901", "Start": ""}
    monkeypatch.setattr(modul.sys, "platform", "win32")
    monkeypatch.setattr(modul.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=json.dumps(einzeln)))
    monkeypatch.setattr(modul.prozessbaum, "_arbeitsordner_windows", lambda pid: str(tmp_path))
    monkeypatch.setattr(modul.prozessbaum, "_ist_weg_windows", lambda pid: False)

    alle = modul.prozesse_lesen()
    assert len(alle) == 1
    assert alle[0].pid == 2001
    assert alle[0].ordner == tmp_path.resolve()


@pytest.mark.skipif(sys.platform != "win32", reason="braucht Windows-PEB")
def test_prozesse_lesen_windows_echter_kindprozess_erkennt_arbeitsordner(
    modul: ModuleType, tmp_path: Path
) -> None:
    """Gegenprobe mit echtem Windows-Unterprozess: fremder Ordner wird erkannt, beendeter fällt raus."""
    fremd = tmp_path.parent / f"{tmp_path.name}-fremd"
    fremd.mkdir()
    kind = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(fremd))
    try:
        assert modul.arbeitsordner(kind.pid) == fremd.resolve()
        assert modul.ordner_fremd(modul.arbeitsordner(kind.pid)) is True
        assert modul.beendet(kind.pid) is False
    finally:
        kind.kill()
        kind.wait()
    assert modul.beendet(kind.pid) is True
