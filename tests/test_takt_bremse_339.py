"""#339: weiche Reste der Takt-Bremse (#315) — Fehler-Lauf, doppelte Meldung, Blättern, Codes, kaputter Merker.

GitHub und Claude sind externe Dienste: Aufzeichnungs-Ersatz aus ``tests/hilfen`` wie in #315/#316;
nur das Blättern der Kommentar-Seiten (Rechenlogik) läuft über gestelltes ``gh.json_lauf``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tests"))

from test_takt_bremse_315 import _kommentare, _zeige_aufrufe
from test_waechter_takt_316 import CLAUDE_AUFZ, _aufrufe, _merker, _takt, welt  # noqa: F401


def _zustand_datei(welt: dict[str, Any]) -> Path:
    return Path(welt["env"]["TO_SPAWN_LEITSTAND_ORDNER"]) / "zustand.json"


def _aendere_merker(welt: dict[str, Any], aenderung: Any) -> None:
    datei = _zustand_datei(welt)
    zustand = json.loads(datei.read_text(encoding="utf-8"))
    aenderung(zustand["takt"]["293"])
    datei.write_text(json.dumps(zustand), encoding="utf-8")


@pytest.mark.parametrize("art", ["exit", "zeitgrenze"])
def test_gescheiterter_lauf_zaehlt_fuer_bremse(welt: dict[str, Any], monkeypatch: Any, art: str) -> None:
    """Punkt 1: auch ein Claude-Lauf mit Fehler/Zeitgrenze zählt als gezeigt — keine 12 Starts je Stunde."""
    from to_spawn import briefkasten

    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    if art == "exit":
        welt["env"]["CLAUDE_AUFZEICHNUNG_EXIT"] = "1"
    else:
        welt["env"]["CLAUDE_AUFZEICHNUNG_SCHLAF"] = "5"
        welt["env"]["TO_SPAWN_TAKT_TIMEOUT_S"] = "1"
    fid = briefkasten.einwerfen("297", "Ordner neu anlegen?", "neu", 3, [], hauptbaum=welt["repo"])
    for _ in range(5):
        lauf = _takt(welt, "--claude", str(CLAUDE_AUFZ))
        assert "Traceback" not in lauf.stderr, lauf.stderr
    assert _zeige_aufrufe(welt, fid) == 3, "gescheiterter Lauf muss die Bremse weiterzählen"
    assert len([k for k in _kommentare(welt, "297") if fid in k]) == 1


def test_gescheiterter_lauf_zeigt_alles_wieder(welt: dict[str, Any], monkeypatch: Any) -> None:
    """Punkt 1: nach dem Fehler-Lauf gilt nichts als erledigt — der nächste Takt zeigt dieselbe Liste."""
    from to_spawn import briefkasten

    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    briefkasten.einwerfen("297", "Ordner neu anlegen?", "neu", 3, [], hauptbaum=welt["repo"])
    welt["env"]["CLAUDE_AUFZEICHNUNG_EXIT"] = "1"
    assert _takt(welt, "--claude", str(CLAUDE_AUFZ)).returncode == 1
    welt["env"]["CLAUDE_AUFZEICHNUNG_EXIT"] = "0"
    assert _takt(welt, "--claude", str(CLAUDE_AUFZ)).returncode == 0
    erster, zweiter = _aufrufe(welt)
    assert zweiter["stdin"] == erster["stdin"]


def test_bremse_meldung_nicht_doppelt_wenn_merker_verloren(welt: dict[str, Any], monkeypatch: Any) -> None:
    """Punkt 2: Kommentar geklappt, Merker danach verloren — der nächste Takt kommentiert nicht nochmal."""
    from to_spawn import briefkasten

    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid = briefkasten.einwerfen("297", "Ordner neu anlegen?", "neu", 3, [], hauptbaum=welt["repo"])
    for _ in range(3):
        assert _takt(welt, "--claude", str(CLAUDE_AUFZ)).returncode == 0
    assert len([k for k in _kommentare(welt, "297") if fid in k]) == 1
    _aendere_merker(welt, lambda m: m["gezeigt"][f"297:{fid}"].update(gemeldet=False))
    assert _takt(welt, "--claude", str(CLAUDE_AUFZ)).returncode == 0
    assert len([k for k in _kommentare(welt, "297") if fid in k]) == 1, "Meldung doppelt"
    assert (_merker(welt) or {})["gezeigt"][f"297:{fid}"]["gemeldet"] is True


def test_issue_kommentare_blaettert_alle_seiten(monkeypatch: Any) -> None:
    """Punkt 3: Beleg hinter dem 100. Kommentar wird gefunden (gh liefert mit ``--slurp`` eine Liste je Seite)."""
    from to_spawn import capo, gh

    seite_1 = [{"body": f"k{i}", "author_association": "OWNER"} for i in range(100)]
    seite_2 = [{"body": "Beleg", "author_association": "OWNER"}]
    aufrufe: list[list[str]] = []

    def json_lauf(args: list[str], **_kw: Any) -> Any:
        aufrufe.append(args)
        return [seite_1, seite_2]

    monkeypatch.setattr(gh, "json_lauf", json_lauf)
    kommentare = capo.issue_kommentare("o/r", 7)
    assert kommentare is not None and len(kommentare) == 101
    assert kommentare[-1]["body"] == "Beleg"
    assert "--paginate" in aufrufe[0]


def test_start_text_nennt_alle_codes_von_antwort() -> None:
    """Punkt 4: die Bau-Session hat für jeden Exit-Code von ``antwort`` eine Anweisung."""
    from to_spawn import befugnis, briefkasten_cli

    kopf = briefkasten_cli.__doc__ or ""
    abschnitt = re.search(r"``antwort`` (.*?)\s·\s``antworten``", kopf, re.DOTALL)
    assert abschnitt, kopf
    codes = set(re.findall(r"\b(\d) = ", abschnitt.group(1)))
    text = befugnis.start_text(7)
    fehlend = {c for c in codes if not re.search(rf"\b{c} = ", text)}
    assert not fehlend, fehlend


@pytest.mark.parametrize("kaputt", ["drei", {"n": "3"}, {}, None])
def test_kaputter_gezeigt_eintrag_wie_fehlt(welt: dict[str, Any], monkeypatch: Any, kaputt: Any) -> None:
    """Punkt 5: kaputter Zeige-Zähler bringt den Takt nicht zum Absturz, er zählt wie „fehlt“ neu."""
    from to_spawn import briefkasten

    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid = briefkasten.einwerfen("297", "Ordner neu anlegen?", "neu", 3, [], hauptbaum=welt["repo"])
    assert _takt(welt, "--claude", str(CLAUDE_AUFZ)).returncode == 0
    _aendere_merker(welt, lambda m: m["gezeigt"].__setitem__(f"297:{fid}", kaputt))
    lauf = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    stand = (_merker(welt) or {})["gezeigt"][f"297:{fid}"]
    assert stand["n"] == 1 and stand["gemeldet"] is False
