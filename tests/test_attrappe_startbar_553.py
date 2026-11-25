"""#553/#643: Eine Attrappe startet per Pfad, per Name (PATH) und per Shell-Befehl (git) auf jedem Betriebssystem."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path, PureWindowsPath

import pytest

from conftest import attrappe, shell_befehl


def test_attrappe_startet_per_rueckgabepfad(tmp_path: Path) -> None:
    skript = attrappe(tmp_path, "gh", "import sys\nprint('hallo', *sys.argv[1:])\n")
    erg = subprocess.run([str(skript), "x"], capture_output=True, text=True, check=True)
    assert erg.stdout.split() == ["hallo", "x"]


def test_attrappe_startet_per_name_ueber_path_mit_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attrappe(tmp_path, "meinwerkzeug", "import sys\nprint('a', *sys.argv[1:])\nsys.exit(3)\n")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    erg = subprocess.run([shutil.which("meinwerkzeug"), "x", "y"], capture_output=True, text=True, check=False)
    assert (erg.returncode, erg.stdout.split()) == (3, ["a", "x", "y"])


def test_shell_befehl_wandelt_windows_pfade_fuer_sh() -> None:
    """#643: ``shell_befehl`` liefert eine POSIX-Shell-Zeile ohne Backslashes und mit Quoting bei Leerzeichen."""
    befehl = shell_befehl(
        PureWindowsPath(r"C:\Users\nutzer\AppData\Local\Temp\ordner mit platz\upload-pack"),
        interpreter=PureWindowsPath(r"C:\Program Files\Python313\python.exe"),
    )
    assert "\\" not in befehl
    assert shlex.split(befehl) == [
        "C:/Program Files/Python313/python.exe",
        "C:/Users/nutzer/AppData/Local/Temp/ordner mit platz/upload-pack",
    ]


def test_attrappe_startet_als_git_uploadpack_mit_leerzeichen(tmp_path: Path) -> None:
    """#643: ``git fetch``/``ls-remote`` starten eine Attrappe über ``remote.origin.uploadpack`` plattformneutral."""
    basis = tmp_path / "ordner mit leerzeichen"
    bare = basis / "origin.git"
    klon = basis / "klon"
    basis.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(klon)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(klon),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "a",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(klon), "push", "-q", "-u", "origin", "main"], check=True)

    marke = basis / "zweiter-aufruf"
    wrapper = attrappe(
        basis,
        "upload-pack-attrappe",
        f"""
        import pathlib, subprocess, sys
        marke = pathlib.Path({str(marke)!r})
        if marke.is_file():
            print("Server weg", file=sys.stderr)
            sys.exit(1)
        marke.touch()
        sys.exit(subprocess.run(["git", "upload-pack", *sys.argv[1:]], check=False).returncode)
        """,
    )
    subprocess.run(["git", "-C", str(klon), "config", "remote.origin.uploadpack", shell_befehl(wrapper)], check=True)

    erster = subprocess.run(
        ["git", "-C", str(klon), "fetch", "-q", "origin"], capture_output=True, text=True, check=False
    )
    zweiter = subprocess.run(
        ["git", "-C", str(klon), "ls-remote", "--heads", "origin"], capture_output=True, text=True, check=False
    )

    assert erster.returncode == 0, erster.stderr
    assert zweiter.returncode != 0 and "Server weg" in zweiter.stderr
