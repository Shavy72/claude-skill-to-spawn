"""Unit-Tests für präzisen Logtext in respawn.fenster_schliessen (#886)."""

from __future__ import annotations

import logging

import pytest

from to_spawn import respawn
from to_spawn.tmux_aufruf import FensterWeg


class _FensterWegWerkzeug(respawn.TmuxWerkzeug):
    def _tmux(self, *argumente: str, eingabe: str | None = None) -> str:
        raise FensterWeg("kill-window", "can't find window: @2165")


def test_fenster_schliessen_loggt_nicht_selbst_beendet(caplog: pytest.LogCaptureFixture) -> None:
    werkzeug = _FensterWegWerkzeug()
    with caplog.at_level(logging.INFO, logger="to_spawn.respawn"):
        werkzeug.fenster_schliessen("=spec-886:@2165")
    meldungen = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert meldungen
    assert all("Session hat sich selbst beendet" not in m for m in meldungen)
    assert any("bereits geschlossen" in m or "schon zu" in m for m in meldungen)
