"""Start-Tür #1406, Teil 2b: Fenster-Starts (tmux ``new-window``, wt-Tab) sperren unter Test.

Ohne Attrappe in ``TO_SPAWN_TEST_BIN`` endet jeder Fenster-Start mit ``StartGesperrt``,
bevor ein Prozess entsteht; mit Attrappe läuft genau sie.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser, pc_fenster, respawn, starter, tmux_aufruf  # noqa: E402


def test_tmux_fenster_ohne_attrappe_gesperrt(tmp_path: Path) -> None:
    with pytest.raises(starter.StartGesperrt):
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
    with pytest.raises(starter.StartGesperrt):
        a.fenster_starten("spec-1", "bau 1", tmp_path, "true")


def test_wt_tab_ohne_attrappe_gesperrt(tmp_path: Path) -> None:
    with pytest.raises(starter.StartGesperrt):
        pc_fenster.tab_starten(tmp_path, 1406, "")


def test_wt_tab_startet_attrappe(tmp_path: Path) -> None:
    """Liegt ``wt`` als Attrappe im Ordner, startet genau sie (Python-Shebang, Exit 0)."""
    wt = Path(os.environ[starter.TEST_BIN_ENV]) / "wt"
    wt.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8")
    wt.chmod(0o755)
    pc_fenster.tab_starten(tmp_path, 1406, "")
