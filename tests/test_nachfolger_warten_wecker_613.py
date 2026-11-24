"""#613: Warten auf einen Nachfolger, der nie kommt, endet nach höchstens einem Wecker-Takt.

Die Bau-Session sagt am Prompt „warte auf die Folgesession“, hat aber keinen eigenen Handoff
(Rest aus #381). Bisher stieß der Aufpasser erst nach der frühen Stille-Schwelle (5 min) plus
Takt an; jetzt der Briefkasten-Wecker (60 s) — dieselbe Regel (:func:`aufpasser.wartet_umsonst`)
und derselbe Anstupser-Text, je Stehen genau ein Anstoß. Kein GitHub nötig.

Gestellt ist nur ``tmux`` (Attrappe aus ``test_briefkasten_wecker_414``); Wecker, Regel und
Handoff-Abfrage sind echt (außer im Fall „echter Handoff“, dort liefert die Abfrage einen Treffer).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
import test_briefkasten_wecker_414 as basis
from test_briefkasten_wecker_414 import LINIE, PANE, SPEC, TICKET, _bild, _getippt

from to_spawn import aufpasser, briefkasten_wecker, eigener_handoff

welt = basis.welt  # Fixture aus dem Grund-Test (Hauptbaum + gestelltes tmux)
BAU = f"=spec-{SPEC}:@2"
ANSTOSS = aufpasser.ANSTUPS_TEXT.split("{min}")[0]  # „Aufpasser: Du stehst seit “


def _sagt(satz: str) -> str:
    """Claude-Pane: letzte Ausgabe der Session, dann leeres Eingabefeld."""
    return f"● {satz}\n\n{LINIE}\n❯ \n{LINIE}\n  ⏵⏵ auto mode\n"


def _wecker(welt: dict[str, Any]) -> briefkasten_wecker.Wecker:
    return briefkasten_wecker.Wecker(SPEC, welt["haupt"], PANE)


def test_wartet_auf_folgesession_anstoss_im_naechsten_takt_genau_einmal(
    welt: dict[str, Any],
) -> None:
    _bild(
        welt,
        _sagt("Code ist auf dem Hauptzweig. Ich warte auf die Folgesession für den Klickweg."),
    )
    w = _wecker(welt)
    w.einmal()  # ein Wecker-Takt, keine 5 min Stille nötig
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [BAU], getippt
    assert getippt[0]["text"].startswith(ANSTOSS), getippt[0]["text"]
    w.einmal()  # unverändertes Bild: kein zweiter Anstoß
    assert len(_getippt(welt)) == 1


@pytest.mark.parametrize(
    "satz",
    [
        "Handoff committet. Ich warte auf die Ablösung durch den Launcher.",
        "Ich warte auf die Übergabe des Menschen.",
        "Ich warte auf die andere Session (Review).",
    ],
)
def test_zulaessiges_warten_kein_anstoss(welt: dict[str, Any], satz: str) -> None:
    _bild(welt, _sagt(satz))
    _wecker(welt).einmal()
    assert _getippt(welt) == []


def test_echter_eigener_handoff_kein_anstoss(welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    gesehen: list[int] = []

    def aktuell(repo: Path, ticket: int) -> eigener_handoff.Treffer:
        gesehen.append(ticket)
        return eigener_handoff.Treffer(f"docs/handoffs/HANDOFF_2026-11-24_{ticket}.md", time.time())

    monkeypatch.setattr(eigener_handoff, "aktuell", aktuell)
    _bild(welt, _sagt("Ablösung ausstehend, warte auf die Folgesession."))
    _wecker(welt).einmal()
    assert gesehen == [TICKET]
    assert _getippt(welt) == []
