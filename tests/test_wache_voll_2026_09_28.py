"""Wache als voll fähige Session (David 28.09.2026): Effort, [1m], Prompt, Neustart, Ablösung."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parent.parent
WACHE = SKILL / "skripte" / "wache.py"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import config, spawn, waechter_lauf  # noqa: E402


@pytest.fixture(scope="module")
def wache() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wache_voll", WACHE)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_effort_default_medium() -> None:
    assert config.DEFAULTS["effort"]["waechter"] == "medium"


def test_befehl_mit_effort_und_resume_gleich() -> None:
    start = waechter_lauf.befehl("claude", "claude-opus-5-5[1m]", "claude-sonnet-5", True, 7, "P", effort="medium")
    assert start[:5] == ["claude", "--model", "claude-opus-5-5[1m]", "--effort", "medium"]
    weiter = waechter_lauf.resume_befehl("claude", "id", "m", "medium", False, 7, "W")
    assert weiter == ["claude", "--resume", "id", "--model", "m", "--effort", "medium", "W"]
    assert "--effort" not in waechter_lauf.befehl("claude", "m", "", False, 7, "P")


def test_volles_fenster(wache: ModuleType) -> None:
    assert wache.volles_fenster("claude-opus-5-5") == "claude-opus-5-5[1m]"
    assert wache.volles_fenster("claude-opus-5-5[1m]") == "claude-opus-5-5[1m]"
    assert wache.volles_fenster("claude-sonnet-5") == "claude-sonnet-5"


def test_prompt_ohne_fesseln_mit_befehlen(wache: ModuleType) -> None:
    text = wache.prompt_bauen(900, "x/y", 1800, {"ssh_ziel": "bau-server", "server_repo": "/home/bau/r"})
    for verboten in ("low", "caveman", "baue NICHTS", "nichts lesen"):
        assert verboten not in text
    assert "to_spawn.py neustart 900 <N> --ziel srv" in text
    assert "--beenden" in text and "--handoff docs/handoffs/HANDOFF_<datum>_<N>.md" in text
    assert "wache.py 900 --abloesen docs/HANDOFF_" in text
    assert "ssh bau-server 'cd /home/bau/r && python3 scripts/sessions_stand.py 900 --alle'" in text
    assert "pstree -p <PID>" in text


def test_dry_run_zeigt_effort_und_1m(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    umgebung = {**os.environ, "TO_SPAWN_REPO": str(tmp_path), "TO_SPAWN_GH_REPO": "x/y"}
    lauf = subprocess.run(
        [sys.executable, str(WACHE), "900", "--dry-run"],
        cwd=str(tmp_path),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if lauf.returncode == 2 and "context-mode" in lauf.stderr:
        pytest.skip("context-mode fehlt auf diesem Rechner")
    assert lauf.returncode == 0, lauf.stderr
    assert "--effort medium" in lauf.stdout
    assert "claude-opus-5-5[1m]" in lauf.stdout


def test_abloesen_schreibt_datei(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "h.md").write_text("Stand", encoding="utf-8")
    ziel = tmp_path / "abloesung.json"
    umgebung = {**os.environ, "TO_SPAWN_REPO": str(tmp_path), "TO_SPAWN_GH_REPO": "x/y"}
    ohne = subprocess.run(
        [sys.executable, str(WACHE), "900", "--abloesen", "h.md"],
        env={k: v for k, v in umgebung.items() if k != "TO_SPAWN_WACHE_ABLOESUNG"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert ohne.returncode == 2
    mit = subprocess.run(
        [sys.executable, str(WACHE), "900", "--abloesen", "h.md"],
        env={**umgebung, "TO_SPAWN_WACHE_ABLOESUNG": str(ziel)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert mit.returncode == 0, mit.stderr
    assert Path(json.loads(ziel.read_text(encoding="utf-8"))["handoff"]).name == "h.md"


def test_abloese_prompt_traegt_handoff(wache: ModuleType, tmp_path: Path) -> None:
    handoff = tmp_path / "h.md"
    handoff.write_text("STAND-XYZ", encoding="utf-8")
    text = wache.abloese_prompt("AUFTRAG", handoff, 2)
    assert text.index("STAND-XYZ") < text.index("AUFTRAG")


def test_fern_ordner_tilde_bleibt_aufgeloest() -> None:
    assert spawn.fern_ordner("~/duoplus-management") == "~/duoplus-management"
    assert spawn.fern_ordner("/home/bau/a b") == "'/home/bau/a b'"


def test_neustart_srv_ohne_server_repo_exit_2(tmp_path: Path) -> None:
    assert spawn.neustart(tmp_path, 900, 901, {"ssh_ziel": "x"}, ziel="srv", dry_run=True) == 2


def test_neustart_auftrag_nennt_handoff() -> None:
    assert spawn.neustart_auftrag("") == ""
    assert "docs/handoffs/H_901.md" in spawn.neustart_auftrag("docs/handoffs/H_901.md")
