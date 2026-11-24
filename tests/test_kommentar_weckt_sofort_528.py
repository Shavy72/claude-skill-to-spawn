"""#528: Neuer Mensch-Kommentar am Abnahme-Issue weckt Aufseher-Pane und Bau-Fenster sofort.

Echt sind Manifest, Wecker, tmux-Tür (Attrappe ``tmux`` wie in #414). Gestellt ist nur GitHub
(``gh.json_lauf``): ein Dict je Issue (Labels) bzw. die Kommentar-Seiten, so wie ``gh api`` sie liefert.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_briefkasten_wecker_414 import PANE, SPEC, TICKET, _getippt, welt  # noqa: E402,F401
from to_spawn import briefkasten_wecker, gh

CHECKPOINT = {"labels": [{"name": "checkpoint:human"}]}
OHNE = {"labels": [{"name": "bug"}]}


def _komm(i: int, text: str, rolle: str = "OWNER") -> dict[str, Any]:
    return {"id": i, "body": text, "author_association": rolle}


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Zustand von GitHub: ``issue`` (Labels) und ``kommentare`` (Liste), beides frei änderbar."""
    z: dict[str, Any] = {"issue": CHECKPOINT, "kommentare": [_komm(1, "Teilabnahme bereit")]}

    def json_lauf(args: list[str], *_a: Any, **_k: Any) -> Any:
        if args[1].endswith(f"/issues/{TICKET}"):
            return z["issue"]
        if "/comments" in args[1]:
            return [z["kommentare"]]
        return None

    monkeypatch.setattr(gh, "json_lauf", json_lauf)
    return z


def _wecker(welt: dict[str, Path]) -> Any:
    return briefkasten_wecker.Wecker(SPEC, welt["haupt"], PANE, gh_repo="o/r")


def test_mensch_kommentar_weckt_beide_genau_einmal(welt: dict[str, Path], github: dict[str, Any]) -> None:
    w = _wecker(welt)
    w.einmal()
    assert _getippt(welt) == [], "Bestand beim Start weckt nicht"
    github["kommentare"].append(_komm(2, "Der Button fehlt noch."))
    w.einmal()
    w.einmal()
    ziele = sorted(g["ziel"] for g in _getippt(welt))
    assert ziele == sorted([PANE, f"=spec-{SPEC}:@2"]), ziele
    assert all(f"#{TICKET}" in g["text"] for g in _getippt(welt))


def test_eigene_koepfe_wecken_nicht_zitat_schon(welt: dict[str, Path], github: dict[str, Any]) -> None:
    w = _wecker(welt)
    w.einmal()
    github["kommentare"] += [_komm(2, "Aufseher: Teilabnahme bereit"), _komm(3, "Bau-Session: erledigt")]
    w.einmal()
    assert _getippt(welt) == []
    github["kommentare"].append(_komm(4, "> Aufseher: Teilabnahme bereit\n\npasst"))
    w.einmal()
    assert len(_getippt(welt)) == 2


def test_ticket_ohne_checkpoint_label_weckt_nicht(welt: dict[str, Path], github: dict[str, Any]) -> None:
    github["issue"] = OHNE
    w = _wecker(welt)
    w.einmal()
    github["kommentare"].append(_komm(2, "passt"))
    w.einmal()
    assert _getippt(welt) == []
