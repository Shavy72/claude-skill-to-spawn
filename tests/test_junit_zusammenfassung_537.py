"""Zusammenfassung des JUnit-XML im GitHub-Actions-Lauf (#537)."""

import importlib.util
from pathlib import Path

import sys

_ORDNER = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_PFAD = _ORDNER / "junit_zusammenfassung.py"
sys.path.insert(0, str(_ORDNER))  # junit_zusammenfassung importiert auslassungen
import auslassungen  # noqa: E402

_spec = importlib.util.spec_from_file_location("junit_zusammenfassung", _PFAD)
assert _spec and _spec.loader
modul = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(modul)

_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.test_b" name="test_rot"><failure message="x"/></testcase>
  <testcase classname="tests.test_a" name="test_kaputt"><error message="y"/></testcase>
  <testcase classname="tests.test_a" name="test_gruen"/>
  <testcase classname="tests.test_a" name="test_aus"><skipped message="z"/></testcase>
</testsuite></testsuites>
"""


def test_zaehlt_und_listet_rote_sortiert(tmp_path: Path) -> None:
    datei = tmp_path / "junit.xml"
    datei.write_text(_XML, encoding="utf-8")
    text = modul.zusammenfassen(datei, "42", "pytest Linux")
    assert "grün 1 · rot 2 · übersprungen 1 · ausgelassen 0 · Laufzeit 42 s" in text
    a = text.index("`tests.test_a::test_kaputt`")
    b = text.index("`tests.test_b::test_rot`")
    assert a < b


def test_ohne_rote_keine_liste(tmp_path: Path) -> None:
    datei = tmp_path / "junit.xml"
    datei.write_text('<testsuite><testcase classname="c" name="n"/></testsuite>', encoding="utf-8")
    text = modul.zusammenfassen(datei, None, "pytest")
    assert "rot 0" in text and "Laufzeit unbekannt" in text
    assert "Rote Tests" not in text


def test_datei_fehlt(tmp_path: Path) -> None:
    text = modul.zusammenfassen(tmp_path / "fehlt.xml", "1", "pytest")
    assert "Kein JUnit-XML" in text


def test_auslassungen_mit_grund_in_zusammenfassung(tmp_path: Path) -> None:
    liste = tmp_path / "aus.txt"
    liste.write_text("# Kopf\n\ntests/test_x.py::test_a[p]  # Plugin fehlt\n", encoding="utf-8")
    eintraege = auslassungen.lesen(liste)
    assert eintraege == [("tests/test_x.py::test_a[p]", "Plugin fehlt")]
    text = modul.zusammenfassen(tmp_path / "fehlt.xml", None, "pytest", eintraege)
    assert "Ausgelassen (1)" in text and "`tests/test_x.py::test_a[p]` — Plugin fehlt" in text


def test_veraltete_auslassung_wird_erkannt(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_aus.py").write_text("", encoding="utf-8")
    eintraege = [
        ("tests/test_x.py::test_a", ""),
        ("tests/test_x.py::test_weg", ""),  # Datei gesammelt, Test fehlt → veraltet
        ("tests/test_aus.py::test_c", ""),  # ganze Datei auf diesem System übersprungen → ok
        ("tests/test_geloescht.py::test_d", ""),  # Datei weg → veraltet
    ]
    gesammelt = "tests/test_x.py::test_a\ntests/test_x.py::test_b\n\n2 tests collected in 0.1s\n"
    assert auslassungen.fehlende(eintraege, gesammelt, tmp_path) == [
        "tests/test_x.py::test_weg",
        "tests/test_geloescht.py::test_d",
    ]
