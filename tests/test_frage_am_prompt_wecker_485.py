"""#485: Eine Ja/Nein-Frage am Prompt einer Bau-Session endet nach höchstens einem Wecker-Takt.

Bau-Sessions fragen nie (sie nehmen den Vorschlag). Bisher stieß erst der Aufpasser an — nach
der kurzen Stille-Schwelle (5 min) plus seinem Takt (15 min); Probe-Lauf 2 stand so 19 min.
Jetzt der Briefkasten-Wecker (60 s) mit derselben Regel (:func:`aufpasser.steht_umsonst`) und
demselben Anstupser-Text wie bei #613, je Stehen genau ein Anstoß.

Gestellt ist nur ``tmux`` (Attrappe aus ``test_briefkasten_wecker_414``); Wecker, Regel und
Briefkasten sind echt.
"""

from __future__ import annotations

from typing import Any

import pytest
import test_briefkasten_wecker_414 as basis
from test_briefkasten_wecker_414 import LINIE, PANE, SPEC, TICKET, _bild, _getippt

from to_spawn import aufpasser, briefkasten, briefkasten_wecker

welt = basis.welt  # Fixture aus dem Grund-Test (Hauptbaum + gestelltes tmux)
BAU = f"=spec-{SPEC}:@2"
ANSTOSS = aufpasser.ANSTUPS_TEXT.split("{min}")[0]  # „Aufpasser: Du stehst seit “
FRAGE = "Nicht gestartet habe ich das große Cloud-Review (kostet). Vorschlag: /code-review ultra starten? Ja/Nein."


def _sagt(satz: str) -> str:
    """Claude-Pane: letzte Ausgabe der Session, dann leeres Eingabefeld."""
    return f"● {satz}\n\n{LINIE}\n❯ \n{LINIE}\n  ⏵⏵ auto mode\n"


def _wecker(welt: dict[str, Any]) -> briefkasten_wecker.Wecker:
    return briefkasten_wecker.Wecker(SPEC, welt["haupt"], PANE)


@pytest.mark.parametrize("satz", [FRAGE, "Fertig gebaut. Soll ich den Pull-Request jetzt mergen?"])
def test_frage_am_prompt_anstoss_im_naechsten_takt_genau_einmal(welt: dict[str, Any], satz: str) -> None:
    _bild(welt, _sagt(satz))
    w = _wecker(welt)
    w.einmal()  # ein Wecker-Takt, keine 5 min Stille nötig
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [BAU], getippt
    assert getippt[0]["text"].startswith(ANSTOSS), getippt[0]["text"]
    w.einmal()  # unverändertes Bild: kein zweiter Anstoß
    assert len(_getippt(welt)) == 1


def test_live_ja_aufforderung_kein_anstoss(welt: dict[str, Any]) -> None:
    _bild(welt, _sagt(f"Live braucht dein Ja: `{briefkasten.JA_BEFEHL} {TICKET}` — Ja/Nein?"))
    _wecker(welt).einmal()
    assert _getippt(welt) == []


def test_offene_frage_im_briefkasten_kein_anstoss(welt: dict[str, Any]) -> None:
    briefkasten.einwerfen(TICKET, "Zweig A oder B?", "A", None, ["Lastenheft"], hauptbaum=welt["haupt"])
    _bild(welt, _sagt("Frage im Briefkasten: Zweig A oder B? Ja/Nein."))
    _wecker(welt).einmal()
    assert [g for g in _getippt(welt) if g["ziel"] == BAU] == []


def test_aussage_ohne_frage_kein_anstoss(welt: dict[str, Any]) -> None:
    _bild(welt, _sagt("Ticket geschlossen, Fenster wird beendet."))
    _wecker(welt).einmal()
    assert _getippt(welt) == []
