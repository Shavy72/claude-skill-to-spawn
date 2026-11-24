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


# --- Fix-Runde 1: Fehler je Fenster, STUPSER_MAX, Merker ---------------------------

WARTET = "Ich warte auf die Folgesession für den Klickweg."


def test_fehler_bei_einem_fenster_die_anderen_werden_geprueft(
    welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    zweites = TICKET + 1
    (welt["tmux"] / "fenster.txt").write_text(
        f"@1\twache {SPEC}\t0\n@2\tbau {TICKET}\t0\n@3\tbau {zweites}\t0\n", encoding="utf-8"
    )

    def aktuell(repo: Path, ticket: int) -> None:
        if ticket == TICKET:
            raise OSError("git kaputt")

    monkeypatch.setattr(eigener_handoff, "aktuell", aktuell)
    _bild(welt, _sagt(WARTET))
    _wecker(welt).einmal()
    assert [g["ziel"] for g in _getippt(welt)] == [f"=spec-{SPEC}:@3"]


def test_hoechstens_stupser_max_anstoesse_je_ticket(welt: dict[str, Any]) -> None:
    w = _wecker(welt)
    for _ in range(aufpasser.STUPSER_MAX + 3):
        _bild(welt, _sagt(WARTET))
        w.einmal()
        _bild(welt, _sagt("Klickweg läuft, Beleg wird geschrieben."))
        w.einmal()
    assert len(_getippt(welt)) == aufpasser.STUPSER_MAX


def test_merker_faellt_wenn_fenster_nicht_mehr_wartet(welt: dict[str, Any]) -> None:
    w = _wecker(welt)
    _bild(welt, _sagt(WARTET))
    w.einmal()
    w.einmal()
    assert len(_getippt(welt)) == 1
    _bild(welt, _sagt("Klickweg läuft, Beleg wird geschrieben."))
    w.einmal()
    _bild(welt, _sagt(WARTET))
    w.einmal()  # neues Stehen: wieder ein Anstoß
    assert len(_getippt(welt)) == 2
