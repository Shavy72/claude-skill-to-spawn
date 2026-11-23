"""Aufseher-Takt liest den Briefkasten (#278 P3b): echter Takt-Lauf in der #293-Welt.

Offene Briefkasten-Fragen eines Spec-Tickets stehen in „Zu entscheiden“ (auch im ersten Takt),
werden nach dem Zeigen gemerkt und kommen nicht doppelt.
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
    fid = briefkasten.einwerfen("297", "Ordner neu anlegen?", "neu", 3, [], hauptbaum=welt["repo"])
    probe = _takt(welt, "--dry-run")
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert f"#297 Briefkasten-Frage {fid}" in probe.stdout, probe.stdout
    erster = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert erster.returncode == 0, erster.stdout + erster.stderr
    assert any(f"Briefkasten-Frage {fid}" in str(a) for a in _aufrufe(welt))
    assert f"297:{fid}" in (_merker(welt) or {}).get("fragen", [])
    zweiter = _takt(welt, "--dry-run")
    assert f"Briefkasten-Frage {fid}" not in zweiter.stdout
