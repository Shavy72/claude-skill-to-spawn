"""Optionales Spec-Feld ``effort`` im Manifest → ``claude --effort <stufe>`` (David 2026-10-11).

Ohne Feld bleibt der claude-Befehl wie bisher (kein ``--effort``); ein ungültiger
Wert lässt ``pruefen`` verweigern. ``bau.py`` läuft echt als Prozess (``--dry-run``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

import context_mode_attrappe  # noqa: E402  (tests/hilfen, Pfad setzt conftest)
from to_spawn import manifest  # noqa: E402


def _repo(tmp_path: Path, effort: str | None) -> Path:
    arbeit = tmp_path / "repo"
    manifeste = arbeit / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(arbeit), check=True, capture_output=True)
    daten: dict = {
        "spec": 900,
        "tickets": {"901": {"title": "Wegwerf", "schaetzung_k": 120, "umfang": "Kern bauen.", "mcp": []}},
    }
    if effort is not None:
        daten["effort"] = effort
    (manifeste / "spec-900.json").write_text(json.dumps(daten), encoding="utf-8")
    shutil.copy2(SKILL / "repo-scripts" / "_default.json", manifeste / "_default.json")
    return arbeit


def _befehl(tmp_path: Path, effort: str | None) -> str:
    repo = _repo(tmp_path, effort)
    home = tmp_path / "home"
    (home / "tmp").mkdir(parents=True)
    context_mode_attrappe.plugin_anlegen(home, "1.0.169", True)
    env = {k: v for k, v in os.environ.items() if k != "TO_SPAWN_REPO"}
    env.update(HOME=str(home), USERPROFILE=str(home), TMPDIR=str(home / "tmp"))
    ergebnis = subprocess.run(
        [sys.executable, str(SKILL / "skripte" / "bau.py"), "901", "--dry-run"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    return ausgabe.split("Befehl:", 1)[1]


def test_mit_feld_steht_effort_im_befehl(tmp_path: Path) -> None:
    assert "--effort high" in _befehl(tmp_path, "high")


def test_ohne_feld_kein_effort_im_befehl(tmp_path: Path) -> None:
    assert "--effort" not in _befehl(tmp_path, None)


@pytest.mark.parametrize(("wert", "sauber"), [("high", True), ("max", True), ("turbo", False), ("", False)])
def test_pruefen_kennt_nur_gueltige_stufen(tmp_path: Path, wert: str, sauber: bool) -> None:
    bericht = manifest.pruefe(_repo(tmp_path, wert), 900, {}, mit_github=False)
    assert bericht.sauber is sauber, bericht.text()
    if not sauber:
        assert "effort" in bericht.text()
