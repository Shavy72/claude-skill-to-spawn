"""Zwei Plattform-/Import-Fallen hinter #542 (Wurzel-Fixes, Weg über echte Module).

1. ``config.worktree_pfad`` ignorierte ``BAU_WT_DIR`` auf Windows (fest ``C:/dev``) — der
   Aufpasser fand den Ticket-Worktree dort nie, Neustart ab Handoff blieb aus.
2. ``waechter_lauf._StoppWerkzeug`` erbte beim Import von ``respawn.TmuxWerkzeug``; war
   die Naht da ersetzt (CLI-Tests), brach der Import mit ``TypeError``.
"""

from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import config, respawn


@pytest.mark.parametrize("plattform", ["win32", "linux"])
def test_bau_wt_dir_gilt_auf_jeder_plattform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plattform: str
) -> None:
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path / "wts"))
    monkeypatch.setattr(sys, "platform", plattform)
    assert config.worktree_pfad(7, tmp_path) == f"{(tmp_path / 'wts').as_posix()}/wt-7"


def test_ohne_bau_wt_dir_windows_vorgabe_c_dev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.delenv("BAU_WT_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert config.worktree_pfad(7, tmp_path) == "C:/dev/wt-7"


def test_waechter_lauf_import_trotz_ersetzter_tmux_naht(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frischer Import bei ersetztem ``respawn.TmuxWerkzeug`` bricht nicht; die Hülle nutzt die Naht."""
    getippt: list[tuple[str, str]] = []

    class Gestellt:
        def tippen(self, ziel: str, text: str) -> None:
            getippt.append((ziel, text))

        def bildschirm(self, ziel: str) -> str:
            return "leer"

        def schlafen(self, s: float) -> None:
            raise AssertionError("inneres schlafen muss ersetzt sein")

    monkeypatch.setattr(respawn, "TmuxWerkzeug", lambda: Gestellt())
    import to_spawn
    from to_spawn import waechter_lauf as vorher

    # Paket-Attribut + sys.modules nach dem Test zurück: andere Tests sehen das alte Modul.
    monkeypatch.setattr(to_spawn, "waechter_lauf", vorher)
    monkeypatch.delitem(sys.modules, "to_spawn.waechter_lauf")
    waechter_lauf = importlib.import_module("to_spawn.waechter_lauf")
    stopp = threading.Event()
    w = waechter_lauf._StoppWerkzeug(stopp)
    w.tippen("%1", "Auftrag")
    assert getippt == [("%1", "Auftrag")]
    assert w.bildschirm("%1") == "leer"  # übrige Aufrufe reicht die Hülle durch
    w._innen.schlafen(0)  # inneres schlafen = stopp-bewusstes der Hülle
    stopp.set()
    with pytest.raises(respawn._Abbruch):
        w.tippen("%1", "zu spät")
