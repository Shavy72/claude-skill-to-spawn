"""Gemeinsame Test-Einstellungen.

Die Wächter-Tests aus dem ersten Bau von #213 (``test_waechter_213.py`` und der
Weg-Test) prüfen die Regeln selbst: Ticket schließen, sofort ein Tick, Ergebnis.
Seit der Fixrunde wartet capo 15 min nach dem Schließen (Karenz) und nimmt beim
ersten Tick alle geschlossenen Tickets als Ausgangsstand. Diese beiden
Zeitgrenzen schaltet ``TO_SPAWN_WAECHTER_SOFORT=1`` ab — nur für diese beiden
Dateien. Die Zeitgrenzen selbst prüft ``test_waechter_213_fix.py`` ohne Schalter.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Test-Helfer (``tests/hilfen``) einmal importierbar machen — z. B. ``context_mode_attrappe`` (#237).
_HILFEN = str(Path(__file__).resolve().parent / "hilfen")
if _HILFEN not in sys.path:
    sys.path.insert(0, _HILFEN)

_SOFORT_MODULE = frozenset({"test_waechter_213", "test_waechter_213_weg"})


@pytest.fixture(autouse=True)
def _waechter_sofort(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if request.module.__name__ in _SOFORT_MODULE:
        monkeypatch.setenv("TO_SPAWN_WAECHTER_SOFORT", "1")
    else:
        monkeypatch.delenv("TO_SPAWN_WAECHTER_SOFORT", raising=False)
