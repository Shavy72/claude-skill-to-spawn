"""Weg-Tests: Checkpoint mitten in der Kette ist ein Fehler (duoplus-management#325).

Echt laufen: CLI (``to_spawn.py pruefen``), Manifest, Git-Repo.
Gestellt ist nur die ``gh``-CLI (GitHub ist ein externer Dienst, per
``TO_SPAWN_GH_STUB``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
GH_STUB = Path(__file__).resolve().parent / "hilfen" / "gh_stub.py"

SPEC = 900
TICKETS = ("901", "902", "903")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Wegwerf-Repo mit origin, Manifest mit drei Tickets und gestelltem gh."""
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    _git(arbeit, "init")
    # GitHub-Form statt lokalem Bare-Pfad: repo_aus_origin liest auf Windows keinen
    # Backslash-Pfad; gh ist gestellt, es geht nichts ins Netz.
    _git(arbeit, "remote", "add", "origin", "https://github.com/wegwerf/repo.git")
    ordner = arbeit / "docs" / "agents" / "manifests"
    ordner.mkdir(parents=True)
    eintraege = {
        t: {
            "title": f"Wegwerf-Ticket {t}",
            "schaetzung_k": 120,
            "umfang": "Kern bauen.",
        }
        for t in TICKETS
    }
    (ordner / f"spec-{SPEC}.json").write_text(
        json.dumps(
            {"spec": SPEC, "feature": "wegwerf", "tickets": eintraege},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(GH_STUB))
    for name in ("GH_STUB_ZU", "GH_STUB_DATEN", "GH_STUB_BLOCKER", "TO_SPAWN_TICKET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(arbeit)
    return arbeit


def _checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *nummern: str
) -> None:
    daten = {
        t: {"labels": ["checkpoint:human" if t in nummern else "enhancement"]}
        for t in TICKETS
    }
    datei = tmp_path / "gh_daten.json"
    datei.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("GH_STUB_DATEN", str(datei))


def _pruefen(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), "pruefen", str(SPEC)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=dict(os.environ),
        check=False,
    )


def test_checkpoint_vor_anderen_tickets_weigert_sich(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#901 ist Checkpoint und blockiert #902 und #903 → Fehler, Exit ≠ 0."""
    monkeypatch.setenv("GH_STUB_BLOCKER", "902:901,903:901")
    _checkpoints(tmp_path, monkeypatch, "901")
    ergebnis = _pruefen(repo)
    assert ergebnis.returncode != 0, ergebnis.stdout + ergebnis.stderr
    assert (
        "Checkpoint-Ticket #901 steht mitten in der Kette: blockiert #902, #903"
        in ergebnis.stdout
    )
    assert "Abnahme gehört ans Kettenende" in ergebnis.stdout
    assert "Frage in den Grill vor den Spawn ziehen" in ergebnis.stdout


def test_checkpoint_am_kettenende_geht_durch(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#903 ist Checkpoint, von #901 und #902 blockiert, blockiert selbst keins → Exit 0."""
    monkeypatch.setenv("GH_STUB_BLOCKER", "902:901,903:901,903:902")
    _checkpoints(tmp_path, monkeypatch, "903")
    ergebnis = _pruefen(repo)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert "mitten in der Kette" not in ergebnis.stdout


def test_zwei_checkpoints_einer_mittig_weigert_sich(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#901 (Checkpoint) blockiert #902, #903 (Checkpoint) steht am Ende → Fehler für #901."""
    monkeypatch.setenv("GH_STUB_BLOCKER", "902:901,903:902")
    _checkpoints(tmp_path, monkeypatch, "901", "903")
    ergebnis = _pruefen(repo)
    assert ergebnis.returncode != 0, ergebnis.stdout + ergebnis.stderr
    assert (
        "Checkpoint-Ticket #901 steht mitten in der Kette: blockiert #902"
        in ergebnis.stdout
    )
    assert "Checkpoint-Ticket #903 steht mitten in der Kette" not in ergebnis.stdout
