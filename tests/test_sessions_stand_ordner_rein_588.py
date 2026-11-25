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


def test_prozesse_lesen_windows_liest_arbeitsordner_und_filtert_beendete(
    modul: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows: ``prozesse_lesen`` liest den PEB-Arbeitsordner und wirft beendete Prozesse raus.

    Sonst meldet ``sessions_stand.py`` unter Windows ein ``bau.py 901`` aus einem fremden Repo
    (z. B. paralleler Test-Worker) als laufend im eigenen Repo.
    """
    import json
    from types import SimpleNamespace

    eigen = tmp_path / "eigen"
    fremd = tmp_path / "fremd"
    eigen.mkdir()
    fremd.mkdir()
    modul.REPO = eigen

    wmi = [
        {"ProcessId": 401, "ParentProcessId": 1, "Name": "python.exe", "CommandLine": r"python.exe skripte\bau.py 901", "Start": ""},
        {"ProcessId": 402, "ParentProcessId": 1, "Name": "python.exe", "CommandLine": r"python.exe skripte\bau.py 902", "Start": ""},
        {"ProcessId": 403, "ParentProcessId": 1, "Name": "python.exe", "CommandLine": r"python.exe skripte\bau.py 903", "Start": ""},
        {"ProcessId": 404, "ParentProcessId": 1, "Name": "python.exe", "CommandLine": r"python.exe skripte\bau.py 904", "Start": ""},
    ]

    def _peb_cwd(pid: int) -> str | None:
        if pid == 401:
            return str(fremd)
        if pid == 402:
            return None
        if pid == 403:
            return str(eigen)
        raise OSError("PEB unlesbar")

    monkeypatch.setattr(modul.sys, "platform", "win32")
    monkeypatch.setattr(
        modul.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout=json.dumps(wmi)),
    )
    monkeypatch.setattr(modul.prozessbaum, "_arbeitsordner_windows", _peb_cwd)
    monkeypatch.setattr(modul.prozessbaum, "_ist_weg_windows", lambda pid: pid == 402)

    alle = modul.prozesse_lesen()
    nach_pid = {p.pid: p for p in alle}
    assert 402 not in nach_pid
    assert nach_pid[401].ordner == fremd.resolve()
    assert nach_pid[403].ordner == eigen.resolve()
    assert nach_pid[404].ordner is None

    eintraege = {n: modul.Eintrag(n, "ticket", f"Ticket {n}") for n in ("901", "902", "903", "904")}
    modul.zuordnen(eintraege, alle)
    assert eintraege["901"].zustand == "aus"
    assert eintraege["902"].zustand == "aus"
    assert eintraege["903"].zustand == "wartet"
    assert eintraege["904"].zustand == "wartet"


def test_prozesse_lesen_windows_einzelnes_json_objekt(
    modul: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PowerShell ``ConvertTo-Json`` liefert bei einem Treffer ein Objekt statt einer Liste."""
    import json
    from types import SimpleNamespace

    eigen = tmp_path / "eigen"
    eigen.mkdir()
    modul.REPO = eigen
    einzeln = {
        "ProcessId": 501,
        "ParentProcessId": 1,
        "Name": "python.exe",
        "CommandLine": r"python.exe skripte\bau.py 905",
        "Start": "",
    }
    monkeypatch.setattr(modul.sys, "platform", "win32")
    monkeypatch.setattr(
        modul.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout=json.dumps(einzeln)),
    )
    monkeypatch.setattr(modul.prozessbaum, "_arbeitsordner_windows", lambda _pid: str(eigen))
    monkeypatch.setattr(modul.prozessbaum, "_ist_weg_windows", lambda _pid: False)

    alle = modul.prozesse_lesen()
    assert len(alle) == 1
    assert alle[0].pid == 501
    assert alle[0].ordner == eigen.resolve()


@pytest.mark.skipif(sys.platform != "win32", reason="braucht Windows (PEB)")
def test_prozesse_windows_gibt_arbeitsordner_mit(modul: ModuleType, tmp_path: Path) -> None:
    """Echter Kindprozess unter Windows: fremder Arbeitsordner wird über PEB erkannt und ausgeschlossen."""
    eigen = tmp_path / "eigen"
    fremd = tmp_path / "fremd"
    eigen.mkdir()
    fremd.mkdir()
    modul.REPO = eigen
    kind = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", "skripte/bau.py", "901"],
        cwd=str(fremd),
    )
    try:
        alle = modul.prozesse_lesen()
        treffer = [p for p in alle if p.pid == kind.pid]
        assert len(treffer) == 1
        assert treffer[0].ordner == fremd.resolve()
        eintraege = {"901": modul.Eintrag("901", "ticket", "Kern bauen")}
        modul.zuordnen(eintraege, treffer)
        assert eintraege["901"].zustand == "aus"
    finally:
        kind.kill()
        kind.wait()


def test_prozesse_lesen_windows_trennt_fremde_und_beendete_wache(
    modul: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows: fremde oder eben beendete ``wache.py 900`` lässt Spec #900 auf ``aus``.

    Sonst überspringt ``spawn_srv.sh --nur-wache`` das Fenster ``wache 900``, sobald ein
    paralleler Test-Worker eine Aufseher-Session für dieselbe Spec-Nummer laufen hat.
    """
    import json
    from types import SimpleNamespace

    eigen = tmp_path / "eigen"
    fremd = tmp_path / "fremd"
    eigen.mkdir()
    fremd.mkdir()
    modul.REPO = eigen

    wmi = [
        {"ProcessId": 601, "ParentProcessId": 1, "Name": "python.exe", "CommandLine": r"python.exe skripte\wache.py 900", "Start": ""},
        {"ProcessId": 602, "ParentProcessId": 1, "Name": "python.exe", "CommandLine": r"python.exe skripte\wache.py 901", "Start": ""},
        {"ProcessId": 603, "ParentProcessId": 1, "Name": "python.exe", "CommandLine": r"python.exe skripte\wache.py 902", "Start": ""},
    ]

    def _peb_cwd(pid: int) -> str | None:
        if pid == 601:
            return str(fremd)
        if pid == 602:
            return None
        return str(eigen)

    monkeypatch.setattr(modul.sys, "platform", "win32")
    monkeypatch.setattr(
        modul.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout=json.dumps(wmi)),
    )
    monkeypatch.setattr(modul.prozessbaum, "arbeitsordner_windows", _peb_cwd)
    monkeypatch.setattr(modul.prozessbaum, "ist_weg_windows", lambda pid: pid == 602)

    alle = modul.prozesse_lesen()
    eintraege = {n: modul.Eintrag(n, "spec", f"Aufseher Spec #{n}") for n in ("900", "901", "902")}
    modul.zuordnen(eintraege, alle)
    assert eintraege["900"].zustand == "aus"
    assert eintraege["901"].zustand == "aus"
    assert eintraege["902"].zustand == "startet"
