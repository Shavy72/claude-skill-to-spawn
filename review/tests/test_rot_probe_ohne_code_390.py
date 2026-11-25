"""Skill #390: Tests ohne Code-Aufruf (z. B. reine Testdaten-/Zähl-Tests) → nicht prüfbar statt rot.

Reine Test-Diffs ohne Code-Unterschied zur Basis deckt bereits #401 ab; hier wird zusätzlich
der Fall aus Pull-Request 374 (Basis 8841b6a, Zähl-Test neben geändertem Code) geprüft.
"""

from __future__ import annotations

import json
from pathlib import Path

from review_hilfen import git, lauf

HINWEIS_OHNE_CODE = "nicht prüfbar: Test ohne Code-Aufruf"
PFAD = "import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent.parent))\n"


def probe(repo: Path, *args: str) -> dict:
    ergebnis = lauf("rot_probe.py", "--repo", str(repo), *args)
    assert ergebnis.returncode == 0, ergebnis.stderr
    return json.loads(ergebnis.stdout)


def befunde(daten: dict) -> list[tuple[str, str]]:
    return [(b["datei"], b["schwere"]) for b in daten["befunde"]]


def _basis_mit_testdaten(repo: Path) -> None:
    (repo / "tests").mkdir()
    (repo / "tests" / "conftest.py").write_text(
        PFAD + "\nimport pytest\n\n\n@pytest.fixture\ndef wert():\n    return 1\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_basis_390.py").write_text(
        'FAELLE = {"a": 1, "b": 2}\n\n\ndef test_basis_ok():\n    assert len(FAELLE) == 2\n',
        encoding="utf-8",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "basis mit testdaten")


def test_zaehl_test_ohne_code_aufruf_neben_code_diff_ist_nicht_pruefbar(repo: Path) -> None:
    """Fall Pull-Request 374 (Basis 8841b6a): Code geändert + Zähl-Test nur auf Testdaten → nicht prüfbar."""
    _basis_mit_testdaten(repo)
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "tests" / "test_zaehl_390.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
        "from test_basis_390 import FAELLE as BASIS\n"
        "from rechnen import eins\n\n"
        'GRUPPE = frozenset({"a"})\n'
        'ALLE = {**BASIS, "c": 3}\n\n\n'
        "def test_faelle_fest_je_gruppe():\n"
        "    assert len(BASIS) == 2\n"
        "    assert len(ALLE) == 3\n"
        "    assert GRUPPE <= set(ALLE)\n",
        encoding="utf-8",
    )
    daten = probe(repo, "--files", "rechnen.py", "tests/test_zaehl_390.py")
    assert daten["befunde"] == [], daten
    assert HINWEIS_OHNE_CODE in daten["hinweis"], daten
    assert "test_faelle_fest_je_gruppe" in daten["hinweis"], daten


def test_gemischt_zaehl_test_und_echter_gruener_test_meldet_nur_echten(repo: Path) -> None:
    """Zähl-Test neben grünem Code-Test in derselben Datei → nur der Code-Test wird gemeldet."""
    (repo / "rechnen.py").write_text("def eins():\n    return 1\n\n\ndef zwei():\n    return 2\n", encoding="utf-8")
    (repo / "test_misch_390.py").write_text(
        "from rechnen import eins\n\n"
        "FAELLE = [1, 2]\n\n\n"
        "def test_faelle_anzahl():\n"
        "    assert len(FAELLE) == 2\n\n\n"
        "def test_eins_alt():\n"
        "    assert eins() == 1\n",
        encoding="utf-8",
    )
    daten = probe(repo, "--files", "rechnen.py", "test_misch_390.py")
    assert befunde(daten) == [("test_misch_390.py", "hoch")], daten
    text = daten["befunde"][0]["text"]
    assert "test_eins_alt" in text and "test_faelle_anzahl" not in text, text
    assert HINWEIS_OHNE_CODE in daten["hinweis"] and "test_faelle_anzahl" in daten["hinweis"], daten


