"""Hook-Befehle aus ``bau.staffel_hooks()`` müssen in Git Bash laufen.

Claude Code startet Hooks unter Windows über ``/usr/bin/bash``. ``subprocess.list2cmdline``
liefert ``C:\\Python313\\python.exe …`` – Bash schluckt die Backslashes, daraus wird
``C:Python313python.exe: command not found`` (Exit 127) bei jedem Zugende.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
from git_bash import git_bash

SKRIPTE = Path(__file__).resolve().parent.parent / "skripte"
BASH = git_bash()


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path) -> types.ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location("bau_hook_bash", SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules["bau_hook_bash"] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop("bau_hook_bash", None)
    return modul


def _python_befehle(hooks: dict) -> list[str]:
    befehle = [h["command"] for block in hooks.values() for eintrag in block for h in eintrag["hooks"]]
    return [b for b in befehle if not b.startswith("node ")]


@pytest.mark.skipif(BASH is None, reason="bash fehlt")
def test_python_hooks_findet_bash_programm_und_skript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bau = _lade_bau(monkeypatch, tmp_path)
    befehle = _python_befehle(bau.staffel_hooks())
    assert len(befehle) == 3
    for befehl in befehle:
        # Echte Bash zerlegt den Befehl wie Claude Code; Programm und Skript müssen auffindbar sein.
        pruefung = f'set -- {befehl}; command -v "$1" >/dev/null && [ -f "$2" ]'
        ergebnis = subprocess.run([BASH, "-c", pruefung], capture_output=True, text=True)
        assert ergebnis.returncode == 0, f"Bash findet nicht: {befehl} ({ergebnis.stderr.strip()})"


@pytest.mark.skipif(BASH is None, reason="bash fehlt")
def test_hook_befehl_laeuft_mit_leerzeichen_im_pfad(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bau = _lade_bau(monkeypatch, tmp_path)
    skript = tmp_path / "mit leer" / "x.py"
    skript.parent.mkdir()
    marker = tmp_path / "marker.txt"
    skript.write_text(f"from pathlib import Path\nPath({marker.as_posix()!r}).write_text('ok')\n", encoding="utf-8")
    befehl = bau.hook_befehl(Path(sys.executable), skript)
    ergebnis = subprocess.run([BASH, "-c", befehl], capture_output=True, text=True)
    assert ergebnis.returncode == 0, f"{befehl}: {ergebnis.stderr.strip()}"
    assert marker.read_text() == "ok"


@pytest.mark.skipif(BASH is None, reason="bash fehlt")
def test_bau_log_stop_hook_laeuft_in_bash_ohne_ticket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bau = _lade_bau(monkeypatch, tmp_path)
    stop = [h["command"] for h in bau.staffel_hooks()["Stop"][0]["hooks"]]
    befehl = next(b for b in stop if b.endswith("hook-stop"))
    umgebung = {k: v for k, v in os.environ.items() if k != "TO_SPAWN_TICKET"}
    eingabe = {"session_id": "test", "transcript_path": str(tmp_path / "fehlt.jsonl"), "cwd": str(tmp_path)}
    ergebnis = subprocess.run(
        [BASH, "-c", befehl], input=json.dumps(eingabe), capture_output=True, text=True, cwd=tmp_path, env=umgebung
    )
    assert ergebnis.returncode == 0, f"{befehl}: {ergebnis.stderr.strip()}"
