"""#285: ``spawn_local.ps1 -Tickets`` nimmt die Liste aus Git Bash und PowerShell gleich an.

Echter Weg-Test ohne Attrappen: Git Bash (``bash.exe``) startet ``pwsh`` wirklich. Es startet
nie eine Sitzung oder ein Fenster: ``-NurListe`` gibt die erkannten Nummern aus und endet, die
Weigerungs-Tests laufen im Wegwerf-Repo, in dem der Regularien-Prüfer vor jedem Start abbricht.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]
SKRIPT = (WURZEL / "spawn_local.ps1").as_posix()
PWSH = shutil.which("pwsh")


def _git_bash() -> str | None:
    if sys.platform != "win32":
        return None
    for ordner in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        import os

        basis = os.environ.get(ordner)
        if basis and (Path(basis) / "Git" / "bin" / "bash.exe").is_file():
            return str(Path(basis) / "Git" / "bin" / "bash.exe")
    return None


BASH = _git_bash()
braucht_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh nicht vorhanden")
braucht_bash = pytest.mark.skipif(BASH is None, reason="Git Bash (bash.exe von Git for Windows) nur unter Windows")

SCHREIBWEISEN = ["1476,1477", '"1476,1477"', "1476 1477"]


def _lauf(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=120, check=False)


def _erkannt(lauf: subprocess.CompletedProcess[str]) -> list[str]:
    treffer = re.findall(r"^Tickets: (.*)$", lauf.stdout, flags=re.MULTILINE)
    return treffer


@braucht_pwsh
@braucht_bash
@pytest.mark.parametrize("schreibweise", SCHREIBWEISEN)
def test_git_bash_file_weg(tmp_path: Path, schreibweise: str) -> None:
    lauf = _lauf([BASH, "-c", f"pwsh -NoProfile -File {SKRIPT} -Spec 48 -Tickets {schreibweise} -NurListe"], tmp_path)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert _erkannt(lauf) == ["1476,1477"], lauf.stdout + lauf.stderr


@braucht_pwsh
@braucht_bash
@pytest.mark.parametrize("schreibweise", ["1476,1477", "'1476,1477'", "1476 1477"])
def test_git_bash_command_weg(tmp_path: Path, schreibweise: str) -> None:
    befehl = f'pwsh -NoProfile -Command "& \'{SKRIPT}\' -Spec 48 -Tickets {schreibweise} -NurListe"'
    lauf = _lauf([BASH, "-c", befehl], tmp_path)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert _erkannt(lauf) == ["1476,1477"], lauf.stdout + lauf.stderr


@braucht_pwsh
@pytest.mark.parametrize("schreibweise", SCHREIBWEISEN)
def test_powershell_file_weg(tmp_path: Path, schreibweise: str) -> None:
    lauf = _lauf([PWSH, "-NoProfile", "-File", SKRIPT, "-Spec", "48", "-Tickets", *schreibweise.replace('"', "").split(" "), "-NurListe"], tmp_path)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert _erkannt(lauf) == ["1476,1477"], lauf.stdout + lauf.stderr


@braucht_pwsh
@pytest.mark.parametrize("schreibweise", ["1476,1477", "'1476,1477'", "1476 1477"])
def test_powershell_command_weg(tmp_path: Path, schreibweise: str) -> None:
    lauf = _lauf([PWSH, "-NoProfile", "-Command", f"& '{SKRIPT}' -Spec 48 -Tickets {schreibweise} -NurListe"], tmp_path)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert _erkannt(lauf) == ["1476,1477"], lauf.stdout + lauf.stderr


@braucht_pwsh
def test_restliche_argumente_werden_nicht_zum_fenster(tmp_path: Path) -> None:
    # „1476 1477“ per -File: 1477 darf nicht als -Window (Positionsparameter) landen.
    lauf = _lauf([PWSH, "-NoProfile", "-File", SKRIPT, "-Spec", "48", "-Tickets", "1476", "1477", "-NurListe"], tmp_path)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert _erkannt(lauf) == ["1476,1477"]


@braucht_pwsh
def test_keine_zahl_meldet_und_exit_2(tmp_path: Path) -> None:
    lauf = _lauf([PWSH, "-NoProfile", "-File", SKRIPT, "-Spec", "48", "-Tickets", "1476;abc", "-NurListe"], tmp_path)
    assert lauf.returncode == 2
    assert "abc" in lauf.stdout + lauf.stderr


@braucht_pwsh
@pytest.mark.parametrize("trenner", [";", ",", " "])
def test_semikolon_trennt_auch(tmp_path: Path, trenner: str) -> None:
    lauf = _lauf([PWSH, "-NoProfile", "-File", SKRIPT, "-Spec", "48", "-Tickets", f"1476{trenner}1477", "-NurListe"], tmp_path)
    assert _erkannt(lauf) == ["1476,1477"], lauf.stdout + lauf.stderr


@braucht_pwsh
def test_fremde_zahl_nennt_ursache_und_aufruf(tmp_path: Path) -> None:
    pfad = tmp_path / "docs" / "agents" / "manifests"
    pfad.mkdir(parents=True)
    (pfad / "spec-48.json").write_text(
        json.dumps({"spec": 48, "feature": "T", "tickets": {"1476": {"title": "A"}, "1477": {"title": "B"}}}),
        encoding="utf-8",
    )
    lauf = _lauf([PWSH, "-NoProfile", "-File", SKRIPT, "-Spec", "48", "-Tickets", "14761477", "-DryRun"], tmp_path)
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 3, ausgabe
    assert "#14761477 steht nicht im Manifest" in ausgabe
    assert "Liste als eine Zahl gelesen?" in ausgabe
    assert '-Tickets "1476,1477"' in ausgabe


DOKU_DATEIEN = [
    *WURZEL.glob("*.md"),
    *WURZEL.glob("aliase/*/SKILL.md"),
    *WURZEL.glob("to_spawn/*.md"),
    WURZEL / "docs" / "kontext-manifest.md",
    WURZEL / "spawn_local.ps1",
    WURZEL / "spawn_srv.ps1",
]
# Wert nach -Tickets ohne Anführungszeichen (oder Klammer/Option) = abweichende Schreibweise.
UNGEQUOTET = re.compile(r"(?<!\w)-Tickets\s+[^\s\"'\[-][^\s\"']*[\d,]")


def test_doku_nennt_nur_den_gequoteten_aufruf() -> None:
    schlecht = [
        f"{d.name}:{n}: {zeile.strip()}"
        for d in DOKU_DATEIEN
        if d.is_file()
        for n, zeile in enumerate(d.read_text(encoding="utf-8-sig").splitlines(), 1)
        if UNGEQUOTET.search(zeile)
    ]
    assert not schlecht, "ungequotete -Tickets-Liste in Doku: " + "; ".join(schlecht)
