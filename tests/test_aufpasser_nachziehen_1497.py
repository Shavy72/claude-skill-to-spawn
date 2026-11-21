"""Aufpasser zieht Klone laufender Sessions nach (duoplus-management#1497).

Weg-Test mit echten Git-Repos (bare origin + Klon), ohne Attrappen.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, name: str, inhalt: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(inhalt, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)


def _aufbau(tmp: Path, nachziehen: bool = True) -> tuple[Path, Path]:
    """origin mit Konfig, Klon, danach ein neuer Commit nur auf origin."""
    origin = tmp / "origin.git"
    _git(tmp, "init", "-q", "--bare", "-b", "master", str(origin))
    quelle = tmp / "quelle"
    _git(tmp, "clone", "-q", str(origin), str(quelle))
    konfig: dict = {"hauptzweig": "master"}
    if nachziehen:
        konfig["nachziehen"] = {"danach": ["sh", "-c", "echo lief > danach.txt"]}
    _commit(quelle, ".to-spawn/config.json", json.dumps(konfig))
    _git(quelle, "push", "-q", "origin", "master")
    klon = tmp / "klon"
    _git(tmp, "clone", "-q", str(origin), str(klon))
    _commit(quelle, "neu.txt", "neu")
    _git(quelle, "push", "-q", "origin", "master")
    return quelle, klon


def test_klon_hinter_origin_wird_vorgespult_und_danach_laeuft(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    (klon / "eigene_notiz.txt").write_text("bleibt", encoding="utf-8")  # unversioniert
    ergebnis = aufpasser.klon_nachziehen(klon)
    assert ergebnis == "nachgezogen"
    assert _git(klon, "rev-parse", "HEAD") == _git(klon, "rev-parse", "origin/master")
    assert (klon / "neu.txt").is_file()
    assert (klon / "danach.txt").read_text(encoding="utf-8").strip() == "lief"
    assert (klon / "eigene_notiz.txt").read_text(encoding="utf-8") == "bleibt"


def test_eigener_commit_bleibt_unberuehrt(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    _commit(klon, "lokal.txt", "lokal")
    vorher = _git(klon, "rev-parse", "HEAD")
    assert aufpasser.klon_nachziehen(klon).startswith("übersprungen")
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_anderer_zweig_bleibt_unberuehrt(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    _git(klon, "checkout", "-q", "-b", "ticket/1-x")
    vorher = _git(klon, "rev-parse", "HEAD")
    assert aufpasser.klon_nachziehen(klon).startswith("übersprungen")
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_ohne_schluessel_in_origin_nichts_angefasst(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path, nachziehen=False)
    vorher = _git(klon, "rev-parse", "HEAD")
    assert aufpasser.klon_nachziehen(klon) == "aus"
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_kein_git_ordner(tmp_path: Path) -> None:
    assert aufpasser.klon_nachziehen(tmp_path) == "aus"
