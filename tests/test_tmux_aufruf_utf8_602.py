"""#602: tmux-Ausgabe ist UTF-8 — ``tmux_aufruf.aufrufen`` liest sie auch unter Windows (cp1252) unverfälscht.

Echt ist ``aufrufen`` samt Prozess-Start; gestellt ist nur ``tmux`` (Attrappe schreibt UTF-8-Bytes wie das echte).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import attrappe

from to_spawn import tmux_aufruf

BILD = "● Frage eingeworfen\n❯ \n  ⏵⏵ auto mode · 1h\n"

FAKE_TMUX = f"""
import sys
sys.stdout.buffer.write({BILD!r}.encode("utf-8"))
"""


def test_capture_pane_liefert_utf8_zeichen_unverfaelscht(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmux = attrappe(tmp_path / "bin", "tmux", FAKE_TMUX)
    monkeypatch.setenv("TO_SPAWN_TMUX", json.dumps([sys.executable, str(tmux)]))
    assert tmux_aufruf.aufrufen("capture-pane", "-p", "-t", "%1") == BILD