def test_gegenprobe_tautologie_ohne_testdaten_bleibt_befund(repo: Path) -> None:
    """Reine Literal-Tautologie ohne Testdaten-Bezug darf nicht als nicht prüfbar durchrutschen."""
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "test_taut_390.py").write_text(
        "def test_literal():\n    assert 1 == 1\n\n\ndef test_lokal():\n    x = [1, 2]\n    assert len(x) == 2\n",
        encoding="utf-8",
    )
    daten = probe(repo, "--files", "rechnen.py", "test_taut_390.py")
    assert befunde(daten) == [("test_taut_390.py", "hoch")], daten
    assert HINWEIS_OHNE_CODE not in daten["hinweis"], daten


def test_gegenprobe_code_aufruf_ueber_modul_konstante_bleibt_befund(repo: Path) -> None:
    """Modul-Konstante aus Produktiv-Code zählt transitiv als Code-Aufruf → Befund bleibt."""
    (repo / "rechnen.py").write_text("def eins():\n    return 1\n\n\ndef zwei():\n    return 2\n", encoding="utf-8")
    (repo / "test_konst_390.py").write_text(
        "from rechnen import eins\n\nWERT = eins()\n\n\ndef test_wert():\n    assert WERT == 1\n",
        encoding="utf-8",
    )
    daten = probe(repo, "--files", "rechnen.py", "test_konst_390.py")
    assert befunde(daten) == [("test_konst_390.py", "hoch")], daten
    assert HINWEIS_OHNE_CODE not in daten["hinweis"], daten


def test_gegenprobe_werkzeug_mit_testnamen_bleibt_befund(repo: Path) -> None:
    """Werkzeug scripts/test_plaetze.py ist Code (#1454), kein Testmodul → Befund bleibt scharf."""
    (repo / "scripts").mkdir()
    (repo / "scripts" / "test_plaetze.py").write_text("def plaetze():\n    return 1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))\n",
        encoding="utf-8",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "werkzeug")
    (repo / "scripts" / "test_plaetze.py").write_text(
        "def plaetze():\n    return 1\n\n\ndef andere():\n    return 2\n", encoding="utf-8"
    )
    (repo / "tests" / "test_plaetze_alt_390.py").write_text(
        "from test_plaetze import plaetze\n\n\ndef test_alt():\n    assert plaetze() == 1\n",
        encoding="utf-8",
    )
    daten = probe(repo, "--files", "scripts/test_plaetze.py", "tests/test_plaetze_alt_390.py")
    assert befunde(daten) == [("tests/test_plaetze_alt_390.py", "hoch")], daten
    assert HINWEIS_OHNE_CODE not in daten["hinweis"], daten


def test_gegenprobe_test_ohne_assert_bleibt_befund(repo: Path) -> None:
    """Test ohne jedes assert prüft auch Testdaten nicht wirklich → Befund bleibt."""
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "test_ohne_assert_390.py").write_text(
        "FAELLE = [1, 2]\n\n\ndef test_nur_lesen():\n    len(FAELLE)\n",
        encoding="utf-8",
    )
    daten = probe(repo, "--files", "rechnen.py", "test_ohne_assert_390.py")
    assert befunde(daten) == [("test_ohne_assert_390.py", "hoch")], daten
    assert HINWEIS_OHNE_CODE not in daten["hinweis"], daten


def test_gegenprobe_test_mit_fixture_bleibt_befund(repo: Path) -> None:
    """Fixture-Parameter kann Code aus conftest ausführen → vorsichtshalber Befund behalten."""
    _basis_mit_testdaten(repo)
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "tests" / "test_fix_390.py").write_text(
        "FAELLE = [1, 2]\n\n\ndef test_mit_fixture(wert):\n    assert len(FAELLE) == 2\n",
        encoding="utf-8",
    )
    daten = probe(repo, "--files", "rechnen.py", "tests/test_fix_390.py")
    assert befunde(daten) == [("tests/test_fix_390.py", "hoch")], daten
    assert HINWEIS_OHNE_CODE not in daten["hinweis"], daten


