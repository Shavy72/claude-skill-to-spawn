"""rot_probe #1493: Worktree auf der Platte, verwaiste Ordner weg, Platz-Check vor dem Anlegen."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from review_hilfen import git, lauf

ALT = time.time() - 2 * 3600


def probe(repo: Path, *args: str) -> dict:
    ergebnis = lauf("rot_probe.py", "--repo", str(repo), *args)
    assert ergebnis.returncode == 0, ergebnis.stderr
    return json.loads(ergebnis.stdout)


def toter_pid() -> int:
    kind = subprocess.Popen(["true"])
    kind.wait()
    return kind.pid


def alt_machen(*pfade: Path) -> None:
    for pfad in pfade:
        os.utime(pfad, (ALT, ALT))


@pytest.fixture
def ordner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    pfad = tmp_path / "rot-probe"
    pfad.mkdir()
    monkeypatch.setenv("ROT_PROBE_ORDNER", str(pfad))
    return pfad


def test_abgebrochener_lauf_wird_weggeraeumt_lebender_bleibt(repo: Path, ordner: Path) -> None:
    (repo / "test_leer.py").write_text("def test_nichts():\n    assert True\n", encoding="utf-8")
    # Abgebrochener Lauf: echter Worktree, Besitzer tot, alt.
    verwaist = ordner / "rpverwaist"
    verwaist.mkdir()
    git(repo, "worktree", "add", "--detach", str(verwaist / "wt"), "HEAD")
    (verwaist / "besitzer.pid").write_text(str(toter_pid()), encoding="utf-8")
    alt_machen(verwaist / "besitzer.pid", verwaist)
    # Paralleler Lauf: alt, aber Besitzer lebt.
    lebend = ordner / "rplebend"
    lebend.mkdir()
    (lebend / "besitzer.pid").write_text(str(os.getpid()), encoding="utf-8")
    alt_machen(lebend / "besitzer.pid", lebend)
    # Gerade angelegt, Besitzer-Datei noch nicht geschrieben.
    frisch = ordner / "rpfrisch"
    frisch.mkdir()

    daten = probe(repo, "--files", "test_leer.py")

    assert not verwaist.exists()
    assert lebend.is_dir() and frisch.is_dir()
    assert len(git(repo, "worktree", "list").strip().splitlines()) == 1
    assert sorted(p.name for p in ordner.iterdir()) == ["rpfrisch", "rplebend"], daten


def test_platz_knapp_umgebung_ohne_worktree(repo: Path, ordner: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "test_leer.py").write_text("def test_nichts():\n    assert True\n", encoding="utf-8")
    monkeypatch.setenv("ROT_PROBE_MIN_FREI_MB", str(10**9))
    daten = probe(repo, "--files", "test_leer.py")
    assert daten["befunde"] == []
    assert daten["hinweis"].startswith("Umgebung: Platz knapp"), daten
    assert list(ordner.iterdir()) == []
    assert len(git(repo, "worktree", "list").strip().splitlines()) == 1


def test_genug_platz_probe_laeuft_im_ordner(repo: Path, ordner: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "test_leer.py").write_text("def test_nichts():\n    assert True\n", encoding="utf-8")
    monkeypatch.setenv("ROT_PROBE_MIN_FREI_MB", "1")
    daten = probe(repo, "--files", "test_leer.py")
    assert "Platz knapp" not in daten["hinweis"]
    assert [b["schwere"] for b in daten["befunde"]] == ["hoch"], daten
    assert list(ordner.iterdir()) == []
