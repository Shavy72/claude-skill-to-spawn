"""L1 Etappe 1 P4b: Starter-Shell-Skripte lesen den Manifest-Ordner aus der Konfig.

``skripte/spawn_srv.sh`` und ``install.sh`` nehmen ``pfade.manifeste`` aus
``.to-spawn/config.json``; fehlt der Schlüssel, gilt die Vorgabe (heutiger Ordner).
Die ``.ps1``-Dateien sind auf dem Server nicht ausführbar — dort nur Text-Prüfungen,
dass der Rückfall ergänzt und der alte Weg erhalten ist.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from conftest import attrappe

SKILL = Path(__file__).resolve().parents[1]
VORGABE = "docs/agents/manifests"


def _repo(tmp_path: Path, manifeste: str | None = None) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    if manifeste is not None:
        (repo / ".to-spawn").mkdir()
        (repo / ".to-spawn" / "config.json").write_text(
            json.dumps({"pfade": {"manifeste": manifeste}}), encoding="utf-8"
        )
    return repo


def _env(tmp_path: Path, repo: Path) -> dict[str, str]:
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    attrappe(stub, "gh", "raise SystemExit(1)")
    env = dict(os.environ)
    env.update(HOME=str(tmp_path / "home"), TO_SPAWN_REPO=str(repo), PATH=f"{stub}:{env.get('PATH', '')}")
    return env


def _spawn_srv(tmp_path: Path, repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SKILL / "skripte" / "spawn_srv.sh"), "9", "--dry-run"],
        cwd=repo,
        env=_env(tmp_path, repo),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _install(tmp_path: Path, repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SKILL / "install.sh"), "--skills", str(tmp_path / "skills"), "--repo", str(repo)],
        cwd=tmp_path,
        env=_env(tmp_path, repo),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_spawn_srv_manifest_aus_konfig(tmp_path: Path) -> None:
    lauf = _spawn_srv(tmp_path, _repo(tmp_path, "eigen/manifeste"))
    assert lauf.returncode == 2, lauf.stderr
    assert "eigen/manifeste/spec-9.json" in lauf.stderr


def test_spawn_srv_ohne_schluessel_vorgabe(tmp_path: Path) -> None:
    lauf = _spawn_srv(tmp_path, _repo(tmp_path))
    assert lauf.returncode == 2, lauf.stderr
    assert f"{VORGABE}/spec-9.json" in lauf.stderr


def test_install_sh_manifest_aus_konfig(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "eigen/manifeste")
    lauf = _install(tmp_path, repo)
    assert lauf.returncode == 0, lauf.stderr
    # #274 P1: Manifest-Ordner aus der Konfig ja, Regel-Kopie _default.json nein.
    assert (repo / "eigen" / "manifeste").is_dir()
    assert not (repo / "eigen" / "manifeste" / "_default.json").exists()
    assert not (repo / VORGABE).exists()


def test_install_sh_heutiger_aufruf_gleich(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert _install(tmp_path, repo).returncode == 0
    ziel = tmp_path / "skills" / "to-spawn"
    assert (repo / VORGABE).is_dir()
    assert not (repo / VORGABE / "_default.json").exists()  # #274 P1: keine Regel-Kopie
    assert (repo / "scripts" / "bau.py").is_file()
    assert (ziel / "skripte" / "spawn_srv.sh").is_file()
    skills = tmp_path / "skills"
    assert not (skills / "_alt").exists()
    installiert = sorted(p.name for p in skills.iterdir() if p.is_dir())
    assert "to-spawn" in installiert and "to-spawn-local" in installiert
    zweiter = _install(tmp_path, repo)
    assert zweiter.returncode == 0, zweiter.stderr
    # Je installiertem Skill-Ordner genau eine Sicherung _alt/<name>-<JJJJMMTT-hhmmss>, alle aus einem Lauf.
    sicherungen = [re.fullmatch(r"(.+)-(\d{8}-\d{6})", p.name) for p in (skills / "_alt").iterdir()]
    assert all(sicherungen), [p.name for p in (skills / "_alt").iterdir()]
    assert sorted(m.group(1) for m in sicherungen) == installiert
    assert len({m.group(2) for m in sicherungen}) == 1


def test_ps1_rueckfall_ergaenzt_alter_weg_bleibt() -> None:
    lokal = (SKILL / "spawn_local.ps1").read_text(encoding="utf-8")
    assert "config.pfad(" in lokal and '"docs/agents/manifests/spec-$Spec.json"' in lokal
    assert '"$PSScriptRoot/skripte/bau.py"' in lokal and '"./scripts/bau.py"' in lokal
    inst = (SKILL / "install.ps1").read_text(encoding="utf-8")
    assert "config.pfad(" in inst and '"docs\\agents\\manifests"' in inst
    for skript in ("bau.py", "wache.py", "sessions_stand.py"):
        assert f"\\.claude\\skills\\to-spawn\\skripte\\{skript}" in inst
        assert f'Test-Path "./scripts/{skript}"' in inst


def test_install_sh_mit_pycache_in_repo_scripts(tmp_path: Path) -> None:
    """#155: liegt ``repo-scripts/__pycache__`` im Skill, bricht der Installer nicht ab und kopiert keinen Bytecode."""
    kopie = tmp_path / "skill"
    shutil.copytree(SKILL, kopie, ignore=shutil.ignore_patterns(".git"))
    (kopie / "repo-scripts" / "__pycache__").mkdir(exist_ok=True)
    (kopie / "repo-scripts" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    repo = _repo(tmp_path)
    lauf = subprocess.run(
        ["bash", str(kopie / "install.sh"), "--skills", str(tmp_path / "skills"), "--repo", str(repo)],
        cwd=tmp_path,
        env=_env(tmp_path, repo),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert lauf.returncode == 0, lauf.stderr
    assert (repo / "scripts" / "bau.py").is_file()
    assert not (repo / "scripts" / "__pycache__").exists()
