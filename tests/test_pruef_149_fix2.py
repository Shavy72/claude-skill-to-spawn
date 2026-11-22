"""Fix-Runde 2 zu #149: Test-Kennung im Namens-Beleg eindeutig, Zahl der Tests im Beleg geprüft.

JUnit-XML von pytest kennt im ``classname`` nur Punkte: ``tests/a.b/test_c.py`` und
``tests/a/b/test_c.py`` (mit ``--import-mode=importlib``) fielen zur selben Kennung zusammen. Ein
roter Test ließ sich so im zweiten vollen Lauf still abwählen, sein grüner Zwilling belegte ihn.
Alles läuft echt: CLI, git, pytest im frischen Probe-Repo unter ``tmp_path``; ein ``conftest.py``
zählt die pytest-Läufe (1 = erster voller Lauf, 2 = Einzel-Wiederholung ``--lf``, 3 = zweiter
voller Lauf).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import test_pruef_tuer_3b as tuer
import test_pruef_wackler_3b as wackler_3b

_ZAEHL_CONFTEST = (
    "import pathlib\nK = pathlib.Path({zaehler})\n\n"
    "def pytest_configure(config):\n    K.write_text(str(int(K.read_text()) + 1))\n\n"
)
_ABWAHL_L3 = (
    "def pytest_collection_modifyitems(config, items):\n"
    "    if int(K.read_text()) >= 3:\n"
    "        items[:] = [i for i in items if i.nodeid != 'tests/a.b/test_c.py::test_x']\n"
)
_ROT_IN_L1 = "import pathlib\n\ndef test_x():\n    assert int(pathlib.Path({zaehler}).read_text()) >= 2\n"
_GRUEN = "def test_x():\n    pass\n"


def _zwillinge(wurzel: Path, rot_text: str) -> None:
    """Zwei Testdateien, deren JUnit-``classname`` gleich ist: ``a.b/`` (rot) und ``a/b/`` (grün)."""
    (wurzel / "tests" / "a.b").mkdir(parents=True, exist_ok=True)
    (wurzel / "tests" / "a" / "b").mkdir(parents=True, exist_ok=True)
    (wurzel / "tests" / "a.b" / "test_c.py").write_text(rot_text, encoding="utf-8")
    (wurzel / "tests" / "a" / "b" / "test_c.py").write_text(_GRUEN, encoding="utf-8")


def test_zwilling_mit_gleicher_kennung_belegt_abgewaehlten_roten_nicht(tmp_path: Path) -> None:
    """Ganze Tür: roter Test im 3. Lauf abgewählt, grüner Zwilling darf ihn nicht belegen."""
    probe = wackler_3b._probe_mehrere(tmp_path, "def test_0gruen():\n    pass\n", "addopts = --import-mode=importlib\n")
    zaehler = repr(str(tmp_path / "zaehler.txt"))
    _zwillinge(probe, _ROT_IN_L1.replace("{zaehler}", zaehler))
    (probe / "conftest.py").write_text((_ZAEHL_CONFTEST + _ABWAHL_L3).replace("{zaehler}", zaehler), encoding="utf-8")
    tuer._git(probe, "add", "-A")
    tuer._git(probe, "commit", "-q", "--amend", "-m", "base")
    tuer._git(probe, "tag", "-f", "base")
    erg = tuer._tuer(probe, "base", "HEAD")
    zeile = tuer._zeile(erg)
    assert (tmp_path / "zaehler.txt").read_text(encoding="utf-8") == "3", zeile
    assert erg.returncode != 0, zeile
    assert zeile["urteil"] != "wackler", zeile


def _beleg(tmp_path: Path, *zusatz: str) -> Path:
    from to_spawn.pruef.adapter_py import ADAPTER

    beleg = tmp_path / "voll.xml"
    befehl = ADAPTER.mit_beleg(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--import-mode=importlib", *zusatz],
        beleg,
    )
    subprocess.run(befehl, cwd=tmp_path, capture_output=True, check=False)
    return beleg


def test_zwilling_im_beleg_belegt_den_anderen_nicht(tmp_path: Path) -> None:
    """Echter pytest-Lauf: nur der grüne Zwilling lief, der abgewählte rote ist nicht belegt."""
    from to_spawn.pruef.adapter_py import ADAPTER

    _zwillinge(tmp_path, "def test_x():\n    assert False\n")
    beleg = _beleg(tmp_path, "tests", "--deselect", "tests/a.b/test_c.py::test_x")
    assert ADAPTER.alle_bestanden(beleg, {"tests/a/b/test_c.py::test_x"})
    assert not ADAPTER.alle_bestanden(beleg, {"tests/a.b/test_c.py::test_x"})


def test_weniger_tests_im_beleg_als_im_ersten_lauf_belegt_nicht(tmp_path: Path) -> None:
    """Zählung: stehen im Beleg weniger ``testcase`` als der erste Lauf zählte, ist nichts belegt."""
    from to_spawn.pruef.adapter_py import ADAPTER

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_r():\n    pass\n\ndef test_s():\n    pass\n")
    beleg = _beleg(tmp_path, "tests", "--deselect", "tests/test_a.py::test_s")
    assert ADAPTER.alle_bestanden(beleg, {"tests/test_a.py::test_r"}, 1)
    assert not ADAPTER.alle_bestanden(beleg, {"tests/test_a.py::test_r"}, 2)
    assert not ADAPTER.alle_bestanden(beleg, {"tests/test_a.py::test_r"}, 0)