def test_reiner_test_commit_mit_base_ohne_code_unterschied_kein_befund(repo: Path) -> None:
    """Fall Pull-Request 374 (Basis 2d356bf, bereits #401): committeter reiner Test-Commit mit --base."""
    (repo / "tests").mkdir()
    (repo / "tests" / "conftest.py").write_text(PFAD, encoding="utf-8")
    (repo / "tests" / "test_alt_390.py").write_text(
        "from rechnen import eins\n\n\ndef test_alt():\n    assert eins() == 1\n",
        encoding="utf-8",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "basis")
    basis = git(repo, "rev-parse", "HEAD").strip()
    (repo / "tests" / "test_alt_390.py").write_text(
        "from rechnen import eins\n\n\ndef test_umbenannt():\n    assert eins() == 1\n",
        encoding="utf-8",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "nur test umbenannt")
    daten = probe(repo, "--base", basis, "--files", "tests/test_alt_390.py")
    assert daten["befunde"] == [], daten
    assert "ausgelassen: nur Tests geändert, keine Aussage" in daten["hinweis"], daten


def test_parametrisierter_und_klassen_daten_test_sind_nicht_pruefbar(repo: Path) -> None:
    """Parametrisierter Test und Klassen-Methode nur auf Testdaten → nicht prüfbar."""
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "test_param_klasse_390.py").write_text(
        "import pytest\n\n"
        'NAMEN = ["a", "b"]\n\n\n'
        '@pytest.mark.parametrize("eintrag", NAMEN)\n'
        "def test_eintrag_nicht_leer(eintrag):\n"
        '    assert eintrag.strip() != ""\n\n\n'
        "class TestDaten:\n"
        "    FAELLE = [1, 2]\n\n"
        "    def test_anzahl(self):\n"
        "        assert len(self.FAELLE) == 2\n",
        encoding="utf-8",
    )
    daten = probe(repo, "--files", "rechnen.py", "test_param_klasse_390.py")
    assert daten["befunde"] == [], daten
    assert HINWEIS_OHNE_CODE in daten["hinweis"], daten


def test_gegenprobe_lokaler_import_stern_und_schatten_bleiben_befund(repo: Path) -> None:
    """Lokaler Produktiv-Import, Stern-Import und lokale Verschattung bleiben scharfe Befunde."""
    (repo / "rechnen.py").write_text("def eins():\n    return 1\n\n\ndef zwei():\n    return 2\n", encoding="utf-8")
    (repo / "test_lokal_imp_390.py").write_text(
        "FAELLE = [1, 2]\n\n\ndef test_lokal_imp():\n    from rechnen import eins\n    assert len(FAELLE) == 2 and eins() == 1\n",
        encoding="utf-8",
    )
    (repo / "test_stern_390.py").write_text(
        "from rechnen import *\n\nFAELLE = [1, 2]\n\n\ndef test_stern():\n    assert len(FAELLE) == 2\n",
        encoding="utf-8",
    )
    (repo / "test_schatten_390.py").write_text(
        "FAELLE = [1, 2]\n\n\ndef test_schatten():\n    FAELLE = [1, 2]\n    assert len(FAELLE) == 2\n",
        encoding="utf-8",
    )
    daten = probe(
        repo,
        "--files",
        "rechnen.py",
        "test_lokal_imp_390.py",
        "test_stern_390.py",
        "test_schatten_390.py",
    )
    assert befunde(daten) == [
        ("test_lokal_imp_390.py", "hoch"),
        ("test_stern_390.py", "hoch"),
        ("test_schatten_390.py", "hoch"),
    ], daten
    assert HINWEIS_OHNE_CODE not in daten["hinweis"], daten
