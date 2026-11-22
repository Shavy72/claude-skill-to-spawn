"""Fix-Runde 1 zu #149: der zweite volle Lauf belegt jeden vorher roten Test mit Namen.

Gezählt (gelaufen/bestanden) ließ sich der Beleg ausgleichen oder entfiel ganz, wenn die Ausgabe
des ersten Laufs keine eindeutige Schlusszeile hatte. Alles läuft echt: CLI, git, pytest im
frischen Probe-Repo unter ``tmp_path``. Ein ``conftest.py`` zählt die pytest-Läufe (1 = erster
voller Lauf, 2 = Einzel-Wiederholung ``--lf``, 3 = zweiter voller Lauf).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import test_pruef_tuer_3b as tuer
import test_pruef_wackler_3b as wackler_3b

_KOPF = "import pytest\nK = pathlib.Path({zaehler})\n\ndef lauf():\n    return int(K.read_text())\n\n"
_ZAEHL_CONFTEST = (
    "import pathlib\nK = pathlib.Path({zaehler})\n\n"
    "def pytest_configure(config):\n    K.write_text(str(int(K.read_text()) + 1))\n\n"
)
_GRUEN = "def test_0gruen():\n    pass\n\n"
_WACKEL = "def test_1wackel():\n    assert lauf() >= 2\n\n"
#: Fehlerausgabe mit einer pytest-ähnlichen Schlusszeile (wie bei pytester/inneren Läufen).
_WACKEL_INNEN = "def test_1wackel():\n    print('1 failed, 3 passed in 0.12s')\n    assert lauf() >= 2\n\n"
_SKIP_IMMER = "def test_0weg():\n    pytest.skip('immer')\n\n"


def _urteil(tmp_path: Path, tests_text: str, conftest_zusatz: str = "") -> tuple[int, dict]:
    probe = wackler_3b._probe_mehrere(tmp_path, _KOPF + tests_text)
    zaehler = repr(str(tmp_path / "zaehler.txt"))
    (probe / "conftest.py").write_text(
        (_ZAEHL_CONFTEST + conftest_zusatz).replace("{zaehler}", zaehler), encoding="utf-8"
    )
    tuer._git(probe, "add", "-A")
    tuer._git(probe, "commit", "-q", "--amend", "-m", "base")
    tuer._git(probe, "tag", "-f", "base")
    erg = tuer._tuer(probe, "base", "HEAD")
    return erg.returncode, tuer._zeile(erg)


# --- H1: ein anderer Test gleicht die Zahl der bestandenen aus -----------------------------------


@pytest.mark.parametrize("weg", ["skip", "xfail"])
def test_ausgleich_ueber_anderen_test_bleibt_rot(tmp_path: Path, weg: str) -> None:
    tests = (
        _GRUEN
        + f"def test_1rot():\n    if lauf() == 3:\n        pytest.{weg}('l3')\n    assert lauf() == 2\n\n"
        + "def test_2s():\n    if lauf() == 1:\n        pytest.skip('l1')\n"
    )
    code, zeile = _urteil(tmp_path, tests)
    assert (tmp_path / "zaehler.txt").read_text(encoding="utf-8") == "3"
    assert (code, zeile["urteil"], zeile["wackler"]) == (1, "rot", [])


# --- H2: erster Lauf ohne eindeutige Schlusszeile, roter Test im vollen Lauf still abgewählt -----

_ABWAHL_L3 = (
    "def pytest_collection_modifyitems(config, items):\n"
    "    if int(K.read_text()) >= 3:\n"
    "        items[:] = [i for i in items if 'wackel' not in i.name]\n"
)


def test_innere_schlusszeile_plus_stille_abwahl_bleibt_rot(tmp_path: Path) -> None:
    code, zeile = _urteil(tmp_path, _GRUEN + _WACKEL_INNEN, _ABWAHL_L3)
    assert (tmp_path / "zaehler.txt").read_text(encoding="utf-8") == "3"
    assert (code, zeile["urteil"], zeile["wackler"]) == (1, "rot", [])


# --- Fehlt der Namens-Beleg oder ist er unlesbar, bleibt es rot ----------------------------------

_BELEG_KAPUTT_L3 = (
    "def pytest_unconfigure(config):\n"
    "    pfad = getattr(config.option, 'xmlpath', None)\n"
    "    if int(K.read_text()) >= 3 and pfad:\n"
    "        pathlib.Path(pfad).write_text('<kaputt', encoding='utf-8')\n"
)


_BELEG_WEG_L3 = _BELEG_KAPUTT_L3.replace(
    "pathlib.Path(pfad).write_text('<kaputt', encoding='utf-8')", "pathlib.Path(pfad).unlink()"
)


@pytest.mark.parametrize("conftest", [_BELEG_KAPUTT_L3, _BELEG_WEG_L3], ids=["kaputt", "fehlt"])
def test_unlesbarer_namens_beleg_bleibt_rot(tmp_path: Path, conftest: str) -> None:
    code, zeile = _urteil(tmp_path, _GRUEN + _WACKEL, conftest)
    assert (tmp_path / "zaehler.txt").read_text(encoding="utf-8") == "3"
    assert (code, zeile["urteil"], zeile["wackler"]) == (1, "rot", [])


def test_beleg_mit_aehnlichem_namen_belegt_nicht(tmp_path: Path) -> None:
    """Echter pytest-Lauf schreibt das JUnit-XML; nur der genaue Name zählt als bestanden."""
    import subprocess
    import sys

    from to_spawn.pruef.adapter_py import ADAPTER

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_r():\n    pass\n", encoding="utf-8")
    (tmp_path / "tests" / "test_b.py").write_text(
        "import pytest\n\n@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_p(n):\n    assert n == 1\n\ndef test_r():\n    assert False\n",
        encoding="utf-8",
    )
    beleg = tmp_path / "voll.xml"
    befehl = ADAPTER.mit_beleg([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], beleg)
    subprocess.run(befehl, cwd=tmp_path, capture_output=True, check=False)
    assert ADAPTER.alle_bestanden(beleg, {"tests/test_a.py::test_r", "tests/test_b.py::test_p[1]"})
    assert not ADAPTER.alle_bestanden(beleg, {"tests/test_b.py::test_r"})
    assert not ADAPTER.alle_bestanden(beleg, {"tests/test_b.py::test_p[2]"})
    assert not ADAPTER.alle_bestanden(beleg, {"tests/test_c.py::test_r"})


# --- Rückfall ohne Namens-Beleg (z. B. Adapter ohne ``Namentlich``): Rechenlogik ----------------


def test_rueckfall_ohne_namens_beleg_zaehlt() -> None:
    from to_spawn.pruef import wackler
    from to_spawn.pruef.adapter_py import ADAPTER

    erst = ".sF  [3/3]\n1 failed, 1 passed, 1 skipped in 0.1s"
    ohne_schluss = ".sF  [3/3]\n"
    # Erster Lauf mit Schlusszeile: gelaufen gleich, bestanden == vorher + rot.
    assert wackler._voll_belegt(ADAPTER, erst, 1, "..s  [3/3]\n2 passed, 1 skipped in 0.1s")
    assert not wackler._voll_belegt(ADAPTER, erst, 1, ".ss  [3/3]\n1 passed, 2 skipped in 0.1s")
    assert not wackler._voll_belegt(ADAPTER, erst, 1, "..  [2/2]\n2 passed in 0.1s")
    # Ohne Schlusszeile im ersten Lauf: nur ein Lauf, in dem jeder gelaufene Test bestand.
    assert wackler._voll_belegt(ADAPTER, ohne_schluss, 1, "...  [3/3]\n3 passed in 0.1s")
    assert not wackler._voll_belegt(ADAPTER, ohne_schluss, 1, "..s  [3/3]\n2 passed, 1 skipped in 0.1s")
    # Voller Lauf ohne Zählung ist nie belegt.
    assert not wackler._voll_belegt(ADAPTER, erst, 1, "...\n")


# --- Gegenproben: echte Wackler bleiben Wackler, Grün bleibt Grün --------------------------------


@pytest.mark.parametrize(
    "tests",
    [_GRUEN + _WACKEL, _SKIP_IMMER + _GRUEN + _WACKEL, _SKIP_IMMER + _GRUEN + _WACKEL_INNEN],
    ids=["schlicht", "skip-im-ersten-lauf", "innere-schlusszeile-plus-skip"],
)
def test_echter_wackler_bleibt_wackler(tmp_path: Path, tests: str) -> None:
    code, zeile = _urteil(tmp_path, tests)
    assert (code, zeile["urteil"]) == (0, "wackler"), zeile
    assert [w["test"] for w in zeile["wackler"]] == ["tests/test_rechnen.py::test_1wackel"]


def test_sauber_gruen_bleibt_gruen(tmp_path: Path) -> None:
    code, zeile = _urteil(tmp_path, _SKIP_IMMER + _GRUEN)
    assert (tmp_path / "zaehler.txt").read_text(encoding="utf-8") == "1"
    assert (code, zeile["urteil"]) == (0, "grün"), zeile
