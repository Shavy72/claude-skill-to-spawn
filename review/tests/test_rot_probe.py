"""rot_probe: neuer Test muss gegen den alten Code rot werden (echte tmp-Git-Repos)."""

from __future__ import annotations

import json
from pathlib import Path

from review_hilfen import git, lauf


def probe(repo: Path, *args: str) -> dict:
    ergebnis = lauf("rot_probe.py", "--repo", str(repo), *args)
    assert ergebnis.returncode == 0, ergebnis.stderr
    return json.loads(ergebnis.stdout)


def test_fix_mit_rotem_test_ohne_befund(repo: Path) -> None:
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_rechnen.py").write_text(
        "from rechnen import eins\n\n\ndef test_eins():\n    assert eins() == 2\n",
        encoding="utf-8",
    )
    daten = probe(repo, "--files", "rechnen.py", "tests/test_rechnen.py")
    assert daten["befunde"] == [], daten


def test_assert_true_ist_befund_hoch(repo: Path) -> None:
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "test_leer.py").write_text("def test_nichts():\n    assert True\n", encoding="utf-8")
    daten = probe(repo, "--files", "rechnen.py", "test_leer.py")
    assert len(daten["befunde"]) == 1, daten
    befund = daten["befunde"][0]
    assert befund["schwere"] == "hoch"
    assert befund["pruefer"] == "rot-probe"
    assert befund["datei"] == "test_leer.py"
    assert befund["zeile"] == 1
    assert befund["status"] == "behalten"


def test_nur_code_ohne_test_ist_befund_mittel(repo: Path) -> None:
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "README.md").write_text("# Neu\n", encoding="utf-8")
    daten = probe(repo, "--files", "rechnen.py", "README.md")
    assert [b["schwere"] for b in daten["befunde"]] == ["mittel"]
    assert daten["befunde"][0]["datei"] == "rechnen.py"
    assert "README.md" in daten["hinweis"]


def test_worktree_weg_auch_bei_timeout(repo: Path) -> None:
    (repo / "test_langsam.py").write_text("import time\n\n\ndef test_warten():\n    time.sleep(30)\n", encoding="utf-8")
    daten = probe(repo, "--files", "test_langsam.py", "--timeout", "0.5")
    assert daten["befunde"] == []
    assert "Timeout" in daten["hinweis"]
    zeilen = git(repo, "worktree", "list").strip().splitlines()
    assert len(zeilen) == 1, zeilen
