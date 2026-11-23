"""#1533: conftest.py und Helfer unter tests/ sind Test-Hilfsdateien.

Sie bleiben in der Probe auf dem neuen Stand, werden selbst nicht beurteilt und
lösen allein kein „Code geändert ohne Test“ aus.
"""

from __future__ import annotations

import json
from pathlib import Path

from review_hilfen import git, lauf

PFAD = "import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent.parent))\n"
FIXTURE_NEU = "\n\nimport pytest\n\n\n@pytest.fixture\ndef neu():\n    return 1\n"


def probe(repo: Path, *dateien: str) -> dict:
    ergebnis = lauf("rot_probe.py", "--repo", str(repo), "--files", *dateien)
    assert ergebnis.returncode == 0, ergebnis.stderr
    return json.loads(ergebnis.stdout)


def basis_anlegen(repo: Path) -> None:
    (repo / "rechnen.py").write_text("def eins():\n    return 1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "conftest.py").write_text(PFAD, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "basis")


def befunde(daten: dict) -> list[tuple[str, str]]:
    return [(b["datei"], b["schwere"]) for b in daten["befunde"]]


def test_a_conftest_und_echter_test_kein_fehlalarm(repo: Path) -> None:
    """Neue Fixture in conftest + Test, der den Code-Fix prüft → gegen alten Code rot → kein Befund."""
    basis_anlegen(repo)
    (repo / "rechnen.py").write_text("def eins():\n    return 22\n", encoding="utf-8")
    (repo / "tests" / "conftest.py").write_text(PFAD + FIXTURE_NEU, encoding="utf-8")
    (repo / "tests" / "test_eins_1533.py").write_text(
        "from rechnen import eins\n\n\ndef test_eins(neu):\n    assert eins() == 22\n", encoding="utf-8"
    )
    daten = probe(repo, "rechnen.py", "tests/conftest.py", "tests/test_eins_1533.py")
    assert daten["befunde"] == [], daten


def test_b_neue_fixture_und_leerer_test_wird_gemeldet(repo: Path) -> None:
    """Review H1/S1: neue Fixture + assert True darf nicht per ERROR auf alter conftest verschwinden."""
    basis_anlegen(repo)
    (repo / "tests" / "conftest.py").write_text(PFAD + FIXTURE_NEU, encoding="utf-8")
    (repo / "tests" / "test_s1_1533.py").write_text("def test_x(neu):\n    assert True\n", encoding="utf-8")
    daten = probe(repo, "tests/conftest.py", "tests/test_s1_1533.py")
    assert befunde(daten) == [("tests/test_s1_1533.py", "hoch")], daten


def test_c_neuer_helfer_und_leerer_test_wird_gemeldet(repo: Path) -> None:
    """Review H1/S2: neues Helfer-Modul darf nicht per Sammelfehler den Befund schlucken."""
    basis_anlegen(repo)
    (repo / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tests" / "helfer.py").write_text("WERT = 2\n", encoding="utf-8")
    (repo / "tests" / "test_s2_1533.py").write_text(
        "from tests import helfer\n\n\ndef test_x():\n    assert True\n", encoding="utf-8"
    )
    daten = probe(repo, "tests/__init__.py", "tests/helfer.py", "tests/test_s2_1533.py")
    assert befunde(daten) == [("tests/test_s2_1533.py", "hoch")], daten


def test_d_nur_conftest_geaendert_kein_code_ohne_test(repo: Path) -> None:
    """Review W1/S3: Fixture-Umbau nur in conftest ist kein „Code geändert ohne Test“."""
    basis_anlegen(repo)
    (repo / "tests" / "conftest.py").write_text(PFAD + FIXTURE_NEU, encoding="utf-8")
    daten = probe(repo, "tests/conftest.py")
    assert daten["befunde"] == [], daten


def test_gegenprobe_echter_fall_neben_conftest_bleibt_gemeldet(repo: Path) -> None:
    """Gegenprobe: Code + conftest geändert, neuer Test prüft nichts → Befund bleibt."""
    basis_anlegen(repo)
    (repo / "rechnen.py").write_text("def eins():\n    return 22\n", encoding="utf-8")
    (repo / "tests" / "conftest.py").write_text(PFAD + "PLATTE = 'voll'\n", encoding="utf-8")
    (repo / "tests" / "test_leer_1533.py").write_text("def test_nichts():\n    assert True\n", encoding="utf-8")
    daten = probe(repo, "rechnen.py", "tests/conftest.py", "tests/test_leer_1533.py")
    assert befunde(daten) == [("tests/test_leer_1533.py", "hoch")], daten


HINWEIS = "Hilfsdatei unter tests/ mitgeändert"


def test_e_wert_nur_aus_conftest_wird_gemeldet_mit_hinweis(repo: Path) -> None:
    """Fall #1429: Test prüft nur einen Wert aus conftest → Befund bleibt, erklärt sich aber selbst."""
    basis_anlegen(repo)
    (repo / "tests" / "conftest.py").write_text(PFAD + "PLATTE = 'voll'\n", encoding="utf-8")
    (repo / "tests" / "test_platte_1533.py").write_text(
        "from conftest import PLATTE\n\n\ndef test_platte():\n    assert PLATTE\n", encoding="utf-8"
    )
    daten = probe(repo, "tests/conftest.py", "tests/test_platte_1533.py")
    assert befunde(daten) == [("tests/test_platte_1533.py", "hoch")], daten
    text = daten["befunde"][0]["text"]
    assert HINWEIS in text and "tests/conftest.py" in text, text


def test_f_ohne_hilfsdatei_kein_hinweis(repo: Path) -> None:
    """Ohne geänderte Hilfsdatei bleibt der Befund-Text wie vor #1533."""
    basis_anlegen(repo)
    (repo / "rechnen.py").write_text("def eins():\n    return 22\n", encoding="utf-8")
    (repo / "tests" / "test_leer_1533.py").write_text("def test_nichts():\n    assert True\n", encoding="utf-8")
    daten = probe(repo, "rechnen.py", "tests/test_leer_1533.py")
    assert [b["text"] for b in daten["befunde"]] == [
        "Test kann nicht rot werden: prüft die Änderung nicht (test_nichts)"
    ], daten


def test_bc_befund_mit_geaenderter_hilfsdatei_traegt_hinweis(repo: Path) -> None:
    """Zusatz zu b/c: neue Fixture bzw. neuer Helfer → Befund nennt die Hilfsdateien."""
    basis_anlegen(repo)
    (repo / "tests" / "conftest.py").write_text(PFAD + FIXTURE_NEU, encoding="utf-8")
    (repo / "tests" / "helfer.py").write_text("WERT = 2\n", encoding="utf-8")
    (repo / "tests" / "test_s1_1533.py").write_text("def test_x(neu):\n    assert True\n", encoding="utf-8")
    daten = probe(repo, "tests/conftest.py", "tests/helfer.py", "tests/test_s1_1533.py")
    assert befunde(daten) == [("tests/test_s1_1533.py", "hoch")], daten
    text = daten["befunde"][0]["text"]
    assert HINWEIS in text and "tests/conftest.py, tests/helfer.py" in text, text
