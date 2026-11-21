"""L1 E2-5 (#89): Skill-Stand an einer Stelle — Installer stempelt, einrichten vergleicht mit main.

Weg-Test ohne Attrappen am Stand selbst: ein Skill-Klon mit Bare-Repo als ``origin``,
echte Installation (``install.sh`` bzw. ``install.ps1``) in ein Wegwerf-Ziel und
``to_spawn.py einrichten`` der installierten Kopie in einem Wegwerf-Repo. Nur ``gh`` ist
eine Attrappe (``TO_SPAWN_GH_STUB``), die Maschinen-Liste liegt unter ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import skill_stand

SKILL = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ort,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _commit(ort: Path, text: str) -> str:
    _git(ort, "add", "-A")
    _git(ort, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", text)
    return _git(ort, "rev-parse", "HEAD")


def _skill_klon(tmp: Path) -> tuple[Path, Path, str]:
    """Arbeitsstand des Skills als Klon mit Bare-Repo als ``origin`` (main)."""
    bare = tmp / "skill-origin.git"
    klon = tmp / "skill-klon"
    shutil.copytree(
        SKILL,
        klon,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".to-spawn"
        ),
    )
    _git(tmp, "init", "-q", "--bare", "-b", "main", str(bare))
    _git(klon, "init", "-q", "-b", "main")
    sha = _commit(klon, "start")
    _git(klon, "remote", "add", "origin", str(bare))
    _git(klon, "push", "-q", "origin", "main")
    return klon, bare, sha


def _umgebung(tmp: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    heim = tmp / "heim"
    heim.mkdir(exist_ok=True)
    stub = tmp / "gh_stub.py"
    stub.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    env.update(
        {
            "HOME": str(heim),
            "USERPROFILE": str(heim),
            "PYTHONIOENCODING": "utf-8",
            "TO_SPAWN_GH_STUB": str(stub),
            "TO_SPAWN_MASCHINE": str(tmp / "maschine.json"),
        }
    )
    return env


def _installiere(art: str, klon: Path, skills: Path, env: dict[str, str]) -> None:
    claude_md = skills.parent / "CLAUDE.md"
    if art == "sh":
        befehl = ["bash", str(klon / "install.sh"), "--skills", str(skills)]
        befehl += ["--claude-md", str(claude_md)]
    else:
        befehl = [PWSH or "pwsh", "-NoProfile", "-File", str(klon / "install.ps1")]
        befehl += ["-Skills", str(skills), "-ClaudeMd", str(claude_md)]
    lauf = subprocess.run(
        befehl, env=env, capture_output=True, text=True, encoding="utf-8", check=False
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr


def _app_repo(tmp: Path) -> Path:
    app = tmp / "app"
    app.mkdir()
    _git(app, "init", "-q", "-b", "main")
    _git(app, "remote", "add", "origin", "https://github.com/test-org/app.git")
    (app / "README.md").write_text("app\n", encoding="utf-8")
    _commit(app, "start")
    return app


def _einrichten(
    ziel: Path, app: Path, env: dict[str, str], *, pruefen: bool = False
) -> tuple[int, str]:
    befehl = [sys.executable, str(ziel / "to_spawn.py"), "einrichten"]
    lauf = subprocess.run(
        befehl + (["--pruefen"] if pruefen else []),
        cwd=app,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    return lauf.returncode, lauf.stdout + lauf.stderr


def _zeile(text: str, name: str) -> str:
    return next((z for z in text.splitlines() if z.strip().startswith(f"{name}:")), "")


INSTALLER = [
    pytest.param("sh", marks=pytest.mark.braucht("bash")),
    pytest.param(
        "ps1",
        marks=pytest.mark.skipif(
            not PWSH, reason="pwsh fehlt — install.ps1 am PC prüfen"
        ),
    ),
]


@pytest.mark.parametrize("art", INSTALLER)
def test_installer_stempelt_und_einrichten_vergleicht_mit_main(
    art: str, tmp_path: Path
) -> None:
    klon, bare, sha = _skill_klon(tmp_path)
    env = _umgebung(tmp_path)
    skills = tmp_path / "heim" / "skills"
    _installiere(art, klon, skills, env)
    ziel = skills / "to-spawn"
    assert not (ziel / ".git").exists()
    app = _app_repo(tmp_path)

    # 1. Rot heute: in der installierten Kopie war skill_stand immer leer (kein .git).
    code, text = _einrichten(ziel, app, env)
    assert code == 0, text
    stand = json.loads((app / ".to-spawn" / "stand.json").read_text(encoding="utf-8"))
    assert stand["skill_stand"], text
    assert sha.startswith(stand["skill_stand"])
    code, text = _einrichten(ziel, app, env, pruefen=True)
    assert code == 0, text
    assert "da_aktuell" in _zeile(text, "skill_aktuell"), text

    # 2. Neuerer main im origin → veraltet mit fertigem Befehl, Exit 3 (Abweichung).
    (klon / "NEU.txt").write_text("neu\n", encoding="utf-8")
    _commit(klon, "neuer Stand")
    _git(klon, "push", "-q", "origin", "main")
    code, text = _einrichten(ziel, app, env, pruefen=True)
    zeile = _zeile(text, "skill_aktuell")
    assert code == 3, text
    assert "veraltet" in zeile and ("install.sh" in zeile or "install.ps1" in zeile)
    assert str(klon) in zeile, zeile

    # 3. origin nicht erreichbar → nur Hinweis, Exit wie bisher (0).
    shutil.rmtree(bare)
    code, text = _einrichten(ziel, app, env, pruefen=True)
    zeile = _zeile(text, "skill_aktuell")
    assert code == 0, text
    assert "nicht erreichbar" in zeile, zeile


def test_git_klon_liefert_stand_ohne_netz(tmp_path: Path) -> None:
    """Im Git-Klon (Entwicklung) kommt der Stand aus Git, ein Vergleich braucht kein Netz."""
    klon, _bare, sha = _skill_klon(tmp_path)
    assert skill_stand.stand(klon) == sha[: skill_stand.KURZ]
    zustand, text = skill_stand.vergleich(klon)
    assert zustand == skill_stand.UNBEKANNT and "Git-Klon" in text
    leer = tmp_path / "leer"
    leer.mkdir()
    assert skill_stand.stand(leer) == ""
