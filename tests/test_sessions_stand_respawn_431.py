"""#431: ``respawn`` startet die neue Session über ``bau <N> --sofort --ohne-prompt``.

Die neue Session läuft damit unter ``bau.py`` wie jede Bau-Session — ``sessions_stand``
braucht keinen Sonderweg über ``BAU_TICKET`` mehr und zählt sie genau einmal.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

SKRIPT = Path(__file__).resolve().parent.parent / "skripte" / "sessions_stand.py"


@pytest.fixture
def modul() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("sessions_stand_431", SKRIPT)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    try:
        spec.loader.exec_module(m)
        m.fremdes_repo = lambda pid: False
        yield m
    finally:
        sys.modules.pop(spec.name, None)


def test_respawn_session_unter_bau_py_wird_genau_einmal_gezaehlt(
    modul: ModuleType,
) -> None:
    P = modul.Prozess
    alle = [
        P(9, 1, "bash", "bash -lc REPO=/r bau 431 --sofort --ohne-prompt", None),
        P(
            10,
            9,
            "python3",
            "python3 /home/bau/.claude/skills/to-spawn/skripte/bau.py 431 --sofort --ohne-prompt",
            None,
        ),
        P(
            11,
            10,
            "claude",
            "claude --settings /r/.to-spawn/x-bau/431-20261004-000000/settings.json",
            None,
        ),
        P(12, 11, "node", "node /usr/local/bin/claude-mcp.js", None),
        P(13, 11, "bash", "bash -c git status", None),
    ]
    eintraege = {"431": modul.Eintrag("431", "ticket", "t")}
    modul.zuordnen(eintraege, alle)
    assert list(eintraege) == ["431"]
    e = eintraege["431"]
    assert e.pid == 10 and e.session_pid == 11
    assert e.zustand.startswith("läuft")
    assert "VERWAIST" not in e.zustand
    assert not hasattr(modul, "_respawn_sessions_zuordnen")
