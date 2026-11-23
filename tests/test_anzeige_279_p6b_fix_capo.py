"""#279 P6b Fix-Runde: capo erkennt „verworfen“ auch bei ``stateReason`` (camelCase) und Leerzeichen.

Eigene Datei, weil die capo-Welt (``test_waechter_213.welt``) und die Leiter-Welt denselben
Fixture-Namen tragen.
"""

# ruff: noqa: F811 — Fixture ``welt`` kommt aus dem Nachbar-Test und wird als Parameter genannt.
from __future__ import annotations

from pathlib import Path

import pytest
from test_waechter_213 import _capo, _gh_setzen, _iso, _text, welt  # noqa: F401


@pytest.mark.parametrize(
    "feld", [{"stateReason": "NOT_PLANNED"}, {"stateReason": " not_planned "}, {"state_reason": " NOT_PLANNED\n"}]
)
def test_capo_verworfen_camelcase_und_leerzeichen(welt: dict[str, Path], feld: dict[str, str]) -> None:
    _gh_setzen(welt, "901", state="open", closed_at=None)
    assert _capo(welt).returncode == 0  # Ausgangsstand
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(), **feld)
    ergebnis = _capo(welt)
    assert "#901 zu als not_planned — keine Regeln" in ergebnis.stdout, _text(ergebnis)
