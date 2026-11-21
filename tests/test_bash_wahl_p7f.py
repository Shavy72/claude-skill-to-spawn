"""P7f (#68): eine Tür startet die bash — unter Windows Git-Bash, Pfade kommen heil an.

Weg-Tests ohne Attrappe: ``startklar._standard_laeufer`` und ``stand._lauf`` starten die echte
gewählte bash mit geerbtem ``MSYS_NO_PATHCONV=1`` (setzt Claude Code), ``~/…`` muss beim
Windows-Python als vorhandene Datei ankommen. Attrappen nur für die Rechenlogik „bash fehlt“.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import bash_wahl, stand, startklar  # noqa: E402

WINDOWS = sys.platform == "win32"


def _marke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Pfadsperre an, eigenes ``~`` (kein Zufallstreffer unter ``C:\\c\\…``); Python-Prüfbefehl."""
    monkeypatch.setenv("MSYS_NO_PATHCONV", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "marke_p7f").write_text("x", encoding="utf-8")
    pruefung = "import sys, pathlib; sys.exit(0 if pathlib.Path(sys.argv[1]).is_file() else 3)"
    return f"{shlex.quote(Path(sys.executable).as_posix())} -c {shlex.quote(pruefung)} ~/marke_p7f"


def test_gewaehlte_bash_ist_nie_der_wsl_stub() -> None:
    pfad = bash_wahl.bash()
    if WINDOWS:
        assert not bash_wahl.ist_wsl_stub(pfad), pfad
        assert Path(pfad).is_file(), pfad
    else:
        assert pfad == shutil.which("bash")


def test_home_pfad_kommt_trotz_pfadsperre_beim_windows_programm_an(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    befehl = _marke(tmp_path, monkeypatch)
    lauf = startklar._standard_laeufer(befehl, tmp_path, None, 60, {})
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr


def test_stand_bash_befehl_bekommt_heilen_pfad_trotz_pfadsperre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weg über ``stand._lauf`` (Konfig-Befehl ``bash -c …``): auch dort keine Pfadsperre."""
    innen = _marke(tmp_path, monkeypatch)
    assert stand._lauf(f"bash -c {shlex.quote(innen)}", tmp_path, 60) == 0


def test_ohne_bash_klare_meldung_statt_wsl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Beide Systeme: keine bash → ``FileNotFoundError`` mit Klartext, auch über den Probe-Weg."""
    for name in ("CLAUDE_CODE_GIT_BASH_PATH", "GIT_BASH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bash_wahl, "_GIT_BASH_STANDARD", "Z:/fehlt/bash.exe")
    monkeypatch.setattr(bash_wahl.shutil, "which", lambda _name: None)
    erwartet = "Git-Bash nicht gefunden" if WINDOWS else "bash fehlt im PATH"
    with pytest.raises(FileNotFoundError, match=erwartet):
        bash_wahl.bash()
    with pytest.raises(FileNotFoundError, match=erwartet):
        startklar._standard_laeufer("exit 0", tmp_path, None, 10, {})


def test_lauf_nimmt_pfadsperre_nur_unter_windows_heraus(tmp_path: Path) -> None:
    lauf = bash_wahl.lauf(
        ["-c", 'echo "${MSYS_NO_PATHCONV:-leer} $PFAD_X"'],
        env={**os.environ, "MSYS_NO_PATHCONV": "1", "PFAD_X": "a"},
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert lauf.stdout.split() == (["leer", "a"] if WINDOWS else ["1", "a"])


def test_wsl_stub_regel() -> None:
    assert bash_wahl.ist_wsl_stub(r"C:\Windows\System32\bash.exe")
    assert bash_wahl.ist_wsl_stub(r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\bash.exe")
    assert not bash_wahl.ist_wsl_stub(r"C:\Program Files\Git\bin\bash.exe")
    assert not bash_wahl.ist_wsl_stub("/usr/bin/bash")
