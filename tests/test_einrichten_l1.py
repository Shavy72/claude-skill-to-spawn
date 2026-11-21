"""Einrichter (L1 Etappe 1, P5): Soll-Liste als Daten, ``stand.json``, ``--pruefen``.

Alles in einem Wegwerf-Repo unter ``tmp_path``; ``gh`` ist eine Attrappe über
``TO_SPAWN_GH_STUB`` (kein echter GitHub-Aufruf), die Maschinen-Liste lenkt
``tests/conftest.py`` auf ``tmp_path``.
"""

from __future__ import annotations

import dataclasses
import io
import json
import subprocess
from pathlib import Path

import pytest

from to_spawn import config, einrichten


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ort, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Frisches App-Repo mit getrackter ``.gitignore`` und GitHub-origin."""
    ort = tmp_path / "app"
    ort.mkdir()
    _git(ort, "init", "-q", "-b", "main")
    _git(ort, "config", "user.email", "t@t")
    _git(ort, "config", "user.name", "t")
    _git(ort, "remote", "add", "origin", "https://github.com/test-org/app.git")
    (ort / "README.md").write_text("app\n", encoding="utf-8")
    (ort / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    _git(ort, "add", ".")
    _git(ort, "commit", "-q", "-m", "start")
    stub = tmp_path / "gh_stub.py"
    stub.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    return ort


def _lauf(repo: Path, pruefen: bool = False) -> tuple[int, str]:
    ausgabe = io.StringIO()
    code = einrichten.einrichten(repo, pruefen=pruefen, ausgabe=ausgabe)
    return code, ausgabe.getvalue()


def _stand(repo: Path) -> Path:
    return repo / ".to-spawn" / "stand.json"


def test_frisch_einrichten_dann_pruefen_exit_0_und_git_status_leer(repo: Path) -> None:
    gitignore_vorher = (repo / ".gitignore").read_bytes()
    code, text = _lauf(repo)
    assert code == 0, text
    stand = json.loads(_stand(repo).read_text(encoding="utf-8"))
    assert set(stand) == {"skill_stand", "bausteine"}
    assert set(stand["bausteine"]) == {b.name for b in einrichten._BAUSTEINE}
    assert (repo / config.KONFIG_PFAD).is_file()
    assert (repo / "docs" / "agents" / "manifests" / "_default.json").is_file()
    code, text = _lauf(repo, pruefen=True)
    assert code == 0, text
    assert "alles da und aktuell" in text
    assert _git(repo, "status", "--porcelain") == ""
    assert (repo / ".gitignore").read_bytes() == gitignore_vorher


def test_zweiter_lauf_schreibt_nichts(repo: Path) -> None:
    assert _lauf(repo)[0] == 0
    mtime = _stand(repo).stat().st_mtime_ns
    maschine_mtime = config.maschine_datei().stat().st_mtime_ns
    code, text = _lauf(repo)
    assert code == 0, text
    zeilen = [z for z in text.splitlines() if z.startswith("  ")]
    assert len(zeilen) == len(einrichten._BAUSTEINE)
    assert all(": da_aktuell" in z for z in zeilen), text
    assert _stand(repo).stat().st_mtime_ns == mtime
    assert config.maschine_datei().stat().st_mtime_ns == maschine_mtime


def test_erhoehte_version_zieht_genau_diesen_baustein_nach(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _lauf(repo)[0] == 0
    neu = [
        dataclasses.replace(b, version=b.version + 1) if b.name == "manifest" else b
        for b in einrichten._BAUSTEINE
    ]
    monkeypatch.setattr(einrichten, "_BAUSTEINE", neu)
    assert _lauf(repo, pruefen=True)[0] == 3
    code, text = _lauf(repo)
    assert code == 0, text
    zeilen = [z.strip() for z in text.splitlines() if z.startswith("  ")]
    nachgezogen = [z for z in zeilen if ": nachgezogen" in z]
    assert len(nachgezogen) == 1 and nachgezogen[0].startswith("manifest:"), text
    assert sum(": da_aktuell" in z for z in zeilen) == len(neu) - 1
    stand = json.loads(_stand(repo).read_text(encoding="utf-8"))
    assert (
        stand["bausteine"]["manifest"]
        == neu[[b.name for b in neu].index("manifest")].version
    )
    assert _lauf(repo, pruefen=True)[0] == 0


def test_pruefen_schreibt_nichts_und_meldet_abweichung(repo: Path) -> None:
    code, text = _lauf(repo, pruefen=True)
    assert code == 3, text
    assert not (repo / ".to-spawn").exists()
    assert not config.maschine_datei().exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_kaputte_stand_json_heisst_neu_einrichten(repo: Path) -> None:
    _stand(repo).parent.mkdir(parents=True)
    _stand(repo).write_text("{kaputt", encoding="utf-8")
    code, text = _lauf(repo)
    assert code == 0, text
    assert json.loads(_stand(repo).read_text(encoding="utf-8"))["bausteine"]
    assert _lauf(repo, pruefen=True)[0] == 0


def test_weiterleitungen_ungetrackt_erneuert_getrackt_nur_gemeldet(repo: Path) -> None:
    skripte = repo / "scripts"
    skripte.mkdir()
    (skripte / "bau.py").write_text("# alt, getrackt\n", encoding="utf-8")
    _git(repo, "add", "scripts/bau.py")
    _git(repo, "commit", "-q", "-m", "alte Weiterleitung")
    (skripte / "wache.py").write_text("# alt, ungetrackt\n", encoding="utf-8")
    code, text = _lauf(repo)
    assert code == 0, text  # große Lücke hält nicht auf
    vorlage = einrichten._VORLAGEN / "wache.py"
    assert (skripte / "wache.py").read_bytes() == vorlage.read_bytes()
    assert (skripte / "bau.py").read_text(encoding="utf-8") == "# alt, getrackt\n"
    assert "weiterleitungen: grosse_luecke" in text
    assert "veraltet" in text and "scripts/bau.py" in text
    assert _lauf(repo, pruefen=True)[0] == 3
