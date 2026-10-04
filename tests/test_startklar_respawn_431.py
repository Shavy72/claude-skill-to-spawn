"""#431 Merge-Review Befund 7: der respawn-Befehl des Aufsehers hat eine Quelle.

``to_spawn/startklar.py`` hält alle Aufseher-Befehle (``BEFEHL_*``); ``skripte/wache.py``
setzt sie als Platzhalter ein, und die Werkzeug-Probe prüft sie per Trockenlauf.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_startklar_450 import (
    SKILL,
    SPEC,
    _git,
    _heim_mit_werkzeugen,
    _manifest,
    _mini_venv,  # noqa: F401  (autouse-Fixture: echtes Mini-venv)
    _repo_mit_worktree,
    _wache,
)

from to_spawn import startklar


def _respawn() -> startklar.Werkzeug:
    treffer = [w for w in startklar.WERKZEUGE if w.name == "respawn"]
    assert len(treffer) == 1, startklar.WERKZEUGE
    return treffer[0]


def test_b7_befehl_respawn_in_startklar_mit_help_probe() -> None:
    assert startklar.BEFEHL_RESPAWN == "python {SKILL}/to_spawn.py respawn {S} <N>"
    werkzeug = _respawn()
    assert werkzeug.befehl == startklar.BEFEHL_RESPAWN
    assert werkzeug.datei == "{SKILL}/to_spawn.py"
    assert werkzeug.trocken == " --help"


def test_b7_wache_hat_respawn_nicht_hartkodiert() -> None:
    quelle = (SKILL / "skripte" / "wache.py").read_text(encoding="utf-8")
    assert "to_spawn.py respawn" not in quelle
    assert quelle.count("`{RESPAWN}`") == 2


def test_b7_prompt_nennt_respawn_aus_startklar(tmp_path: Path) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    _git(wt, "remote", "add", "origin", "https://github.com/t/s.git")
    ergebnis = _wache(wt, wt, _heim_mit_werkzeugen(tmp_path), "--print-prompt")
    assert ergebnis.returncode == 0, ergebnis.stderr
    text = startklar.befehl_text(_respawn(), SPEC, "<N>")
    assert ergebnis.stdout.count(text) == 2, text


def test_b7_probe_prueft_respawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(_heim_mit_werkzeugen(tmp_path)))
    _manifest(tmp_path, {"451": {"title": "a"}})
    befunde = startklar.werkzeug_probe(tmp_path, SPEC, settings_dateien=[])
    respawn_befunde = [b for b in befunde if b.text.startswith("respawn")]
    assert respawn_befunde and all(b.ok for b in respawn_befunde), befunde
