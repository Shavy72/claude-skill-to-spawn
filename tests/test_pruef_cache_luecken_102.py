"""Prüf-Cache (#102, Fix-Runde 1): kein Grün aus dem Cache, wenn Versteckes oder Umgebung abweicht.

Nachstellung der Prüfer-Befunde: ein per ``.git/info/exclude`` versteckter roter Test, eine
versteckte ``tests/conftest.py`` und eine Umgebungsvariable, die ein Test liest. Jeweils erst ein
grüner Lauf (wird gemerkt), dann die Abweichung — der zweite Lauf muss echt prüfen (rot).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_pruef_tuer_3b import _git, _probe_py, _tuer, _zeile

from to_spawn.pruef import cache


def _gruen_gemerkt(probe: Path) -> None:
    zeile = _zeile(_tuer(probe, "base", "HEAD"))
    assert (zeile["urteil"], zeile["cache_treffer"]) == ("grün", False)
    assert _zeile(_tuer(probe, "base", "HEAD"))["cache_treffer"] is True


def _verstecke(probe: Path, pfad: str, text: str) -> None:
    (probe / pfad).write_text(text, encoding="utf-8")
    with (probe / ".git" / "info" / "exclude").open("a", encoding="utf-8") as datei:
        datei.write(f"\n{pfad}\n")


def test_versteckter_roter_test_ist_kein_treffer(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py")
    _gruen_gemerkt(probe)
    _verstecke(probe, "tests/test_versteckt.py", "def test_rot():\n    assert False\n")
    zeile = _zeile(_tuer(probe, "base", "HEAD"))
    assert (zeile["urteil"], zeile["cache_treffer"]) == ("rot", False)


def test_versteckte_conftest_ist_kein_treffer(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py")
    _gruen_gemerkt(probe)
    _verstecke(probe, "tests/conftest.py", "raise RuntimeError('Sammlung bricht ab')\n")
    zeile = _zeile(_tuer(probe, "base", "HEAD"))
    assert zeile["cache_treffer"] is False
    assert zeile["urteil"] != "grün"


def test_geaenderte_umgebungsvariable_ist_kein_treffer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FALL_102", raising=False)
    probe = _probe_py(tmp_path / "py")
    (probe / "tests" / "test_rechnen.py").write_text(
        "import os\n\ndef test_umgebung():\n    assert os.environ.get('FALL_102') != 'rot'\n",
        encoding="utf-8",
    )
    _git(probe, "commit", "-qam", "liest Umgebung")
    _gruen_gemerkt(probe)
    monkeypatch.setenv("FALL_102", "rot")
    zeile = _zeile(_tuer(probe, "base", "HEAD"))
    assert (zeile["urteil"], zeile["cache_treffer"]) == ("rot", False)


def test_fluechtige_variable_und_werkzeug_ordner_lassen_schluessel_gleich(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = _probe_py(tmp_path / "py")
    vorher = cache.schluessel(probe, "base", "HEAD")
    assert vorher is not None
    monkeypatch.setenv("PWD", os.fspath(tmp_path))
    monkeypatch.setenv("SHLVL", "7")
    (probe / "tests" / "__pycache__").mkdir()
    (probe / "tests" / "__pycache__" / "x.pyc").write_bytes(b"egal")
    (probe / ".git" / "info" / "exclude").write_text("__pycache__/\n", encoding="utf-8")
    assert cache.schluessel(probe, "base", "HEAD") == vorher
