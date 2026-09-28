"""to-spawn in jedem Repo: keine festen DuoPlus-Reste (Server-Repo, Hauptzweig, GitHub-Slug, Pi-Pfad).

``spawn_srv.ps1`` läuft als echter Prozess gegen ein Wegwerf-Repo; ``ssh`` ist ein
Stub auf dem PATH, der nur seine Argumente ausgibt (kein echter Server nötig).
Der Leitstand-Teil prüft die reine Rechnung ``seed_docs`` plus die Seite als Text.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parents[1]
OHNE_FENSTER = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, creationflags=OHNE_FENSTER)


def _wegwerf_repo(tmp_path: Path, konfig: dict[str, str] | None) -> Path:
    repo = tmp_path / "fremd"
    repo.mkdir()
    _git(repo, "init", "-q")
    if konfig is not None:
        (repo / ".to-spawn").mkdir()
        (repo / ".to-spawn" / "config.json").write_text(json.dumps(konfig), encoding="utf-8")
    return repo


def _ssh_stub(tmp_path: Path) -> dict[str, str]:
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "ssh.cmd").write_text("@echo SSH-STUB %*\r\n@exit /b 0\r\n", encoding="utf-8")
    (stub / "ssh").write_text('#!/bin/sh\necho "SSH-STUB $*"\n', encoding="utf-8")
    os.chmod(stub / "ssh", 0o755)
    return {**os.environ, "PATH": str(stub) + os.pathsep + os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}


def _ps1(repo: Path, umgebung: dict[str, str], *extra: str) -> subprocess.CompletedProcess[str]:
    assert PWSH, "pwsh/powershell fehlt"
    return subprocess.run(
        [PWSH, "-NoProfile", "-File", str(SKILL / "spawn_srv.ps1"), "-Spec", "7", *extra],
        cwd=repo,
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        creationflags=OHNE_FENSTER,
    )


@pytest.mark.skipif(PWSH is None or sys.platform != "win32", reason="spawn_srv.ps1 nur unter Windows")
def test_spawn_srv_ps1_nimmt_server_repo_hauptzweig_und_ssh_ziel_aus_konfig(tmp_path: Path) -> None:
    repo = _wegwerf_repo(tmp_path, {"server_repo": "~/fremd-repo", "hauptzweig": "main", "ssh_ziel": "mein-server"})
    lauf = _ps1(repo, _ssh_stub(tmp_path), "-DryRun")
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "cd ~/fremd-repo" in ausgabe
    assert "origin/main" in ausgabe and "= main ]" in ausgabe
    assert "SSH-STUB mein-server" in ausgabe
    assert "duoplus-management" not in ausgabe and "master" not in ausgabe


@pytest.mark.skipif(PWSH is None or sys.platform != "win32", reason="spawn_srv.ps1 nur unter Windows")
def test_spawn_srv_ps1_ohne_server_repo_bricht_mit_exit_2_ab(tmp_path: Path) -> None:
    repo = _wegwerf_repo(tmp_path, None)
    lauf = _ps1(repo, _ssh_stub(tmp_path), "-DryRun")
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 2, ausgabe
    assert "server_repo" in ausgabe
    assert "SSH-STUB" not in ausgabe


def _lade_leitstand() -> ModuleType:
    if str(SKILL) not in sys.path:
        sys.path.insert(0, str(SKILL))
    spec = importlib.util.spec_from_file_location("leitstand_fremd", SKILL / "skripte" / "leitstand.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules["leitstand_fremd"] = modul
    spec.loader.exec_module(modul)
    return modul


def test_leitstand_meta_traegt_repo_slug_und_seite_hat_keinen_festen_slug() -> None:
    ls = _lade_leitstand()
    manifest = {"tickets": {"8": {"umfang": "x"}}}

    def meta(**extra: str) -> dict[str, object]:
        docs = ls.seed_docs("7", manifest, {}, ziel="local", gestartet="x", faktor=None, ssh_ziel="s", **extra)
        return next(doc for sammlung, _, doc in docs if sammlung == "meta")

    assert meta(repo_slug="acme/werk")["repo"] == "acme/werk"
    assert "repo" not in meta()
    assert "repo" not in meta(repo_slug="kaputt")
    seite = (SKILL / "leitstand" / "seite.html").read_text(encoding="utf-8")
    assert "Shavy72" not in seite and "duoplus-management" not in seite
    assert "meta.repo" in seite


def test_pi_local_skill_nutzt_aktuelles_repo_und_prueft_pi_motor() -> None:
    # to-spawn-pi-local ist ein eigener Skill außerhalb dieses Repos (nicht in aliase/):
    # neben dem installierten Skill-Ordner oder unter ~/.claude/skills. Fehlt er (Klon auf
    # dem Bau-Server, CI), gibt es nichts zu prüfen → skip mit Grund statt FileNotFoundError.
    kandidaten = [
        SKILL.parent / "to-spawn-pi-local" / "SKILL.md",
        Path.home() / ".claude" / "skills" / "to-spawn-pi-local" / "SKILL.md",
    ]
    datei = next((k for k in kandidaten if k.is_file()), None)
    if datei is None:
        pytest.skip("Skill to-spawn-pi-local nicht installiert (gehört nicht zu diesem Repo)")
    text = datei.read_text(encoding="utf-8")
    assert "Desktop/DuoPlus" not in text
    assert "git rev-parse --show-toplevel" in text
    assert "Pi-Motor in diesem Repo nicht eingerichtet" in text
