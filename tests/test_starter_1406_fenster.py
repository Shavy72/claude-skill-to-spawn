"""Start-Tür #1406, Teil 2b: Fenster-Starts (tmux ``new-window``, wt-Tab) sperren unter Test.

Ohne Attrappe in ``TO_SPAWN_TEST_BIN`` endet jeder Fenster-Start mit ``StartGesperrt``,
bevor ein Prozess entsteht; mit Attrappe läuft genau sie.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser, pc_fenster, respawn, spawn, starter, tmux_aufruf  # noqa: E402


def test_tmux_fenster_ohne_attrappe_gesperrt(tmp_path: Path) -> None:
    with pytest.raises(starter.StartGesperrt, match="verboten: tmux "):
        respawn.TmuxWerkzeug().fenster_starten("spec-1", "bau 1", str(tmp_path), "true")


def test_tmux_lesen_bleibt_ohne_tuer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nur ``start=True`` geht durch die Tür; Lese-Aufrufe laufen wie bisher."""
    gesehen: list[list[str]] = []

    def run(befehl: list[str], **_kw: object) -> object:
        gesehen.append(befehl)
        return type("Fertig", (), {"stdout": "ok"})()

    monkeypatch.setattr(tmux_aufruf.subprocess, "run", run)
    assert tmux_aufruf.aufrufen("list-panes", "-a") == "ok"
    assert gesehen == [["tmux", "list-panes", "-a"]]


def test_aufpasser_fenster_ohne_attrappe_gesperrt(tmp_path: Path) -> None:
    a = aufpasser.Aufpasser(
        aufpasser.Einstellungen(zustand=tmp_path / "stand.json", tmux_socket="nie-benutzt", trocken=False)
    )
    with pytest.raises(starter.StartGesperrt, match="verboten: tmux "):
        a.fenster_starten("spec-1", "bau 1", tmp_path, "true")


def test_wt_tab_ohne_attrappe_gesperrt(tmp_path: Path) -> None:
    with pytest.raises(starter.StartGesperrt, match=r"(?i)verboten: [^→(]*\bwt(\.exe)? "):
        pc_fenster.tab_starten(tmp_path, 1406, "")


def test_wt_tab_startet_attrappe(tmp_path: Path) -> None:
    """Liegt ``wt`` als Attrappe im Ordner, startet genau sie (Python-Shebang, Exit 0)."""
    wt = Path(os.environ[starter.TEST_BIN_ENV]) / "wt"
    wt.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8")
    wt.chmod(0o755)
    pc_fenster.tab_starten(tmp_path, 1406, "")


# --- Fixrunde #1406 (Review 2b, Punkt 3): die drei übrigen Stellen ------------------------


def _nie_starten(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zusätzlicher Riegel: kommt ein Aufruf doch bis ``subprocess``, scheitert der Test laut."""

    def nie(*_a: object, **_k: object) -> None:
        raise AssertionError("subprocess trotz Sperre aufgerufen")

    monkeypatch.setattr(subprocess, "Popen", nie)
    monkeypatch.setattr(subprocess, "run", nie)


def _leitstand_fenster() -> ModuleType:
    spec = importlib.util.spec_from_file_location("leitstand_fenster_1406", SKILL / "skripte" / "leitstand_fenster.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul
    spec.loader.exec_module(modul)
    return modul


def test_leitstand_sitzung_ohne_attrappe_gesperrt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``claude`` liegt nur außerhalb des Attrappen-Ordners (wie die echte ``claude.EXE``)."""
    lf = _leitstand_fenster()
    aussen = tmp_path / "aussen"
    aussen.mkdir()
    for name in ("claude", "claude.cmd"):
        (aussen / name).write_text("", encoding="utf-8")
        (aussen / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(aussen))
    repo = tmp_path / "repo"
    repo.mkdir()
    _nie_starten(monkeypatch)
    with pytest.raises(starter.StartGesperrt, match=r"(?i)verboten: [^→(]*\bclaude(\.exe|\.cmd)? "):
        lf.sitzung(lf.Ablage(repo=repo, spec="1406"))


def test_spawn_ohne_attrappe_gesperrt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sauber = type("Bericht", (), {"sauber": True, "text": lambda self: "sauber"})()
    monkeypatch.setattr(spawn.manifest, "pruefe", lambda *_a, **_k: sauber)
    _nie_starten(monkeypatch)
    with pytest.raises(starter.StartGesperrt, match=r"(?i)verboten: [^→(]*\b(bash|pwsh|powershell)(\.exe)? "):
        spawn.spawn(tmp_path, 1406, {}, ziel="local")


def test_spawn_neustart_server_ohne_attrappe_gesperrt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spawn.stand, "sichere_stand", lambda *_a, **_k: 0)
    _nie_starten(monkeypatch)
    with pytest.raises(starter.StartGesperrt, match="verboten: ssh "):
        spawn.neustart(tmp_path, 1406, 1, {"server_repo": "~/repo"}, ziel="srv")


def test_spawn_neustart_pc_tab_ohne_attrappe_gesperrt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PC-Zweig (wt-Tab); ``sys.platform`` gestellt, damit er auch unter Linux läuft."""
    monkeypatch.setattr(spawn.sys, "platform", "win32")
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))  # neustart setzt es selbst — so räumt monkeypatch auf
    aus = type("Eintrag", (), {"zustand": "aus", "pid": None, "session_pid": None})()
    monkeypatch.setattr(spawn, "ticket_eintrag", lambda *_a: aus)
    _nie_starten(monkeypatch)
    with pytest.raises(starter.StartGesperrt, match=r"(?i)verboten: [^→(]*\bwt(\.exe)? "):
        spawn.neustart(tmp_path, 1406, 1, {}, ziel="local")
