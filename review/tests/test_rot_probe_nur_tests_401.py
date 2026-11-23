"""Skill #401: reiner Test-Diff (ohne Code, ohne Hilfsdatei) → alter Stand = neuer Stand, keine Aussage.

Ausnahme: ein neuer Test ohne jede Prüfung (nur `assert True`/`pass`) bleibt ein Befund,
er kann unabhängig vom Code nie rot werden.
"""

from __future__ import annotations

import json
from pathlib import Path

from review_hilfen import lauf

HINWEIS = "ausgelassen: nur Tests geändert, keine Aussage"
TEST_ECHT = "from rechnen import eins\n\n\ndef test_eins():\n    assert eins() == 1\n"
PFAD = "import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent.parent))\n"


def probe(repo: Path, *dateien: str) -> dict:
    ergebnis = lauf("rot_probe.py", "--repo", str(repo), "--files", *dateien)
    assert ergebnis.returncode == 0, ergebnis.stderr
    return json.loads(ergebnis.stdout)


def befunde(daten: dict) -> list[tuple[str, str]]:
    return [(b["datei"], b["schwere"]) for b in daten["befunde"]]


def test_reiner_test_diff_ist_ausgelassen(repo: Path) -> None:
    """Fall Pull-Request 398: neuer Test für vorhandenen Code, sonst nichts geändert → kein Befund."""
    (repo / "test_eins_401.py").write_text(TEST_ECHT, encoding="utf-8")
    daten = probe(repo, "test_eins_401.py")
    assert daten["befunde"] == [], daten
    assert HINWEIS in daten["hinweis"], daten


def test_gegenprobe_test_mit_geaendertem_code_bleibt_befund(repo: Path) -> None:
    (repo / "rechnen.py").write_text("def eins():\n    return 1\n\n\ndef zwei():\n    return 2\n", encoding="utf-8")
    (repo / "test_eins_401.py").write_text(TEST_ECHT, encoding="utf-8")
    daten = probe(repo, "rechnen.py", "test_eins_401.py")
    assert befunde(daten) == [("test_eins_401.py", "hoch")], daten
    assert HINWEIS not in daten["hinweis"], daten


def test_gegenprobe_nur_kommentar_im_code_bleibt_nicht_beurteilbar(repo: Path) -> None:
    (repo / "rechnen.py").write_text("def eins():\n    # Kommentar\n    return 1\n", encoding="utf-8")
    (repo / "test_eins_401.py").write_text(TEST_ECHT, encoding="utf-8")
    daten = probe(repo, "rechnen.py", "test_eins_401.py")
    assert daten["befunde"] == [], daten
    assert "nicht beurteilbar" in daten["hinweis"], daten


def test_gegenprobe_test_mit_hilfsdatei_bleibt_befund_wie_1533(repo: Path) -> None:
    """#1533: geänderte Hilfsdatei unter tests/ kann Tests grün machen → Erkennung bleibt scharf."""
    (repo / "tests").mkdir()
    (repo / "tests" / "conftest.py").write_text(PFAD + "WERT = 1\n", encoding="utf-8")
    (repo / "tests" / "test_eins_401.py").write_text(TEST_ECHT, encoding="utf-8")
    daten = probe(repo, "tests/conftest.py", "tests/test_eins_401.py")
    assert befunde(daten) == [("tests/test_eins_401.py", "hoch")], daten
    assert "Hilfsdatei unter tests/ mitgeändert" in daten["befunde"][0]["text"], daten
    assert HINWEIS not in daten["hinweis"], daten


def test_gegenprobe_reiner_test_diff_ohne_pruefung_bleibt_befund(repo: Path) -> None:
    """`assert True` prüft gar nichts, egal welcher Code-Stand → Befund bleibt (wie test_rot_probe_platte)."""
    (repo / "test_leer_401.py").write_text(
        "def test_nichts():\n    assert True\n\n\nclass TestLeer:\n    def test_pass(self):\n        pass\n",
        encoding="utf-8",
    )
    daten = probe(repo, "test_leer_401.py")
    assert befunde(daten) == [("test_leer_401.py", "hoch")], daten


def test_gegenprobe_test_mit_geaenderter_datendatei_bleibt_befund(repo: Path) -> None:
    """Review F1: Test + geänderte Nicht-Python-Datei ist kein reiner Test-Diff → Befund bleibt."""
    (repo / "daten.json").write_text('{"wert": 1}\n', encoding="utf-8")
    (repo / "test_daten_401.py").write_text(
        "import json\nfrom pathlib import Path\n\n\ndef test_wert():\n"
        "    assert json.loads((Path(__file__).parent / 'daten.json').read_text())['wert'] == 1\n",
        encoding="utf-8",
    )
    daten = probe(repo, "daten.json", "test_daten_401.py")
    assert befunde(daten) == [("test_daten_401.py", "hoch")], daten
    assert HINWEIS not in daten["hinweis"], daten


def test_gegenprobe_leere_methode_neben_gleichnamiger_pruefender_bleibt_befund(repo: Path) -> None:
    """Review F3: gleichnamige Methode in anderer Klasse darf den Leer-Test nicht verdecken."""
    (repo / "test_klassen_401.py").write_text(
        "from rechnen import eins\n\n\nclass TestA:\n    def test_x(self):\n        assert eins() == 1\n\n\n"
        "class TestB:\n    def test_x(self):\n        assert True\n",
        encoding="utf-8",
    )
    daten = probe(repo, "test_klassen_401.py")
    assert befunde(daten) == [("test_klassen_401.py", "hoch")], daten
