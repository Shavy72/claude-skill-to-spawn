"""Aufseher-Takt liest den Briefkasten (#278 P3b): echter Takt-Lauf in der #293-Welt.

Offene Briefkasten-Fragen eines Spec-Tickets stehen in „Zu entscheiden“ (auch im ersten Takt).
Klasse 2 wird nach dem Zeigen gemerkt und kommt nicht doppelt; Klasse 3 kommt wieder, bis sie
beantwortet ist; eine beantwortete Frage fällt aus dem Merker.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tests"))

from test_waechter_takt_316 import CLAUDE_AUFZ, _aufrufe, _merker, _takt, welt  # noqa: E402,F401


def test_takt_zeigt_briefkasten_frage_einmal(welt: dict[str, Any], monkeypatch: Any) -> None:
    from to_spawn import briefkasten

    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid = briefkasten.einwerfen("297", "Live schalten?", "nein", 2, [], hauptbaum=welt["repo"])
    probe = _takt(welt, "--dry-run")
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert f"#297 Briefkasten-Frage {fid}" in probe.stdout, probe.stdout
    erster = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert erster.returncode == 0, erster.stdout + erster.stderr
    assert any(f"Briefkasten-Frage {fid}" in str(a) for a in _aufrufe(welt))
    assert f"297:{fid}" in (_merker(welt) or {}).get("fragen", [])
    # #315: „einmal“ gilt erst mit belegtem Kommentar an den Nutzer (hier über dieselbe gh-Tür).
    from to_spawn import befund

    for name in ("TO_SPAWN_GH_STUB", "GH_AUFZEICHNUNG_ORDNER", "GH_AUFZEICHNUNG_KOMMENTARE"):
        monkeypatch.setenv(name, welt["env"][name])
    assert befund.kommentiere(
        297, "o/r", f"Aufseher: braucht den Nutzer: Live schalten? — `nutzer-antwort 297 --id {fid}`"
    )
    zweiter = _takt(welt, "--dry-run")
    assert f"Briefkasten-Frage {fid}" not in zweiter.stdout


def test_offene_klasse_3_frage_kommt_im_zweiten_takt_wieder(welt: dict[str, Any], monkeypatch: Any) -> None:
    from to_spawn import briefkasten

    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid = briefkasten.einwerfen("297", "Ordner neu anlegen?", "neu", 3, [], hauptbaum=welt["repo"])
    erster = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert erster.returncode == 0, erster.stdout + erster.stderr
    zweiter = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert zweiter.returncode == 0, zweiter.stdout + zweiter.stderr
    aufrufe = [a for a in _aufrufe(welt) if f"Briefkasten-Frage {fid}" in str(a)]
    assert len(aufrufe) == 2, "unbeantwortete Klasse-3-Frage fehlt im zweiten Takt"


def test_beantwortete_frage_faellt_aus_dem_merker(welt: dict[str, Any], monkeypatch: Any) -> None:
    from to_spawn import briefkasten

    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid = briefkasten.einwerfen("297", "Ordner neu anlegen?", "neu", 3, [], hauptbaum=welt["repo"])
    assert _takt(welt, "--claude", str(CLAUDE_AUFZ)).returncode == 0
    assert f"297:{fid}" in (_merker(welt) or {}).get("fragen", [])
    briefkasten.antworten("297", fid, "neu", ["Beleg"], "passt", hauptbaum=welt["repo"])
    danach = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert danach.returncode == 0, danach.stdout + danach.stderr
    assert f"297:{fid}" not in (_merker(welt) or {}).get("fragen", [])
