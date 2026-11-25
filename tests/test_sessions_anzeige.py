"""sessions-Anzeige: UTF-8-Ausgabe auch bei cp1252-Pipe (PC) und Spec-Klon auf dem Bau-Server."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from git_bash import git_bash  # tests/hilfen: eine Tür ``to_spawn.bash_wahl``

from to_spawn import bash_wahl  # noqa: E402 — Pfad setzt ``git_bash``

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
    return git_bash()


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
    lauf = bash_wahl.lauf(
        ["-c", skript],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert lauf.returncode == 0, lauf.stderr
    zeilen = lauf.stdout.splitlines()
    assert zeilen == ["KLON duoplus-551 551", "KLON duoplus-management 999", "KLON duoplus-management"]


@pytest.mark.skipif(_bash() is None, reason="bash fehlt")
def test_nest_nachtrag_ersetzt_alte_sessions_funktion_idempotent(tmp_path: Path) -> None:
    text = NEST.read_text(encoding="utf-8")
    m = re.search(r"^# Nachtrag: ältere Nest-Köpfe kennen _klon.*?^fi\n", text, re.DOTALL | re.MULTILINE)
    assert m, "Nachtrag für bestehende .bashrc fehlt"
    (tmp_path / ".bashrc").write_text(
        '_bau_py() { :; }\nsessions() { _bau_py sessions_stand.py "$@"; }\n', encoding="utf-8", newline="\n"
    )
    blk = tmp_path / "blk.sh"
    blk.write_text(m.group(0), encoding="utf-8", newline="\n")
    for _ in range(2):
        lauf = bash_wahl.lauf(
            [str(blk)],
            env={**os.environ, "NUTZER_HOME": str(tmp_path)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        assert lauf.returncode == 0, lauf.stderr
    neu = (tmp_path / ".bashrc").read_text(encoding="utf-8")
    assert neu.count("_klon_fuer_spec() {") == 1
    assert '_klon_fuer_spec "${1:-}"' in neu


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-Zweig (ps)")
def test_prozesse_linux_volle_kommandozeile_trotz_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    """``COLUMNS=80`` (z. B. unter pytest-xdist, cron, tmux) darf ``args`` nicht kürzen (#1411)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skripte"))
    import sessions_stand

    monkeypatch.setenv("COLUMNS", "80")
    ende = "x" * 120 + "/skripte/bau.py 97531"
    schlaefer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", ende])
    try:
        zeilen = [p for p in sessions_stand.prozesse_linux() if p.pid == schlaefer.pid]
        assert zeilen and zeilen[0].cmd.endswith(ende)
    finally:
        schlaefer.kill()
        schlaefer.wait(timeout=10)
