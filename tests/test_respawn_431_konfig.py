"""#431 Prüfbefund 1: die neue Session bekommt dieselbe Bau-Konfiguration wie jede Bau-Session.

Vorher startete respawn ein nacktes ``claude --model … --effort …`` — ohne Session-Settings
(AskUserQuestion-Sperre #321, Stop-Hooks), ohne strikte MCP-Config, ohne Gesprächs-ID (#236)
und ohne Staffel-/Bau-Log-Umgebung. Jetzt geht respawn denselben Weg wie spawn/capo:
``bau <N> --sofort`` im tmux-Fenster, nur ohne ersten Prompt (``--ohne-prompt``), weil der
Start-Prompt erst nach dem Handoff der alten Session ins neue Fenster getippt wird.

Test 1 prüft den Startbefehl von respawn gegen den von capo (ein Wissensort).
Test 2 führt bau.py mit genau den Schaltern aus diesem Startbefehl aus (Fake-``claude``
zeichnet argv + Umgebung auf) und belegt Settings, MCP, Session-ID und Umgebung.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SKILL = TESTS.parent
SKRIPTE = SKILL / "skripte"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(TESTS))

from test_frage_sperre_321 import _lade_bau
from test_respawn_431 import TICKET, FakeWerkzeug, _lauf
from test_respawn_431 import (
    umgebung as umgebung,  # noqa: PLC0414 — Fixture der Nachbar-Tests
)
from test_umzug_212 import TICKET as BAU_TICKET
from test_umzug_212 import _ausfuehrbar
from test_umzug_212 import welt as welt  # noqa: PLC0414 — Fixture der Nachbar-Tests

from to_spawn import capo

FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
ausgabe = Path(os.environ["FAKE_AUSGABE"])
ausgabe.mkdir(parents=True, exist_ok=True)
(ausgabe / "argv.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
(ausgabe / "umgebung.json").write_text(json.dumps(dict(os.environ)), encoding="utf-8")
sys.exit(0)
"""


def _respawn_befehl(umgebung: tuple[Path, Path]) -> tuple[str, str]:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    erg = _lauf(repo, fake)
    assert erg.exit == 0, erg.zeile
    start = next(a for a in fake.aufrufe if a[0] == "fenster_starten")
    return start[3], start[4]


def _bau_schalter(befehl: str, ticket: int) -> list[str]:
    """Schalter hinter ``bau <N>`` aus dem tmux-Befehl ``bash -lc '…; bau <N> …'``."""
    teile = shlex.split(befehl)
    assert teile[:2] == ["bash", "-lc"], befehl
    letzte = shlex.split(teile[2].rsplit("; ", 1)[-1])
    assert letzte[:2] == ["bau", str(ticket)], befehl
    return letzte[2:]


def test_respawn_startet_ueber_bau_wie_capo(umgebung: tuple[Path, Path]) -> None:
    repo, _wt = umgebung
    cwd, befehl = _respawn_befehl(umgebung)
    # Kein eigener claude-Aufruf mehr: die Konfiguration kommt allein aus bau.py.
    assert "claude" not in shlex.split(befehl)[:2]
    assert " claude " not in befehl and "--model" not in befehl
    # Derselbe Vorspann wie die Folge-Runde von capo (REPO/TO_SPAWN_HOME), nur ohne Prompt.
    capo_innen = capo.folge_befehl(repo, 399, TICKET, ["wache 399"])[-1]
    assert shlex.split(befehl)[2] == f"{capo_innen} --ohne-prompt"
    assert _bau_schalter(befehl, TICKET) == ["--sofort", "--ohne-prompt"]
    assert cwd == str(repo)


def test_bau_mit_respawn_schaltern_gibt_volle_konfiguration(
    umgebung: tuple[Path, Path], welt: dict[str, Path]
) -> None:
    _cwd, befehl = _respawn_befehl(umgebung)
    schalter = _bau_schalter(befehl, TICKET)

    tmp = welt["tmp"]
    binaer = tmp / "bin-claude"
    binaer.mkdir(exist_ok=True)
    _ausfuehrbar(binaer / "claude", FAKE_CLAUDE)
    temp = tmp / "tmp"
    temp.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}",
        "TO_SPAWN_REPO": str(welt["haupt"]),
        "FAKE_AUSGABE": str(tmp / "fake"),
        "TMPDIR": str(temp),
        "BAU_WT_DIR": str(tmp / "wt"),
    }
    for weg in ("LOCALAPPDATA", "BAU_UMZUG_DATEI", "BAU_UMZUG_ANFRAGE", "BAU_AUFTRAG"):
        env.pop(weg, None)
    lauf = subprocess.run(
        [sys.executable, str(SKRIPTE / "bau.py"), BAU_TICKET, *schalter],
        cwd=str(welt["haupt"]),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    argv = json.loads((tmp / "fake" / "argv.json").read_text(encoding="utf-8"))
    umg = json.loads((tmp / "fake" / "umgebung.json").read_text(encoding="utf-8"))

    # Settings mit Frage-Sperre (#321) und Stop-Hooks.
    settings = json.loads(
        Path(argv[argv.index("--settings") + 1]).read_text(encoding="utf-8")
    )
    assert "AskUserQuestion" in settings["permissions"]["deny"]
    assert settings["hooks"]["Stop"]
    assert settings["skillOverrides"]
    # MCP strikt, Gesprächs-ID (#236).
    assert "--mcp-config" in argv and "--strict-mcp-config" in argv
    sid = argv[argv.index("--session-id") + 1]
    assert len(sid) == 36
    # Kein erster Prompt: das letzte Argument ist die Session-ID, kein Auftragstext.
    assert argv[-1] == sid
    # Umgebung aus staffel_umgebung + bau_log_umgebung.
    assert umg["BAU_HANDOFF_DIRS"]
    assert umg["BAU_TICKET"] == BAU_TICKET and umg["TO_SPAWN_TICKET"] == BAU_TICKET
    assert umg["TO_SPAWN_STAFFEL"] == "1" and umg["TO_SPAWN_START"]
    assert umg["BAU_SESSION_ID"] == sid


def test_ohne_prompt_staffel_runde_haengt_prompt_an_statt_id_zu_ueberschreiben(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Folge-Runde nach ``--ohne-prompt``: Prompt wird angehängt, die Session-ID bleibt Schalterwert."""
    bau = _lade_bau(monkeypatch, tmp_path)
    cmd = ["claude", "--settings", "s.json", "--session-id", "alt-id"]
    bau.prompt_setzen(cmd, "Staffel-Text", mit_prompt=False)
    assert cmd == [
        "claude",
        "--settings",
        "s.json",
        "--session-id",
        "alt-id",
        "Staffel-Text",
    ]
    bau.prompt_setzen(cmd, "Runde 3", mit_prompt=True)
    assert cmd[-1] == "Runde 3" and cmd[-2] == "alt-id"


def test_bau_hilfe_kennt_ohne_prompt() -> None:
    hilfe = subprocess.run(
        [sys.executable, str(SKRIPTE / "bau.py"), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    ).stdout
    assert "--ohne-prompt" in hilfe
