"""prozessbaum: die öffentlichen Türen wählen das Betriebssystem selbst (Skill-Issue #92).

``lebt()``, ``prozesse()`` und ``baum()`` lesen unter Windows die WMI-Prozessliste bzw.
fragen den Kernel, unter Linux ``/proc``. Der Windows-Weg wird hier als reine
Rechenlogik geprüft: ``/proc`` zeigt ins Leere, PowerShell liefert eine Ausgabe im
echten Format von ``ConvertTo-Json -Compress`` (Startzeit = ``ToFileTimeUtc``), der
Kernel-Zugriff ist eine Attrappe von ``kernel32``.
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from to_spawn import prozessbaum

REPO = Path(__file__).resolve().parent.parent

# Echte Form der PowerShell-Ausgabe von ``_PS_PROZESSE``: eine Zeile, Felder p/e/c/t.
# 10 = Terminal, 11/12 = Kinder, 13 = Enkel, 20 = Fremder mit recycelter Eltern-PID 10
# (startete VOR dem heutigen Prozess 10 — gehört nicht zum Baum).
_PS_AUSGABE = (
    '[{"p":0,"e":0,"c":"","t":0},'
    '{"p":10,"e":4,"c":"\\"C:\\\\Program Files\\\\WindowsApps\\\\wt.exe\\"","t":133780000000000000},'
    '{"p":11,"e":10,"c":"pwsh.exe -NoLogo","t":133780000010000000},'
    '{"p":12,"e":10,"c":"claude.exe --remote-control „bau 7“","t":133780000020000000},'
    '{"p":13,"e":12,"c":"node mcp-server.js","t":133780000030000000},'
    '{"p":20,"e":10,"c":"alt.exe","t":133770000000000000}]\r\n'
)


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    """Erzwingt den Windows-Zweig; ``/proc`` fehlt, PowerShell liefert ``_PS_AUSGABE``."""
    aufrufe: list[list[str]] = []

    def run(befehl: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        aufrufe.append(befehl)
        return subprocess.CompletedProcess(befehl, 0, stdout=_PS_AUSGABE, stderr="")

    monkeypatch.setattr(prozessbaum, "_WINDOWS", True)
    monkeypatch.setattr(prozessbaum, "_PROC", tmp_path / "kein-proc")
    monkeypatch.setattr(prozessbaum.subprocess, "run", run)
    # Windows-Konstanten für ``ohne_fenster()``; unter Linux fehlen sie im Modul subprocess.
    monkeypatch.setattr(prozessbaum.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(prozessbaum.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    return aufrufe


def test_prozesse_windows_aus_wmi_liste(windows: list[list[str]]) -> None:
    liste = prozessbaum.prozesse()
    assert liste[12] == (10, "claude.exe --remote-control „bau 7“", 133780000020000000)
    assert liste[10][1] == '"C:\\Program Files\\WindowsApps\\wt.exe"'
    assert set(liste) == {0, 10, 11, 12, 13, 20}
    assert windows and windows[0][0] == "powershell"


def test_baum_windows_ohne_recycelte_fremde(windows: list[list[str]]) -> None:
    assert prozessbaum.baum(10) == [10, 11, 12, 13]
    assert prozessbaum.baum(13) == [13]


def test_lebt_windows_fragt_kernel(monkeypatch: pytest.MonkeyPatch, windows: list[list[str]]) -> None:
    lebende = {12: 259, 30: 0}  # 259 = STILL_ACTIVE, 0 = beendet (Griff noch offen)

    def oeffnen(_recht: int, _erben: bool, pid: int) -> int:
        return pid if pid in lebende else 0

    def exit_code(griff: int, ziel: object) -> int:
        ziel._obj.value = lebende[griff]  # type: ignore[attr-defined]
        return 1

    kernel = types.SimpleNamespace(OpenProcess=oeffnen, GetExitCodeProcess=exit_code, CloseHandle=lambda _g: 1)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel), raising=False)
    assert prozessbaum.lebt(12) is True
    assert prozessbaum.lebt(30) is False
    assert prozessbaum.lebt(99) is False


@pytest.mark.braucht("posix")
def test_prozesse_linux_aus_proc() -> None:
    eigener = prozessbaum.prozesse()[os.getpid()]
    assert eigener[0] == os.getppid()
    assert Path(sys.executable).name in eigener[1]
    assert eigener[2] > 0


def test_kein_zugriff_auf_interna_von_aussen() -> None:
    """Außerhalb von prozessbaum.py nur öffentliche Namen (Tests dürfen Nähte nutzen)."""
    muster = re.compile(r"\bprozessbaum\._\w")
    treffer = [
        f"{datei.relative_to(REPO).as_posix()}:{nr}"
        for datei in REPO.rglob("*.py")
        if "tests" not in datei.relative_to(REPO).parts[:1] and datei.name != "prozessbaum.py"
        for nr, zeile in enumerate(datei.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if muster.search(zeile)
    ]
    assert treffer == []
