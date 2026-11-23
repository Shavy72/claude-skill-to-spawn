"""Befund 2: eine rote Testdatei darf den Befund einer grünen daneben nicht verschlucken."""

from __future__ import annotations

import json
from pathlib import Path

from review_hilfen import git, lauf


def test_rote_und_gruene_testdatei_genau_ein_befund(repo: Path) -> None:
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "test_a_rot.py").write_text(
        "from rechnen import eins\n\n\ndef test_eins():\n    assert eins() == 2\n", encoding="utf-8"
    )
    (repo / "test_b_leer.py").write_text("def test_nichts():\n    assert True\n", encoding="utf-8")
    ergebnis = lauf("rot_probe.py", "--repo", str(repo), "--files", "rechnen.py", "test_a_rot.py", "test_b_leer.py")
    assert ergebnis.returncode == 0, ergebnis.stderr
    befunde = json.loads(ergebnis.stdout)["befunde"]
    assert [(b["datei"], b["schwere"]) for b in befunde] == [("test_b_leer.py", "hoch")], befunde


def _base_mit_test_alt(repo: Path) -> None:
    (repo / "test_r.py").write_text(
        "import pytest\n\n\n@pytest.fixture\ndef wert():\n    return 1\n\n\n"
        "def test_alt(wert):\n    assert wert == 1\n",
        encoding="utf-8",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")


def _probe(repo: Path) -> dict:
    ergebnis = lauf("rot_probe.py", "--repo", str(repo), "--files", "rechnen.py", "test_r.py")
    assert ergebnis.returncode == 0, ergebnis.stderr
    return json.loads(ergebnis.stdout)


def test_alter_test_in_geaenderter_datei_kein_befund(repo: Path) -> None:
    _base_mit_test_alt(repo)
    (repo / "test_r.py").write_text(
        "import pytest\nfrom rechnen import eins\n\n\n@pytest.fixture\ndef wert():\n    return 1 + 0\n\n\n"
        "def test_alt(wert):\n    assert wert == 1\n\n\ndef test_neu():\n    assert eins() == 2\n",
        encoding="utf-8",
    )
    assert _probe(repo)["befunde"] == []


def test_neuer_gruener_test_neben_altem_genau_ein_befund(repo: Path) -> None:
    _base_mit_test_alt(repo)
    (repo / "test_r.py").write_text(
        "import pytest\n\n\n@pytest.fixture\ndef wert():\n    return 1 + 0\n\n\n"
        "def test_alt(wert):\n    assert wert == 1\n\n\ndef test_neu2():\n    assert True\n",
        encoding="utf-8",
    )
    befunde = _probe(repo)["befunde"]
    assert len(befunde) == 1, befunde
    assert befunde[0]["schwere"] == "hoch" and "test_neu2" in befunde[0]["text"]
    assert "test_alt" not in befunde[0]["text"]


def test_nur_fixture_geaendert_kein_befund_mit_hinweis(repo: Path) -> None:
    _base_mit_test_alt(repo)
    (repo / "test_r.py").write_text(
        "import pytest\n\n\n@pytest.fixture\ndef wert():\n    return 1 + 0\n\n\n"
        "def test_alt(wert):\n    assert wert == 1\n",
        encoding="utf-8",
    )
    daten = _probe(repo)
    assert daten["befunde"] == [], daten
    assert "test_r.py" in daten["hinweis"]


def test_importfehler_in_einer_datei_verschluckt_andere_nicht(repo: Path) -> None:
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "test_a_import.py").write_text(
        "from rechnen import zwei\n\n\ndef test_zwei():\n    assert zwei() == 2\n", encoding="utf-8"
    )
    (repo / "test_b_leer.py").write_text("def test_nichts():\n    assert True\n", encoding="utf-8")
    ergebnis = lauf("rot_probe.py", "--repo", str(repo), "--files", "rechnen.py", "test_a_import.py", "test_b_leer.py")
    assert ergebnis.returncode == 0, ergebnis.stderr
    daten = json.loads(ergebnis.stdout)
    assert [(b["datei"], b["schwere"]) for b in daten["befunde"]] == [("test_b_leer.py", "hoch")], daten
    assert "test_a_import.py" in daten["hinweis"]
