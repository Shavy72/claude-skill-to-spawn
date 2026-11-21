"""L1 Etappe 1 P6a: Rest-Stellen folgen ``pfade.manifeste``/``pfade.bau_log`` der Repo-Konfig.

``startklar``, ``capo.log_vom_ref``, ``umzug`` (Manifest-Suche, ``sessions_stand``-Biegung),
``bau_log.log_im_repo`` (Texte an den Aufseher), ``manifest._pruefe_andere_manifeste``,
dazu der Ohne-Konfig-Weg von ``bau.py``/``sessions_stand.py`` (heutige DuoPlus-Werte) und
die Maschinen-Liste des Einrichters (``TO_SPAWN_MASCHINE`` in jedem Schreibweg).
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from to_spawn import bau_log, capo, config, einrichten, manifest, startklar, umzug

SKILL = Path(__file__).resolve().parent.parent
EIGEN = {"manifeste": "eigen/manifeste", "bau_log": "eigen/bau_log"}


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=ort, check=True, capture_output=True, text=True).stdout


def _repo(tmp_path: Path, **werte: object) -> Path:
    repo = tmp_path / "repo"
    (repo / ".to-spawn").mkdir(parents=True)
    (repo / ".to-spawn" / "config.json").write_text(json.dumps(werte), encoding="utf-8")
    return repo


def _manifest(ordner: Path, spec: int, tickets: list[str]) -> None:
    ordner.mkdir(parents=True, exist_ok=True)
    daten = {"spec": spec, "tickets": {t: {"title": f"T{t}"} for t in tickets}}
    (ordner / f"spec-{spec}.json").write_text(json.dumps(daten), encoding="utf-8")


def _lade_skript(name: str, repo: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location(f"{name}_p6a", SKILL / "skripte" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, modul)
    spec.loader.exec_module(modul)
    return modul


def test_startklar_manifest_folgt_konfig(tmp_path: Path) -> None:
    repo = _repo(tmp_path, pfade=EIGEN)
    assert startklar._manifest_pfad(repo, 7) == repo / "eigen" / "manifeste" / "spec-7.json"


def test_capo_log_vom_ref_folgt_konfig(tmp_path: Path) -> None:
    repo = _repo(tmp_path, pfade=EIGEN)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    ordner = repo / "eigen" / "bau_log"
    ordner.mkdir(parents=True)
    (ordner / "11.jsonl").write_text(json.dumps({"typ": "session_start"}) + "\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "log")
    assert capo.log_vom_ref(repo, "HEAD", 11) == [{"typ": "session_start"}]


def test_log_im_repo_vorgabe_eigen_und_aussen(tmp_path: Path) -> None:
    ohne = tmp_path / "ohne"
    ohne.mkdir()
    assert bau_log.log_im_repo(ohne, 7) == "docs/agents/bau_log/7.jsonl"
    assert bau_log.log_im_repo(_repo(tmp_path, pfade=EIGEN), 7) == "eigen/bau_log/7.jsonl"
    aussen = (tmp_path / "aussen").as_posix()
    repo2 = tmp_path / "r2"
    (repo2 / ".to-spawn").mkdir(parents=True)
    (repo2 / ".to-spawn" / "config.json").write_text(json.dumps({"pfade": {"bau_log": aussen}}), encoding="utf-8")
    assert bau_log.log_im_repo(repo2, 7) == f"{aussen}/7.jsonl"


def test_umzug_findet_spec_im_eigenen_manifest_ordner(tmp_path: Path) -> None:
    repo = _repo(tmp_path, pfade=EIGEN)
    _manifest(repo / "eigen" / "manifeste", 7, ["11"])
    _manifest(repo / "docs" / "agents" / "manifests", 8, ["11"])  # alter Ordner zählt nicht mehr
    assert umzug.spec_von_ticket("11", [repo]) == "7"
    assert umzug._sessions_stand(repo).MANIFESTE == repo / "eigen" / "manifeste"


def test_andere_manifeste_im_eigenen_ordner(tmp_path: Path) -> None:
    repo = _repo(tmp_path, pfade=EIGEN)
    _manifest(repo / "eigen" / "manifeste", 7, ["11"])
    _manifest(repo / "eigen" / "manifeste", 8, ["11"])
    bericht = manifest.Bericht(spec="7", tickets=["11"])
    manifest._pruefe_andere_manifeste(repo, bericht, {})
    assert any("spec-8.json" in f for f in bericht.fehler), bericht.fehler


def test_bau_und_sessions_stand_ohne_konfig_heutiger_ordner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    erwartet = repo.resolve() / "docs" / "agents" / "manifests"
    assert _lade_skript("bau", repo, monkeypatch).MANIFEST_DIR == erwartet
    assert _lade_skript("sessions_stand", repo, monkeypatch).MANIFESTE == erwartet


def _einricht_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ort = tmp_path / "app"
    ort.mkdir(parents=True)
    _git(ort, "init", "-q", "-b", "main")
    _git(ort, "config", "user.email", "t@t")
    _git(ort, "config", "user.name", "t")
    _git(ort, "remote", "add", "origin", "https://github.com/test-org/app.git")
    (ort / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    _git(ort, "add", ".")
    _git(ort, "commit", "-q", "-m", "start")
    stub = tmp_path / "gh_stub.py"
    stub.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    return ort


def test_einrichter_schreibt_maschinen_liste_nur_nach_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Jeder Schreibweg (Python-Aufruf und CLI) landet in ``TO_SPAWN_MASCHINE``, nie unter HOME."""
    heim = tmp_path / "heim"
    heim.mkdir()
    monkeypatch.setenv("HOME", str(heim))  # Rückfall-Ort läge damit sicher im tmp_path
    ziel = tmp_path / "eigen-maschine" / "maschine.json"
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(ziel))
    repo = _einricht_repo(tmp_path, monkeypatch)
    assert einrichten.einrichten(repo, ausgabe=io.StringIO()) == 0
    eintraege = [e["pfad"] for e in config.maschine()["repos"]]
    assert repo.resolve().as_posix() in eintraege
    ziel.unlink()
    zweit = _einricht_repo(tmp_path / "cli", monkeypatch)
    lauf = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "einrichten"],
        cwd=zweit,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert zweit.resolve().as_posix() in [e["pfad"] for e in config.maschine()["repos"]]
    assert not (heim / ".config" / "to-spawn" / "maschine.json").exists()
