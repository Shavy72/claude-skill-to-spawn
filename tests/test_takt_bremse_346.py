"""#346: Test-Lücken der Takt-Bremse (#339) und Merker-Randfall nach einem Fehler-Lauf.

GitHub und Claude sind externe Dienste: Aufzeichnungs-Ersatz aus ``tests/hilfen`` wie in #315/#316;
gestellt sind nur ``gh.json_lauf`` (Blättern), ``capo.issue_kommentare``/``befund.kommentiere``
(GitHub-Antwort „nicht lesbar“) und ``pruefe`` (fester Merker-Stand für die Fehler-Lauf-Rechnung).
"""

# ruff: noqa: F811 — Fixture ``welt`` kommt aus dem Nachbar-Test und wird als Parameter genannt.

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tests"))

from test_takt_bremse_315 import _zeige_aufrufe
from test_waechter_takt_316 import CLAUDE_AUFZ, PY, _merker, _takt, welt  # noqa: F401


def test_issue_kommentare_fragt_mit_slurp(monkeypatch: Any) -> None:
    """Lücke 1: ohne ``--slurp`` käme je Seite ein eigenes JSON — der Aufruf muss es tragen."""
    from to_spawn import capo, gh

    aufrufe: list[list[str]] = []

    def json_lauf(args: list[str], **_kw: Any) -> Any:
        aufrufe.append(args)
        return [[{"body": "k", "author_association": "OWNER"}]]

    monkeypatch.setattr(gh, "json_lauf", json_lauf)
    assert capo.issue_kommentare("o/r", 7) == [{"body": "k", "author_association": "OWNER"}]
    assert "--paginate" in aufrufe[0] and "--slurp" in aufrufe[0]


def test_melde_gebremste_kommentare_unlesbar_naechster_takt(monkeypatch: Any) -> None:
    """Lücke 2: Kommentare nicht lesbar ⇒ kein Kommentar, nicht gemeldet; lesbar ⇒ jetzt gemeldet."""
    from to_spawn import befund, capo, waechter_takt

    antwort: list[Any] = [None]
    gepostet: list[str] = []
    monkeypatch.setattr(capo, "issue_kommentare", lambda _repo, _n: antwort[0])
    monkeypatch.setattr(befund, "kommentiere", lambda _t, _r, text: gepostet.append(text) or True)
    neu: dict[str, Any] = {"gezeigt": {"297:abc": {"n": waechter_takt.BREMSE_AB, "gemeldet": False}}}
    waechter_takt.melde_gebremste("o/r", neu)
    assert gepostet == [] and neu["gezeigt"]["297:abc"]["gemeldet"] is False
    antwort[0] = []
    waechter_takt.melde_gebremste("o/r", neu)
    assert len(gepostet) == 1 and neu["gezeigt"]["297:abc"]["gemeldet"] is True


def test_fehler_lauf_laesst_alle_felder_wiederkommen(welt: dict[str, Any], monkeypatch: Any) -> None:
    """Lücke 3: nach einem Fehler-Lauf fehlen Zeilen, „zu“, Verstöße und „SPEC FERTIG“ im Merker."""
    from to_spawn import leitstand, waechter_takt

    for name, wert in welt["env"].items():
        monkeypatch.setenv(name, wert)
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    monkeypatch.setenv("CLAUDE_AUFZEICHNUNG_EXIT", "1")
    eintraege = [
        ("#296 Zeile", "zeilen", "k1"),
        ("#296 neu geschlossen", "zu", 296),
        ("#297 capo-Verstoß R1", "verstoesse", "297:R1"),
        ("#293 SPEC FERTIG", "fertig", True),
        ("#297 Briefkasten-Frage abc", "fragen", "297:abc"),
    ]
    neu = {
        "zeilen": ["k1"],
        "zu": [296],
        "verstoesse": ["297:R1"],
        "fragen": ["297:abc"],
        "belegt": [],
        "gezeigt": {},
        "fertig": True,
        "zuletzt": "",
    }
    monkeypatch.setattr(
        waechter_takt,
        "pruefe",
        lambda *_a, **_kw: (list(eintraege), json.loads(json.dumps(neu))),
    )
    assert waechter_takt.takt(welt["repo"], 293, "o/r", claude=str(CLAUDE_AUFZ)) == 1
    merker = leitstand.lese_zustand()["takt"]["293"]
    assert merker["zeilen"] == [] and merker["zu"] == [] and merker["verstoesse"] == []
    assert merker["fertig"] is False
    assert merker["gezeigt"]["297:abc"]["n"] == 1


def test_klasse_2_beleg_nach_fehler_lauf_gefunden(welt: dict[str, Any], monkeypatch: Any) -> None:
    """Randfall: gezeigt ist gezeigt — nach einem Fehler-Lauf sucht der nächste Takt den Beleg."""
    from to_spawn import briefkasten

    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid = briefkasten.einwerfen("297", "Live schalten?", "nein", 2, [], hauptbaum=welt["repo"])
    welt["env"]["CLAUDE_AUFZEICHNUNG_EXIT"] = "1"
    assert _takt(welt, "--claude", str(CLAUDE_AUFZ)).returncode == 1
    assert f"297:{fid}" in (_merker(welt) or {}).get("fragen", []), "gezeigte Frage fehlt im Merker"
    # Die gescheiterte Session hatte den Kommentar an den Nutzer noch geschrieben.
    text = f"Aufseher: braucht den Nutzer: Live schalten? — bitte: `to_spawn.py nutzer-antwort 297 --id {fid} …`"
    gh = subprocess.run(
        [
            PY,
            welt["env"]["TO_SPAWN_GH_STUB"],
            "issue",
            "comment",
            "297",
            "--body",
            text,
        ],
        env=welt["env"],
        check=False,
    )
    assert gh.returncode == 0
    welt["env"]["CLAUDE_AUFZEICHNUNG_EXIT"] = "0"
    assert _takt(welt, "--claude", str(CLAUDE_AUFZ)).returncode == 0
    assert _zeige_aufrufe(welt, fid) == 1, "belegte Klasse-2-Frage nach Fehler-Lauf erneut gezeigt"
    assert f"297:{fid}" in (_merker(welt) or {}).get("belegt", [])
