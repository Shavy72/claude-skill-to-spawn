# ruff: noqa: F811  (Fixtures aus test_hooks importiert)
"""Zusatz-Tests Fix 1 (VERTRAG.md §8, Beleg 11 Nachschlag): Pathspec-Scope für die
Nachmeldung, gelöschte bekannte Datei bleibt im Block-Text, race-freies Anhänge-Log
holt eine vom JSON verlorene Datei zurück."""

from __future__ import annotations

import json
import time
from pathlib import Path

from test_hooks import Umgebung, git, py_aendern, repo, u  # noqa: F401


def test_stop_fremde_datei_in_anderem_ordner_wird_nicht_nachgemeldet(u: Umgebung, repo: Path) -> None:
    (repo / "sub").mkdir()
    (repo / "sub" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (repo / "other").mkdir()
    (repo / "other" / "b.py").write_text("def b():\n    return 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "vorbereiten")

    u.merke(repo, "sub/a.py")
    time.sleep(0.05)
    (repo / "sub" / "a.py").write_text("def a():\n    return 1\n\n\ndef a2():\n    return 2\n", encoding="utf-8")
    (repo / "other" / "b.py").write_text("def b():\n    return 1\n\n\ndef b2():\n    return 2\n", encoding="utf-8")

    aus = u.stop(repo)
    assert aus is not None and aus["decision"] == "block"
    assert "sub/a.py" in aus["reason"]
    assert "other/b.py" not in aus["reason"]


def test_stop_geloeschte_bekannte_datei_bleibt_im_block_text(u: Umgebung, repo: Path) -> None:
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    (repo / "rechnen.py").unlink()

    aus = u.stop(repo)
    assert aus is not None and aus["decision"] == "block"
    assert "rechnen.py" in aus["reason"]


def test_stop_race_json_verliert_datei_log_hat_beide(u: Umgebung, repo: Path) -> None:
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    u.merke(repo, "a.py")

    # Race simulieren: eine parallele Sitzung überschreibt das JSON zwischendrin und
    # verliert a.py. Das race-freie Anhänge-Log (os.write je Zeile) behält den Eintrag.
    sitzungsdatei = u.sitzungen / "s1.json"
    daten = json.loads(sitzungsdatei.read_text(encoding="utf-8"))
    daten["dateien"] = []
    sitzungsdatei.write_text(json.dumps(daten), encoding="utf-8")

    (repo / "b.py").write_text("b = 1\n", encoding="utf-8")
    u.merke(repo, "b.py")

    aus = u.stop(repo)
    assert aus is not None and aus["decision"] == "block"
    assert "a.py" in aus["reason"]
    assert "b.py" in aus["reason"]
