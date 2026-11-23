"""#277: Startzeile für Ablösung und Folge-Runde läuft ohne Maschinen-Einstellungen.

Ein Spec-Klon hat keine ``.venv``, und die Shell-Funktion ``bau`` aus ``~/.bashrc``
gibt es nicht auf jedem Rechner. Die Startzeile aus ``capo.bau_startzeile`` muss
darum ``bau.py`` des Skills direkt aufrufen — echter Lauf in ``bash -lc`` mit leerem
``HOME`` (kein ``.bashrc``, keine Funktion ``bau``), Schalter ``--help`` statt Bau.
Stirbt ``bau.py``, steht der Exit-Code in ``.to-spawn/fenster_<N>.log``.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import capo, respawn

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="tmux-Startzeile läuft nur unter Linux-bash",
)

TICKET = 7


def _klon(tmp_path: Path) -> Path:
    """Spec-Klon wie ``~/<repo>-<S>``: ohne ``.venv``, ohne ``scripts/bau.py``."""
    repo = tmp_path / "repo-9"
    repo.mkdir()
    return repo


def _bash(zeile: str, repo: Path, home: Path) -> subprocess.CompletedProcess[str]:
    home.mkdir(exist_ok=True)
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
    }
    return subprocess.run(
        ["bash", "-lc", zeile],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _laeuft_ohne_maschine(repo: Path, home: Path, *schalter: str) -> None:
    """Dieselbe Startzeile mit ``--help``: erreicht ``bau.py`` ohne ``.bashrc``/``.venv``."""
    lauf = _bash(capo.bau_startzeile(repo, TICKET, "", *schalter, "--help"), repo, home)
    assert lauf.returncode == 0, lauf.stderr
    assert "usage" in lauf.stdout.lower()


def test_folge_runde_startet_bau_py_ohne_bashrc_und_venv(tmp_path: Path) -> None:
    repo = _klon(tmp_path)
    assert capo.folge_befehl(repo, 9, TICKET, ["wache 9"])[-1] == capo.bau_startzeile(
        repo, TICKET, ""
    )
    _laeuft_ohne_maschine(repo, tmp_path / "home")


def test_abloesung_startet_bau_py_ohne_bashrc_und_venv(tmp_path: Path) -> None:
    repo = _klon(tmp_path)
    argv = shlex.split(respawn._startbefehl(repo, TICKET))
    assert argv == [
        "bash",
        "-lc",
        capo.bau_startzeile(repo, TICKET, "", "--ohne-prompt"),
    ]
    _laeuft_ohne_maschine(repo, tmp_path / "home", "--ohne-prompt")


def test_startzeile_nennt_skill_bau_py_und_repo(tmp_path: Path) -> None:
    repo = _klon(tmp_path)
    zeile = capo.bau_startzeile(repo, TICKET, "", "--ohne-prompt")
    skill_bau = Path(capo.__file__).resolve().parent.parent / "skripte" / "bau.py"
    assert str(skill_bau) in zeile
    assert f"TO_SPAWN_REPO={repo}" in zeile
    assert ".venv" not in zeile and "bau 7" not in zeile


def test_gestorbenes_fenster_hinterlaesst_exit_code(tmp_path: Path) -> None:
    repo = _klon(tmp_path)
    zeile = capo.bau_startzeile(repo, TICKET, "", "--gibt-es-nicht")
    lauf = _bash(zeile, repo, tmp_path / "home")
    assert lauf.returncode == 2
    log = repo / ".to-spawn" / f"fenster_{TICKET}.log"
    assert log.is_file()
    assert f"bau.py #{TICKET} Exit 2" in log.read_text(encoding="utf-8")
