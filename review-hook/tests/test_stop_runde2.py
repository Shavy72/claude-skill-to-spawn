# ruff: noqa: F811  (Fixtures aus test_hooks importiert)
"""Zusatz-Tests Runde 2 (VERTRAG.md §8): Befund B (hinweis-Modus schweigt an der Block-Grenze)
und Befund 11 (Merker verliert Subagent-Dateien, stop.py meldet aus git status nach)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from test_hooks import Umgebung, py_aendern, repo, u  # noqa: F401


def test_stop_hinweis_grenze_sichtbare_meldung_statt_still(u: Umgebung, repo: Path) -> None:
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    erster = u.stop(repo)
    assert erster is not None and erster["decision"] == "block"

    zweiter = u.stop(repo)
    assert zweiter is not None
    assert "decision" not in zweiter
    assert "Review fällig – Mensch nötig" in zweiter["systemMessage"]
    assert repo.as_posix() in zweiter["systemMessage"]


def test_stop_hinweis_grenze_vermerkt_im_sitzungs_state(u: Umgebung, repo: Path) -> None:
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    u.stop(repo)
    u.stop(repo)
    daten = json.loads((u.sitzungen / "s1.json").read_text(encoding="utf-8"))
    assert daten.get("hinweis_mensch")


def test_stop_scharf_meldung_bleibt_unveraendert(u: Umgebung, repo: Path) -> None:
    ROT = [
        {
            "datei": "rechnen.py",
            "zeile": 5,
            "schwere": "hoch",
            "konfidenz": 95,
            "pruefer": "python-reviewer",
            "text": "falsch",
            "status": "behalten",
        }
    ]
    u.modus("scharf", max_fix_runden=1)
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    u.beleg(repo, u.route(repo, "--moment", "sitzung", "--files", "rechnen.py"), ROT)
    u.stop(repo, aktiv=True)
    aus = u.stop(repo, aktiv=True)
    assert aus is not None and "decision" not in aus
    assert aus["systemMessage"].startswith("Review: Mensch nötig")
    assert "Review fällig – Mensch nötig" not in aus["systemMessage"]


def test_stop_meldet_subagent_datei_nach_die_merker_verpasst_hat(u: Umgebung, repo: Path) -> None:
    u.merke(repo, "rechnen.py")
    time.sleep(0.05)
    (repo / "subagent.py").write_text("x = 2\n", encoding="utf-8")
    aus = u.stop(repo)
    assert aus is not None and aus["decision"] == "block"
    assert "subagent.py" in aus["reason"]


def test_stop_nachmeldung_ignoriert_dateien_vor_sitzungsstart(u: Umgebung, repo: Path) -> None:
    (repo / "vorher.py").write_text("x = 1\n", encoding="utf-8")
    u.merke(repo, "rechnen.py")
    py_aendern(repo)
    aus = u.stop(repo)
    assert aus is not None and aus["decision"] == "block"
    assert "vorher.py" not in aus["reason"]


def test_merker_setzt_sitzungsstart_beim_ersten_aufruf(u: Umgebung, repo: Path) -> None:
    vor = time.time()
    u.merke(repo, "rechnen.py")
    nach = time.time()
    daten = json.loads((u.sitzungen / "s1.json").read_text(encoding="utf-8"))
    assert vor <= daten["start"] <= nach
