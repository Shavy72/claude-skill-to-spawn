"""#466/#467: probe/ wird mitinstalliert, anlegen.sh --github legt main im Fern-Repo an.

Alles in Wegwerf-Ordnern, nie gegen das echte ``~/.claude``; ``gh`` ist eine Attrappe
(legt ein lokales bare-Repo als ``origin`` an), kein Netz.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
GIT = shutil.which("git")
brauche_bash = pytest.mark.skipif(BASH is None or GIT is None, reason="braucht bash und git")


def _freie_ports(anzahl: int) -> list[int]:
    """Reserviert ``anzahl`` freie Ports gleichzeitig, damit laufende Proben (18502/18503) nicht stören."""
    with contextlib.ExitStack() as stapel:
        sockets = [stapel.enter_context(socket.socket()) for _ in range(anzahl)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [int(sock.getsockname()[1]) for sock in sockets]


@brauche_bash
def test_install_sh_kopiert_probe(tmp_path: Path) -> None:
    env = dict(
        os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), PYTHONIOENCODING="utf-8", TO_SPAWN_PY=sys.executable
    )
    lauf = subprocess.run(
        [
            str(BASH),
            str(SKILL / "install.sh"),
            "--skills",
            str(tmp_path / "skills"),
            "--claude-md",
            str(tmp_path / "CLAUDE.md"),
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert (tmp_path / "skills" / "to-spawn" / "probe" / "anlegen.sh").is_file()
    assert (tmp_path / "skills" / "to-spawn" / "probe" / "gemeinsam").is_dir()


def test_install_ps1_nennt_probe() -> None:
    text = (SKILL / "install.ps1").read_text(encoding="utf-8")
    assert '"probe"' in text


@brauche_bash
def test_anlegen_github_pusht_main_mit_startcommit(tmp_path: Path) -> None:
    bin_ordner = tmp_path / "bin"
    bin_ordner.mkdir()
    fern = tmp_path / "fern.git"
    gh = bin_ordner / "gh"
    # Attrappe: `gh repo create <name> --private --source . --remote origin` -> bare-Repo als origin.
    gh.write_text(
        f'#!/usr/bin/env bash\nset -e\ngit init -q --bare "{fern}"\ngit remote add origin "{fern}"\n',
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    ziel = tmp_path / "lauf"
    app_port, staging_port, live_port = _freie_ports(3)
    env = dict(os.environ, PATH=f"{bin_ordner}{os.pathsep}{os.environ['PATH']}")
    lauf = subprocess.run(
        [
            str(BASH),
            str(SKILL / "probe" / "anlegen.sh"),
            str(ziel),
            "mit-staging",
            "--app-port",
            str(app_port),
            "--staging-port",
            str(staging_port),
            "--live-port",
            str(live_port),
            "--github",
            "Shavy72/to-spawn-probe-x-mit",
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    zweige = subprocess.run(
        [str(GIT), "-C", str(fern), "branch", "--list", "main"], capture_output=True, text=True, check=True
    ).stdout
    assert "main" in zweige
    n = subprocess.run(
        [str(GIT), "-C", str(fern), "rev-list", "--count", "main"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert int(n) >= 1


@brauche_bash
@pytest.mark.parametrize("schalter", ["--app-port", "--staging-port", "--live-port", "--github"])
def test_447_anlegen_schalter_ohne_wert_meldet_klaren_fehler(tmp_path: Path, schalter: str) -> None:
    """#447: Wert-Schalter ohne Wert darf nicht still bei ``shift 2`` mit Exit 1 abbrechen."""
    ziel = tmp_path / "lauf"
    lauf = subprocess.run(
        [str(BASH), str(SKILL / "probe" / "anlegen.sh"), str(ziel), "mit-staging", schalter],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert lauf.returncode == 2, lauf.stdout + lauf.stderr
    assert schalter in lauf.stderr and "Wert" in lauf.stderr, lauf.stderr
    assert not ziel.exists()


@brauche_bash
def test_447_anlegen_github_push_fehler_meldet_leeres_fern_repo(tmp_path: Path) -> None:
    """#447 (Nachtrag PR 518): Scheitert nur der Push nach ``gh repo create``, nennt stderr
    das bereits angelegte leere Fern-Repo und den stehen gebliebenen lokalen Ordner."""
    bin_ordner = tmp_path / "bin"
    bin_ordner.mkdir()
    fern = tmp_path / "fern.git"
    fehlend = tmp_path / "nicht_da.git"
    gh = bin_ordner / "gh"
    # Attrappe: Repo wird angelegt (leer), aber origin zeigt auf ein unerreichbares Ziel -> Push scheitert.
    gh.write_text(
        f'#!/usr/bin/env bash\nset -e\ngit init -q --bare "{fern.as_posix()}"\n'
        f'git remote add origin "{fehlend.as_posix()}"\n',
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    ziel = tmp_path / "lauf_push_fehler"
    app_port, staging_port, live_port = _freie_ports(3)
    env = dict(os.environ, PATH=f"{bin_ordner}{os.pathsep}{os.environ['PATH']}")
    lauf = subprocess.run(
        [
            str(BASH),
            str(SKILL / "probe" / "anlegen.sh"),
            str(ziel),
            "mit-staging",
            "--app-port",
            str(app_port),
            "--staging-port",
            str(staging_port),
            "--live-port",
            str(live_port),
            "--github",
            "Shavy72/to-spawn-probe-x-mit",
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert "git push" in lauf.stderr, lauf.stderr
    assert "Shavy72/to-spawn-probe-x-mit" in lauf.stderr, lauf.stderr
    assert ziel.name in lauf.stderr, lauf.stderr
    assert (ziel / ".git").is_dir()


def test_463_keine_skill_md_nennt_alten_unterbefehl_pruefen() -> None:
    treffer = [
        f"{p.relative_to(SKILL)}:{n}"
        for p in SKILL.rglob("SKILL.md")
        for n, zeile in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "to_spawn.py pruefen <S>" in zeile
    ]
    assert treffer == []
