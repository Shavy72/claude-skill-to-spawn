"""Skill #326: Diff ändert den geprüften Code nicht (nur Kommentar/Docstring) → nicht beurteilbar, kein Befund."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from review_hilfen import git, lauf

ALT = "def letzter():\n    return 'abc'\n"
TEST_NEU = (
    "import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent.parent))\n\n"
    "from gh import letzter\n\n\ndef test_letzter():\n    assert letzter() == 'abc'\n"
)


def probe(repo: Path, *dateien: str) -> dict:
    ergebnis = lauf("rot_probe.py", "--repo", str(repo), "--files", *dateien)
    assert ergebnis.returncode == 0, ergebnis.stderr
    return json.loads(ergebnis.stdout)


def basis_anlegen(repo: Path) -> None:
    (repo / "gh.py").write_text(ALT, encoding="utf-8")
    (repo / "capo.py").write_text('"""Capo."""\n\nX = 1\n', encoding="utf-8")
    (repo / "tests").mkdir()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "basis")
    (repo / "tests" / "test_gh_letzter.py").write_text(TEST_NEU, encoding="utf-8")


def test_nur_kommentar_im_code_ist_nicht_beurteilbar(repo: Path) -> None:
    """Fall #326: Kommentar in capo.py + neuer Test für vorhandenes gh.py → kein harter Befund."""
    basis_anlegen(repo)
    (repo / "capo.py").write_text(
        '"""Capo, neu beschrieben."""\n\n# Kommentar\n\nX = 1  # eins\n',
        encoding="utf-8",
    )
    daten = probe(repo, "capo.py", "tests/test_gh_letzter.py")
    assert daten["befunde"] == [], daten
    assert (
        "nicht beurteilbar: Diff ändert den geprüften Code nicht" in daten["hinweis"]
    ), daten


def test_gegenprobe_codeaenderung_mit_gruenem_test_bleibt_befund(repo: Path) -> None:
    """Diff ändert ausführbaren Code, Test bleibt gegen alten Stand grün → harter Befund bleibt."""
    basis_anlegen(repo)
    (repo / "capo.py").write_text('"""Capo."""\n\nX = 2\n', encoding="utf-8")
    daten = probe(repo, "capo.py", "tests/test_gh_letzter.py")
    assert [(b["datei"], b["schwere"]) for b in daten["befunde"]] == [
        ("tests/test_gh_letzter.py", "hoch")
    ], daten


def test_eine_code_datei_nur_kommentar_andere_mit_codeaenderung_bleibt_befund(
    repo: Path,
) -> None:
    """Zwei Code-Dateien: eine nur Kommentar, eine echt geändert → harter Befund (any, nicht all)."""
    basis_anlegen(repo)
    (repo / "capo.py").write_text(
        '"""Capo."""\n\n# nur Kommentar\nX = 1\n', encoding="utf-8"
    )
    (repo / "gh.py").write_text("def letzter():\n    return 'xyz'\n", encoding="utf-8")
    daten = probe(repo, "capo.py", "gh.py", "tests/test_gh_letzter.py")
    assert [(b["datei"], b["schwere"]) for b in daten["befunde"]] == [
        ("tests/test_gh_letzter.py", "hoch")
    ], daten
    assert "nicht beurteilbar" not in daten["hinweis"], daten


@pytest.mark.parametrize(
    ("datei", "inhalt"),
    [("neu.py", "X = 1\n"), ("capo.py", "def kaputt(:\n")],
    ids=["neue-datei", "syntaxfehler"],
)
def test_vorsichtiger_rueckfall_zaehlt_als_codeaenderung(
    repo: Path, datei: str, inhalt: str
) -> None:
    """Datei fehlt in base oder ist nicht parsebar → gilt als ausführbar geändert, Befund bleibt."""
    basis_anlegen(repo)
    (repo / datei).write_text(inhalt, encoding="utf-8")
    daten = probe(repo, datei, "tests/test_gh_letzter.py")
    assert [(b["datei"], b["schwere"]) for b in daten["befunde"]] == [
        ("tests/test_gh_letzter.py", "hoch")
    ], daten
    assert "nicht beurteilbar" not in daten["hinweis"], daten


def test_nur_docstring_von_funktion_und_klasse_ist_nicht_beurteilbar(
    repo: Path,
) -> None:
    """Nur Docstrings in Funktion und Klasse neu → kein Befund, nicht beurteilbar."""
    (repo / "gh.py").write_text(ALT, encoding="utf-8")
    (repo / "capo.py").write_text(
        'def f():\n    """alt"""\n    return 1\n\n\nclass K:\n    """alt"""\n    y = 2\n',
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "basis")
    (repo / "tests" / "test_gh_letzter.py").write_text(TEST_NEU, encoding="utf-8")
    (repo / "capo.py").write_text(
        'def f():\n    """neu beschrieben"""\n    return 1\n\n\nclass K:\n    """auch neu"""\n    y = 2\n',
        encoding="utf-8",
    )
    daten = probe(repo, "capo.py", "tests/test_gh_letzter.py")
    assert daten["befunde"] == [], daten
    assert "nicht beurteilbar" in daten["hinweis"], daten
