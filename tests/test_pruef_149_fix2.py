"""Fix-Runde 2 & Nach-Prüfer 2 zu #149: eindeutige Knoten-Kennung im Namens-Beleg, auch bei Import.

JUnit-XML von pytest kennt im ``classname`` nur Punkte (``tests/a.b/test_c.py`` und
``tests/a/b/test_c.py`` fallen zusammen), und ``xunit1``-``file`` nennt die Definitionsdatei (ein
Zwilling, der die Testfunktion aus der roten Datei importiert, teilt auch ``file``). Der Bericht
muss deshalb die echte pytest-``nodeid`` je bestandenem Test führen.
Alles läuft echt: CLI, git, pytest im frischen Probe-Repo unter ``tmp_path``; ein ``conftest.py``
zählt die pytest-Läufe (1 = erster voller Lauf, 2 = Einzel-Wiederholung ``--lf``, 3 = zweiter
voller Lauf).
"""

from __future__ import annotations

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
#: Nach-Prüfer 2: im 1. Lauf läuft ``test_fuell`` nicht, im 3. Lauf ersetzt er das abgewählte Original
#: (Zahl der gelaufenen Tests bleibt gleich).
_TAUSCH_L3 = (
    "def pytest_collection_modifyitems(config, items):\n"
    "    nr = int(K.read_text())\n"
    "    if nr == 1:\n"
    "        items[:] = [i for i in items if i.name != 'test_fuell']\n"
    "    elif nr >= 3:\n"
    "        items[:] = [i for i in items if i.nodeid != 'tests/a.b/test_c.py::test_x']\n"
)
_ROT_IN_L1 = "import pathlib\n\ndef test_x():\n    assert int(pathlib.Path({zaehler}).read_text()) >= 2\n"
_ROT_IMPORTIERT_ORIG = (
    "import pathlib\n\n"
    "def test_x(request):\n"
    "    if request.node.nodeid == 'tests/a.b/test_c.py::test_x':\n"
    "        assert int(pathlib.Path({zaehler}).read_text()) >= 2\n"
)
_IMPORT_ZWILLING = (
    "import importlib.util\n"
    "import pathlib\n\n"
    "_spec = importlib.util.spec_from_file_location(\n"
    "    'orig_149', pathlib.Path(__file__).resolve().parents[2] / 'a.b' / 'test_c.py'\n"
    ")\n"
    "assert _spec is not None and _spec.loader is not None\n"
    "_mod = importlib.util.module_from_spec(_spec)\n"
    "_spec.loader.exec_module(_mod)\n"
    "test_x = _mod.test_x\n"
)
_GRUEN = "def test_x():\n    pass\n"


def _zwillinge(wurzel: Path, rot_text: str, zwilling_text: str = _GRUEN) -> None:
    """Zwei Testdateien, deren JUnit-``classname`` gleich ist: ``a.b/`` (rot) und ``a/b/`` (grün)."""
    (wurzel / "tests" / "a.b").mkdir(parents=True, exist_ok=True)
    (wurzel / "tests" / "a" / "b").mkdir(parents=True, exist_ok=True)
    (wurzel / "tests" / "a.b" / "test_c.py").write_text(rot_text, encoding="utf-8")
    (wurzel / "tests" / "a" / "b" / "test_c.py").write_text(zwilling_text, encoding="utf-8")


def _probe_mit_zwillingen(tmp_path: Path, rot_text: str, zwilling_text: str, conftest_zusatz: str) -> Path:
    probe = wackler_3b._probe_mehrere(
        tmp_path,
        "def test_0gruen():\n    pass\n\ndef test_fuell():\n    pass\n",
        "addopts = --import-mode=importlib\n",
    )
    zaehler = repr(str(tmp_path / "zaehler.txt"))
    _zwillinge(probe, rot_text.replace("{zaehler}", zaehler), zwilling_text)
    (probe / "conftest.py").write_text(
        (_ZAEHL_CONFTEST + conftest_zusatz).replace("{zaehler}", zaehler), encoding="utf-8"
    )
    tuer._git(probe, "add", "-A")
    tuer._git(probe, "commit", "-q", "--amend", "-m", "base")
    tuer._git(probe, "tag", "-f", "base")
    return probe


