"""``skripte/suite.sh`` ist ein Werkzeug nur für den Klon (#353, Befund PR 379).

Im installierten Skill fehlt die Wurzel-``conftest.py`` (``collect_ignore = ["review-hook"]``,
siehe ``tests/test_installer_vollstaendig.py``), ``review-hook/`` liegt aber dort → pytest ohne
Pfad hätte 10 Sammelfehler (Exit 2). Soll: ohne Wurzel-``conftest.py`` bricht ``suite.sh`` mit
einer einzeiligen Meldung ab und startet pytest gar nicht.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from conftest import attrappe

SKILL = Path(__file__).resolve().parent.parent


def test_suite_sh_bricht_ohne_wurzel_conftest_ab_ohne_pytest(tmp_path: Path) -> None:
    (tmp_path / "skripte").mkdir()
    shutil.copy(SKILL / "skripte" / "suite.sh", tmp_path / "skripte" / "suite.sh")
    # Attrappe für ``python``: hinterlässt eine Spur, falls pytest doch gestartet wird.
    attrappen = tmp_path / "bin"
    attrappen.mkdir()
    spur = tmp_path / "python_gestartet"
    attrappe(
        attrappen,
        "python",
        f"import pathlib\npathlib.Path({str(spur)!r}).write_text(' '.join(__import__('sys').argv[1:]))",
    )
    env = {**os.environ, "PATH": f"{attrappen}{os.pathsep}{os.environ['PATH']}"}

    lauf = subprocess.run(
        ["bash", str(tmp_path / "skripte" / "suite.sh")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert not spur.exists(), (
        "pytest wurde trotz fehlender Wurzel-conftest.py gestartet"
    )
    assert lauf.returncode == 64, (lauf.returncode, lauf.stderr)
    zeilen = lauf.stderr.strip().splitlines()
    assert len(zeilen) == 1 and "conftest.py" in zeilen[0] and "Klon" in zeilen[0], (
        lauf.stderr
    )


def test_suite_sh_ruft_mit_wurzel_conftest_pytest_auf(tmp_path: Path) -> None:
    (tmp_path / "skripte").mkdir()
    shutil.copy(SKILL / "skripte" / "suite.sh", tmp_path / "skripte" / "suite.sh")
    (tmp_path / "conftest.py").write_text("", encoding="utf-8")
    spur = tmp_path / "python_gestartet"
    attrappe(
        tmp_path / "bin",
        "python",
        f"import pathlib, sys\npathlib.Path({str(spur)!r}).write_text(' '.join(sys.argv[1:]))",
    )
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}"}

    lauf = subprocess.run(
        ["bash", str(tmp_path / "skripte" / "suite.sh")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert lauf.returncode == 0, lauf.stderr
    assert spur.read_text(encoding="utf-8").startswith("-m pytest"), spur.read_text(
        encoding="utf-8"
    )
