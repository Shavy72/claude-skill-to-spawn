"""P7f (#68): eine Stelle wählt die bash — unter Windows Git-Bash, Pfade kommen heil an.

Weg-Test ohne Attrappe: ``startklar._standard_laeufer`` startet die echte gewählte bash mit
geerbtem ``MSYS_NO_PATHCONV=1`` (setzt Claude Code), ``~/…`` muss beim Windows-Python als
vorhandene Datei ankommen. Attrappen nur für die Rechenlogik „Git-Bash fehlt“.
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

from to_spawn import bash_wahl, startklar  # noqa: E402

WINDOWS = sys.platform == "win32"


def test_gewaehlte_bash_ist_nie_der_wsl_stub() -> None:
    pfad = bash_wahl.bash()
    if WINDOWS:
        assert "system32" not in pfad.lower(), pfad
        assert Path(pfad).is_file(), pfad
    else:
        assert pfad == (shutil.which("bash") or "bash")


def test_home_pfad_kommt_trotz_pfadsperre_beim_windows_programm_an(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MSYS_NO_PATHCONV", "1")
    monkeypatch.setenv("HOME", str(tmp_path))  # eigenes ~: kein Zufallstreffer unter C:\c\…
    (tmp_path / "marke_p7f").write_text("x", encoding="utf-8")
    python = shlex.quote(Path(sys.executable).as_posix())
    befehl = f"{python} -c 'import sys, pathlib; print(pathlib.Path(sys.argv[1]).is_file())' ~/marke_p7f"
    lauf = startklar._standard_laeufer(befehl, tmp_path, None, 60, {})
    assert lauf.returncode == 0, lauf.stderr
    assert lauf.stdout.strip() == "True", lauf.stdout + lauf.stderr


@pytest.mark.skipif(not WINDOWS, reason="Git-Bash-Suche gibt es nur unter Windows")
def test_ohne_git_bash_klare_meldung_statt_wsl(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CLAUDE_CODE_GIT_BASH_PATH", "GIT_BASH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bash_wahl, "_GIT_BASH_STANDARD", "Z:/fehlt/bash.exe")
    monkeypatch.setattr(bash_wahl.shutil, "which", lambda _name: None)
    with pytest.raises(FileNotFoundError, match="Git-Bash nicht gefunden"):
        bash_wahl.bash()
    assert startklar._bash_pfad() is None


def test_umgebung_ohne_pfadsperre_nur_unter_windows() -> None:
    umgebung = bash_wahl.bash_umgebung({"MSYS_NO_PATHCONV": "1", "PFAD_X": "a"})
    assert umgebung["PFAD_X"] == "a"
    assert ("MSYS_NO_PATHCONV" in umgebung) is not WINDOWS
    assert os.environ is not umgebung
