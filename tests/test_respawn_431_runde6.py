"""#431 Echtlauf 8: Fenster, das sich schon selbst geschlossen hat, ist kein Fehler.

Die alte Session beendet sich nach dem Handoff oft selbst. Dann meldet tmux beim
Schließen „can't find window“ — das ist das gewünschte Ziel, keine Warnung.
"""

from __future__ import annotations

import logging

import pytest

from to_spawn import respawn
from to_spawn.tmux_aufruf import FensterWeg, TmuxFehler


class _Werkzeug(respawn.TmuxWerkzeug):
    def __init__(self, fehler: Exception) -> None:
        self.fehler = fehler

    def _tmux(self, *argumente: str, eingabe: str | None = None) -> str:
        raise self.fehler


def test_schon_geschlossenes_fenster_ohne_warnung(caplog: pytest.LogCaptureFixture) -> None:
    werkzeug = _Werkzeug(FensterWeg("kill-window", "can't find window: @2165"))
    with caplog.at_level(logging.INFO, logger="to_spawn.respawn"):
        werkzeug.fenster_schliessen("=spec-9399:@2165")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("schon zu" in r.getMessage() for r in caplog.records)


def test_echter_schliess_fehler_bleibt_warnung(caplog: pytest.LogCaptureFixture) -> None:
    werkzeug = _Werkzeug(TmuxFehler("kill-window", "server exited unexpectedly"))
    with caplog.at_level(logging.INFO, logger="to_spawn.respawn"):
        werkzeug.fenster_schliessen("=spec-9399:@2165")
    assert any(r.levelno == logging.WARNING for r in caplog.records)
