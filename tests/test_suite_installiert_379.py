"""``skripte/suite.sh`` ist ein Werkzeug nur für den Klon (#353, Befund PR 379).

Im installierten Skill fehlt die Wurzel-``conftest.py`` (``collect_ignore = ["review-hook"]``,
siehe ``tests/test_installer_vollstaendig.py``), ``review-hook/`` liegt aber dort → pytest ohne
Pfad hätte 10 Sammelfehler (Exit 2). Soll: ohne Wurzel-``conftest.py`` bricht ``suite.sh`` mit
einer einzeiligen Meldung ab und startet pytest gar nicht.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from conftest import attrappe

SKILL = Path(__file__).resolve().parent.parent


def _klon_vorbereiten(tmp_path: Path, exit_code: int = 0) -> tuple[Path, Path, dict[str, str]]:
    """Minimal-Klon mit ``skripte/suite.sh``, Wurzel-``conftest.py`` und Python-Attrappe."""
    klon = tmp_path / "klon"
    (klon / "skripte").mkdir(parents=True)
    shutil.copy(SKILL / "skripte" / "suite.sh", klon / "skripte" / "suite.sh")
    (klon / "conftest.py").write_text("", encoding="utf-8")
    spur = tmp_path / "python_aufruf.json"
    attrappen = tmp_path / "bin"
    attrappe(
        attrappen,
        "python",
        f"""
        import json, os, pathlib, sys
        pathlib.Path({str(spur)!r}).write_text(
            json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}),
            encoding="utf-8",
        )
        raise SystemExit({exit_code})
        """,
    )
    env = {**os.environ, "PATH": f"{attrappen}{os.pathsep}{os.environ['PATH']}"}
    return klon, spur, env


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

    assert not spur.exists(), "pytest wurde trotz fehlender Wurzel-conftest.py gestartet"
    assert lauf.returncode == 64, (lauf.returncode, lauf.stderr)
    zeilen = lauf.stderr.strip().splitlines()
    assert len(zeilen) == 1 and "conftest.py" in zeilen[0] and "Klon" in zeilen[0], lauf.stderr


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
    assert spur.read_text(encoding="utf-8").startswith("-m pytest"), spur.read_text(encoding="utf-8")


def test_suite_sh_nutzt_vier_worker_und_worksteal(tmp_path: Path) -> None:
    """#353: Standard-Aufruf nutzt vier Worker mit ``--dist worksteal`` gegen langsame End-Worker."""
    klon, spur, env = _klon_vorbereiten(tmp_path)

    lauf = subprocess.run(
        ["bash", str(klon / "skripte" / "suite.sh")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert lauf.returncode == 0, lauf.stderr
    daten = json.loads(spur.read_text(encoding="utf-8"))
    assert daten["argv"] == [
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-n",
        "4",
        "--dist",
        "worksteal",
    ], daten["argv"]


def test_suite_sh_reicht_argumente_cwd_und_exit_code_durch(tmp_path: Path) -> None:
    """#353: Aufruf aus fremdem Ordner wechselt in den Klon, reicht Argumente mit Leerzeichen
    und Exit-Codes ungleich 0 unverändert durch."""
    klon, spur, env = _klon_vorbereiten(tmp_path, exit_code=1)
    woanders = tmp_path / "woanders"
    woanders.mkdir()

    lauf = subprocess.run(
        ["bash", str(klon / "skripte" / "suite.sh"), "-k", "foo bar", "--durations=5"],
        cwd=str(woanders),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert lauf.returncode == 1, (lauf.returncode, lauf.stderr)
    daten = json.loads(spur.read_text(encoding="utf-8"))
    assert Path(daten["cwd"]).resolve() == klon.resolve(), daten["cwd"]
    assert daten["argv"][-3:] == ["-k", "foo bar", "--durations=5"], daten["argv"]


def test_suite_sh_setzt_tmpdir_aus_temp_ordner_wenn_leer(tmp_path: Path) -> None:
    """#355/#353 (Linux-Teil): ohne gesetztes ``TMPDIR`` lenkt ``skripte/suite.sh`` Test-Temp
    über ``to_spawn.temp_ordner.pfad()`` auf die Platte; ein vorgegebenes ``TMPDIR`` gewinnt."""
    import sys

    if sys.platform == "win32":
        import pytest

        pytest.skip("Linux-Teil von skripte/suite.sh (Windows delegiert an suite.py)")

    klon = tmp_path / "klon"
    (klon / "skripte").mkdir(parents=True)
    shutil.copy(SKILL / "skripte" / "suite.sh", klon / "skripte" / "suite.sh")
    (klon / "conftest.py").write_text("", encoding="utf-8")
    (klon / "to_spawn").symlink_to(SKILL / "to_spawn", target_is_directory=True)
    spur = tmp_path / "pytest_env.json"
    attrappen = tmp_path / "bin"
    attrappe(
        attrappen,
        "python",
        f"""
        import json, os, pathlib, subprocess, sys
        if sys.argv[1:3] == ["-m", "pytest"]:
            pathlib.Path({str(spur)!r}).write_text(
                json.dumps({{
                    "argv": sys.argv[1:],
                    "TMPDIR": os.environ.get("TMPDIR"),
                    "TEMP": os.environ.get("TEMP"),
                    "TMP": os.environ.get("TMP"),
                }}),
                encoding="utf-8",
            )
            raise SystemExit(0)
        raise SystemExit(subprocess.call([{sys.executable!r}, *sys.argv[1:]]))
        """,
    )
    platte = tmp_path / "platte-temp"
    basis_env = {k: v for k, v in os.environ.items() if k not in ("TMPDIR", "TEMP", "TMP")}
    basis_env["PATH"] = f"{attrappen}{os.pathsep}{basis_env['PATH']}"
    basis_env["TO_SPAWN_TEMP_ORDNER"] = str(platte)

    lauf = subprocess.run(
        ["bash", str(klon / "skripte" / "suite.sh")],
        capture_output=True,
        text=True,
        env=basis_env,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stderr
    daten = json.loads(spur.read_text(encoding="utf-8"))
    assert daten["TMPDIR"] == str(platte), daten
    assert daten["TEMP"] == str(platte), daten
    assert daten["TMP"] == str(platte), daten
    assert platte.is_dir()

    # Vorgegebenes TMPDIR des Aufrufers bleibt unverändert.
    eigen = tmp_path / "eigen-tmp"
    eigen.mkdir()
    lauf_eigen = subprocess.run(
        ["bash", str(klon / "skripte" / "suite.sh")],
        capture_output=True,
        text=True,
        env={**basis_env, "TMPDIR": str(eigen)},
        check=False,
    )
    assert lauf_eigen.returncode == 0, lauf_eigen.stderr
    daten_eigen = json.loads(spur.read_text(encoding="utf-8"))
    assert daten_eigen["TMPDIR"] == str(eigen), daten_eigen
