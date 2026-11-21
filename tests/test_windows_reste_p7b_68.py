"""L1 P7b (#79, Fehlerliste #68): Windows-Reste, unter Linux prüfbar.

- ``gh.repo_aus_origin`` erkennt einen Windows-Pfad mit ``\\`` als Origin (Gruppe 4).
- ``skripte/wache.py`` schreibt UTF-8 auch auf eine cp1252-Konsole (Gruppe 5c).
- ``conftest.braucht_fehlt``: der eine Schalter für tmux/bash/posix (Gruppen 2, 6, 9, 10).
- Test-Gitconfigs mit ``insteadOf`` nennen das Bare-Repo mit ``/`` (Gruppe 1).
- ``spawn_local.ps1`` erkennt laufende Worktrees am Namen aus ``config.worktree_pfad``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from conftest import braucht_fehlt

from to_spawn import config, gh

SKILL = Path(__file__).resolve().parent.parent


def _origin(tmp_path: Path, url: str) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", url], check=True)
    return tmp_path


def test_repo_aus_origin_windows_pfad(tmp_path: Path) -> None:
    repo = _origin(tmp_path, r"C:\Users\nutzer\AppData\Local\Temp\pytest-1\org\fern.git")
    assert gh.repo_aus_origin(repo, fallback="leer") == "org/fern"


def test_repo_aus_origin_posix_pfad_bleibt(tmp_path: Path) -> None:
    repo = _origin(tmp_path, "/tmp/pytest-1/org/fern.git")
    assert gh.repo_aus_origin(repo, fallback="leer") == "org/fern"


def test_wache_print_prompt_auf_cp1252_konsole(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    umgebung = {
        **os.environ,
        "TO_SPAWN_REPO": str(tmp_path),
        "TO_SPAWN_GH_REPO": "x/y",
        "PYTHONIOENCODING": "cp1252",
        "PYTHONUTF8": "0",
    }
    ergebnis = subprocess.run(
        [
            sys.executable,
            str(SKILL / "skripte" / "wache.py"),
            "900",
            "--dry-run",
            "--print-prompt",
        ],
        cwd=str(tmp_path),
        env=umgebung,
        capture_output=True,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr.decode("utf-8", "replace")[-500:]
    assert "→" in ergebnis.stdout.decode("utf-8")


def test_braucht_fehlt_regeln() -> None:
    nie = lambda _n: None
    stub = lambda _n: r"C:\Windows\System32\bash.exe"
    git_bash = lambda _n: r"C:\Program Files\Git\bin\bash.exe"
    assert braucht_fehlt("posix", "linux") is None
    assert braucht_fehlt("posix", "win32")
    assert braucht_fehlt("tmux", "linux", nie) == "tmux fehlt im PATH"
    assert braucht_fehlt("tmux", "linux", lambda _n: "/usr/bin/tmux") is None
    assert "WSL-Stub" in (braucht_fehlt("bash", "win32", stub) or "")
    assert braucht_fehlt("bash", "win32", git_bash) is None
    assert braucht_fehlt("bash", "linux", lambda _n: "/bin/bash") is None


def test_test_gitconfig_insteadof_mit_schraegstrich() -> None:
    """git verwirft ``\\`` im Unterabschnitt ``[url "…"]`` → Windows-Pfad muss ``as_posix()`` sein."""
    funde = []
    for datei in sorted((SKILL / "tests").glob("test_*.py")):
        for nr, zeile in enumerate(datei.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\[url \"\{", zeile) and "as_posix()" not in zeile:
                funde.append(f"{datei.name}:{nr}")
    assert funde == []


def test_spawn_local_erkennt_worktree_namen_aus_config() -> None:
    quelle = (SKILL / "spawn_local.ps1").read_text(encoding="utf-8")
    assert "duoplus-bau" not in quelle
    assert config.worktree_pfad(7, SKILL).endswith("/wt-7")
    assert "wt-(\\d+)" in quelle