def test_zwilling_mit_gleicher_kennung_belegt_abgewaehlten_roten_nicht(tmp_path: Path) -> None:
    """Ganze Tür: roter Test im 3. Lauf abgewählt, grüner Zwilling darf ihn nicht belegen."""
    probe = _probe_mit_zwillingen(tmp_path, _ROT_IN_L1, _GRUEN, _ABWAHL_L3)
    erg = tuer._tuer(probe, "base", "HEAD")
    zeile = tuer._zeile(erg)
    assert (tmp_path / "zaehler.txt").read_text(encoding="utf-8") == "3", zeile
    assert erg.returncode != 0, zeile
    assert zeile["urteil"] != "wackler", zeile


def test_importierter_zwilling_mit_fuelltest_belegt_abgewaehlten_roten_nicht(tmp_path: Path) -> None:
    """Nach-Prüfer 2: Zwilling importiert die Testfunktion aus der roten Datei + Füll-Test hält Zahl gleich."""
    probe = _probe_mit_zwillingen(tmp_path, _ROT_IMPORTIERT_ORIG, _IMPORT_ZWILLING, _TAUSCH_L3)
    erg = tuer._tuer(probe, "base", "HEAD")
    zeile = tuer._zeile(erg)
    assert (tmp_path / "zaehler.txt").read_text(encoding="utf-8") == "3", zeile
    assert (erg.returncode, zeile["urteil"], zeile["wackler"]) == (1, "rot", []), zeile


def test_echter_wackler_neben_importiertem_zwilling_bleibt_wackler(tmp_path: Path) -> None:
    """Gegenprobe: laufen beide Zwillinge im 3. Lauf regulär durch, bleibt der echte Wackler Wackler."""
    probe = _probe_mit_zwillingen(tmp_path, _ROT_IMPORTIERT_ORIG, _IMPORT_ZWILLING, "")
    erg = tuer._tuer(probe, "base", "HEAD")
    zeile = tuer._zeile(erg)
    assert (tmp_path / "zaehler.txt").read_text(encoding="utf-8") == "3", zeile
    assert (erg.returncode, zeile["urteil"]) == (0, "wackler"), zeile
    assert [w["test"] for w in zeile["wackler"]] == ["tests/a.b/test_c.py::test_x"]


def test_zwilling_im_beleg_belegt_den_anderen_nicht(tmp_path: Path) -> None:
    """Echter Adapter-Lauf: nur der grüne (auch importierte) Zwilling lief, der abgewählte rote fehlt."""
    from to_spawn.pruef import ausfuehren
    from to_spawn.pruef.adapter_py import ADAPTER

    _zwillinge(tmp_path, "def test_x():\n    pass\n", _IMPORT_ZWILLING)
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --import-mode=importlib\n", encoding="utf-8")
    plan = ADAPTER.voll(tmp_path)
    assert not plan.fehler, plan.fehler
    befehl = [*plan.befehl, "--deselect", "tests/a.b/test_c.py::test_x"]
    lauf = ausfuehren.lauf(befehl, tmp_path)
    bericht = ADAPTER.bericht(befehl, lauf.ausgabe)
    assert bericht is not None and bericht.bestanden_namen is not None
    assert "tests/a/b/test_c.py::test_x" in bericht.bestanden_namen
    assert "tests/a.b/test_c.py::test_x" not in bericht.bestanden_namen


def test_weniger_tests_im_beleg_als_im_ersten_lauf_belegt_nicht(tmp_path: Path) -> None:
    """Zählung: stehen im Bericht weniger Tests als im ersten Lauf, ergibt ``bericht`` die echte Zahl."""
    from to_spawn.pruef import ausfuehren
    from to_spawn.pruef.adapter_py import ADAPTER

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_r():\n    pass\n\ndef test_s():\n    pass\n")
    plan = ADAPTER.voll(tmp_path)
    assert not plan.fehler, plan.fehler
    befehl = [*plan.befehl, "--deselect", "tests/test_a.py::test_s"]
    lauf = ausfuehren.lauf(befehl, tmp_path)
    bericht = ADAPTER.bericht(befehl, lauf.ausgabe)
    assert bericht is not None
    assert bericht.gelaufen == 1
    assert bericht.im_bericht == 1
    assert bericht.bestanden_namen == frozenset({"tests/test_a.py::test_r"})
