"""Zusammenfassung des JUnit-XML im GitHub-Actions-Lauf (#537)."""

import importlib.util
from pathlib import Path

_PFAD = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "junit_zusammenfassung.py"
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
    assert "grün 1 · rot 2 · übersprungen 1 · Laufzeit 42 s" in text
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
