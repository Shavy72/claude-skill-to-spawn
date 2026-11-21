"""prozessbaum: die öffentlichen Türen wählen das Betriebssystem selbst (Skill-Issue #92).

``lebt()``, ``prozesse()`` und ``baum()`` lesen unter Windows die Kernel-Prozessliste
bzw. fragen den Kernel, unter Linux ``/proc``. Der Windows-Weg wird hier als reine
Rechenlogik geprüft: ``/proc`` zeigt ins Leere, die Kernel-Nähte (Momentaufnahme,
Kommandozeile, ``kernel32``) sind Attrappen. Seit #154 startet keine Abfrage mehr einen
Prozess (vorher PowerShell + WMI); den echten Kernel-Weg prüft ``test_prozessbaum_echt_154``.
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

# PID → (Eltern-PID, Kommandozeile, Startzeit als FILETIME).
# 10 = Terminal, 11/12 = Kinder, 13 = Enkel, 20 = Fremder mit recycelter Eltern-PID 10
# (startete VOR dem heutigen Prozess 10 — gehört nicht zum Baum).
_PROZESSE = {
    0: (0, "", 0),
    10: (4, '"C:\\Program Files\\WindowsApps\\wt.exe"', 133780000000000000),
    11: (10, "pwsh.exe -NoLogo", 133780000010000000),
    12: (10, "claude.exe --remote-control „bau 7“", 133780000020000000),
    13: (12, "node mcp-server.js", 133780000030000000),
    20: (10, "alt.exe", 133770000000000000),
}


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    """Erzwingt den Windows-Zweig; ``/proc`` fehlt, die Kernel-Nähte liefern ``_PROZESSE``.

    Ergebnis: Liste der Prozess-Starts (``subprocess.run``) — muss leer bleiben.
    """
    aufrufe: list[list[str]] = []

    def run(befehl: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        aufrufe.append(befehl)
        return subprocess.CompletedProcess(befehl, 0, stdout="", stderr="")

    def momentaufnahme() -> dict[int, tuple[int, int]]:
        return {pid: (eltern, start) for pid, (eltern, _, start) in _PROZESSE.items()}

    def kommandozeile(pid: int) -> str:
        return _PROZESSE[pid][1] if pid in _PROZESSE else ""

    monkeypatch.setattr(prozessbaum, "_momentaufnahme_windows", momentaufnahme)
    monkeypatch.setattr(prozessbaum, "_kommandozeile_windows", kommandozeile)
    monkeypatch.setattr(prozessbaum, "_WINDOWS", True)
    monkeypatch.setattr(prozessbaum, "_PROC", tmp_path / "kein-proc")
    monkeypatch.setattr(prozessbaum.subprocess, "run", run)
    # Windows-Konstanten für ``ohne_fenster()``; unter Linux fehlen sie im Modul subprocess.
    monkeypatch.setattr(prozessbaum.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(prozessbaum.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    return aufrufe


def test_prozesse_windows_aus_momentaufnahme(windows: list[list[str]]) -> None:
    liste = prozessbaum.prozesse()
    assert liste[12] == (10, "claude.exe --remote-control „bau 7“", 133780000020000000)
    assert liste[10][1] == '"C:\\Program Files\\WindowsApps\\wt.exe"'
    assert set(liste) == {0, 10, 11, 12, 13, 20}
    assert windows == [], "Prozessliste startet keinen Prozess (#154)"


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
