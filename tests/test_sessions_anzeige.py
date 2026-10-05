"""sessions-Anzeige: UTF-8-Ausgabe auch bei cp1252-Pipe (PC) und Spec-Klon auf dem Bau-Server."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
NEST = SKILL / "nest" / "nest_server.sh"


def test_sessions_stand_stuerzt_bei_cp1252_stdout_nicht_ab() -> None:
    code = (
        "import sys; sys.path.insert(0, 'skripte'); import sessions_stand as s;"
        "s.manifeste_lesen = lambda spec: {}; s.prozesse_lesen = lambda: [];"
        "s.zuordnen = lambda *a: None; s.token_eintragen = lambda *a: None;"
        "s.tabelle = lambda *a, **k: 'Zustand \u2713 Umlaute äöü';"
        "sys.exit(s.main(['1']))"
    )
    lauf = subprocess.run(
        [sys.executable, "-c", code],
        cwd=SKILL,
        capture_output=True,
        env={"PYTHONIOENCODING": "cp1252", "PATH": __import__("os").environ.get("PATH", "")},
        check=False,
        timeout=60,
    )
    assert lauf.returncode == 0, lauf.stderr.decode("utf-8", "replace")
    assert "\u2713".encode() in lauf.stdout


def _funktionen() -> str:
    text = NEST.read_text(encoding="utf-8")
    m = re.search(r"^_bau_py\(\) \{.*?^sessions\(\) \{[^\n]*\n", text, re.DOTALL | re.MULTILINE)
    assert m
    return m.group(0)


def _bash() -> str | None:
    """Git-Bash (Windows: ``bash`` im PATH ist oft WSL ohne /bin/bash) oder System-bash."""
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    return str(git_bash) if git_bash.exists() else shutil.which("bash")


@pytest.mark.skipif(_bash() is None, reason="bash fehlt")
def test_sessions_nimmt_spec_klon_sonst_repo(tmp_path: Path) -> None:
    home = tmp_path / "home"
    for name in ("duoplus-management", "duoplus-551"):
        (home / name / "scripts").mkdir(parents=True)
        (home / name / "scripts" / "sessions_stand.py").write_text(
            f"import sys\nprint('KLON {name}', *sys.argv[1:])\n", encoding="utf-8"
        )
    block = tmp_path / "f.sh"
    block.write_text(_funktionen(), encoding="utf-8", newline="\n")
    skript = f'export HOME="{home.as_posix()}" REPO="{home.as_posix()}/duoplus-management"; source "{block.as_posix()}"; sessions 551; sessions 999; sessions'
    lauf = subprocess.run([_bash() or "bash", "-c", skript], capture_output=True, text=True, check=False, timeout=30)
    assert lauf.returncode == 0, lauf.stderr
    zeilen = lauf.stdout.splitlines()
    assert zeilen == ["KLON duoplus-551 551", "KLON duoplus-management 999", "KLON duoplus-management"]
