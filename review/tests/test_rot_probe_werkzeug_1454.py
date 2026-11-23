"""#1454: Werkzeug mit test_-Namen (scripts/test_plaetze.py) ist Code, kein Test."""

from __future__ import annotations

import json
from pathlib import Path

from review_hilfen import git, lauf

WERKZEUG_ALT = "def plaetze():\n    return 1\n"
WERKZEUG_NEU = "def plaetze():\n    return 22\n"  # andere Größe: kein veralteter .pyc-Treffer


def probe(repo: Path, *dateien: str) -> dict:
    ergebnis = lauf("rot_probe.py", "--repo", str(repo), "--files", *dateien)
    assert ergebnis.returncode == 0, ergebnis.stderr
    return json.loads(ergebnis.stdout)


def werkzeug_anlegen(repo: Path) -> None:
    (repo / "scripts").mkdir()
    (repo / "scripts" / "test_plaetze.py").write_text(WERKZEUG_ALT, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "werkzeug")
    (repo / "scripts" / "test_plaetze.py").write_text(WERKZEUG_NEU, encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))\n",
        encoding="utf-8",
    )


def test_werkzeug_mit_testnamen_wird_gegen_alten_stand_geprueft(repo: Path) -> None:
    """Fix im Werkzeug + Test, der ihn prüft: Test wird gegen das alte Werkzeug rot → kein Fehlalarm."""
    werkzeug_anlegen(repo)
    (repo / "tests" / "test_plaetze_fix.py").write_text(
        "from test_plaetze import plaetze\n\n\ndef test_zwei():\n    assert plaetze() == 22\n",
        encoding="utf-8",
    )
    daten = probe(
        repo,
        "scripts/test_plaetze.py",
        "tests/conftest.py",
        "tests/test_plaetze_fix.py",
    )
    assert daten["befunde"] == [], daten


def test_werkzeug_allein_ist_code_ohne_test(repo: Path) -> None:
    werkzeug_anlegen(repo)
    daten = probe(repo, "scripts/test_plaetze.py")
    assert [(b["datei"], b["schwere"]) for b in daten["befunde"]] == [("scripts/test_plaetze.py", "mittel")], daten


def test_gegenprobe_echter_test_unter_tests_bleibt_erkannt(repo: Path) -> None:
    """Gegenprobe: Testdatei unter tests/, die nicht rot werden kann, meldet weiter den Befund."""
    werkzeug_anlegen(repo)
    (repo / "tests" / "test_leer.py").write_text("def test_nichts():\n    assert True\n", encoding="utf-8")
    daten = probe(repo, "scripts/test_plaetze.py", "tests/test_leer.py")
    assert [(b["datei"], b["schwere"]) for b in daten["befunde"]] == [("tests/test_leer.py", "hoch")], daten


def test_echter_test_ausserhalb_tests_bleibt_erkannt(repo: Path) -> None:
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "test_leer.py").write_text("def test_nichts():\n    assert True\n", encoding="utf-8")
    daten = probe(repo, "rechnen.py", "test_leer.py")
    assert [(b["datei"], b["schwere"]) for b in daten["befunde"]] == [("test_leer.py", "hoch")], daten


def test_werkzeug_mit_testfunktion_gilt_als_test(repo: Path) -> None:
    """Festgenagelt: Testname + def test_ im Inhalt = Test, auch unter scripts/."""
    (repo / "scripts").mkdir()
    (repo / "scripts" / "test_misch.py").write_text(
        "def werkzeug():\n    return 1\n\n\ndef test_nichts():\n    assert True\n", encoding="utf-8"
    )
    daten = probe(repo, "scripts/test_misch.py")
    assert [(b["datei"], b["schwere"]) for b in daten["befunde"]] == [("scripts/test_misch.py", "hoch")], daten


def test_geloeschte_datei_kein_absturz_name_entscheidet(repo: Path) -> None:
    (repo / "test_alt.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "alt")
    (repo / "test_alt.py").unlink()
    daten = probe(repo, "test_alt.py")
    assert daten["befunde"] == [], daten


def test_klasse_und_async_zaehlen_kommentar_nicht(repo: Path) -> None:
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "test_klasse.py").write_text(
        "class TestLeer:\n    def test_nichts(self):\n        assert True\n", encoding="utf-8"
    )
    (repo / "test_async.py").write_text("async def test_leer():\n    pass\n", encoding="utf-8")
    (repo / "test_kommentar.py").write_text("# def test_nichts():\nX = 1\n", encoding="utf-8")

    def schweren(datei: str) -> list[str]:
        return [b["schwere"] for b in probe(repo, datei)["befunde"]]

    assert schweren("test_klasse.py") == ["hoch"]
    assert "mittel" not in schweren("test_async.py")
    assert schweren("test_kommentar.py") == ["mittel"]
