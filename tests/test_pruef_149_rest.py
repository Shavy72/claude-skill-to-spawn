"""Härtung Wackler/Voll-Lauf, Rest von #149 (L3 3b-4 Folge, zählt für M12).

Die schnelle Auswahl und die Wackler-Wiederholung dürfen nie Grün melden, wo der volle Lauf Rot
findet. Alles läuft echt: CLI, git, pytest im frischen Probe-Repo unter ``tmp_path``. Der
Wackel-Test zählt seine Läufe in einer Datei außerhalb des Repos (Lauf 1 = erster Lauf, Lauf 2 =
Einzel-Wiederholung ``--lf``, Lauf 3 = zweiter voller Lauf).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import test_pruef_tuer_3b as tuer
import test_pruef_wackler_3b as wackler_3b

_GRUEN = "def test_0gruen():\n    pass\n\n"
_ZAEHLE = "    z = pathlib.Path({zaehler}); lauf = int(z.read_text()) + 1\n    z.write_text(str(lauf))\n"


def _urteil(probe: Path) -> tuple[int, dict]:
    erg = tuer._tuer(probe, "base", "HEAD")
    return erg.returncode, tuer._zeile(erg)


# --- Befund 1 + 4: der zweite volle Lauf wird ausgezählt, nicht nur sein Exit-Code -------------

_VOLL_SKIP = (
    "import pytest\n\n" + _GRUEN + "def test_1wackel():\n" + _ZAEHLE + "    if lauf >= 3:\n"
    "        pytest.skip('im vollen Lauf weg')\n"
    "    assert lauf >= 2\n"
)
_VOLL_AUSSTIEG = _VOLL_SKIP.replace("pytest.skip('im vollen Lauf weg')", "pytest.exit('Ausstieg', returncode=0)")
_WACKEL = _GRUEN + "def test_1wackel():\n" + _ZAEHLE + "    assert lauf >= 2\n"
#: Abwahl im vollen Lauf: beim Sammeln von Lauf 3 steht der Zähler auf 2.
_ABWAHL_CONFTEST = (
    "import pathlib\n\n"
    "def pytest_collection_modifyitems(config, items):\n"
    "    if int(pathlib.Path({zaehler}).read_text()) >= 2:\n"
    "        weg = [i for i in items if 'wackel' in i.name]\n"
    "        items[:] = [i for i in items if i not in weg]\n"
    "{melden}"
)


@pytest.mark.parametrize("tests_text", [_VOLL_SKIP, _VOLL_AUSSTIEG], ids=["skip", "ausstieg"])
def test_voller_lauf_exit_0_ohne_gruenen_wackler_bleibt_rot(tmp_path: Path, tests_text: str) -> None:
    probe = wackler_3b._probe_mehrere(tmp_path, tests_text)
    code, zeile = _urteil(probe)
    assert (tmp_path / "zaehler.txt").read_text(encoding="utf-8") == "3"
    assert (code, zeile["urteil"], zeile["wackler"]) == (1, "rot", [])


@pytest.mark.parametrize("gemeldet", [True, False], ids=["deselected", "still"])
def test_abwahl_im_vollen_lauf_bleibt_rot(tmp_path: Path, gemeldet: bool) -> None:
    probe = wackler_3b._probe_mehrere(tmp_path, _WACKEL)
    melden = "        config.hook.pytest_deselected(items=weg)\n" if gemeldet else ""
    zaehler = repr(str(tmp_path / "zaehler.txt"))
    (probe / "conftest.py").write_text(
        _ABWAHL_CONFTEST.replace("{zaehler}", zaehler).replace("{melden}", melden), encoding="utf-8"
    )
    tuer._git(probe, "add", "-A")
    tuer._git(probe, "commit", "-q", "--amend", "-m", "base")
    tuer._git(probe, "tag", "-f", "base")
    code, zeile = _urteil(probe)
    assert (tmp_path / "zaehler.txt").read_text(encoding="utf-8") == "2"
    assert (code, zeile["urteil"], zeile["wackler"]) == (1, "rot", [])


def test_echter_wackler_neben_skip_bleibt_wackler(tmp_path: Path) -> None:
    """Gegenprobe: ein Skip, der schon im ersten Lauf da war, macht den Wackler nicht rot."""
    probe = wackler_3b._probe_mehrere(
        tmp_path, "import pytest\n\ndef test_0weg():\n    pytest.skip('immer')\n\n" + _WACKEL
    )
    code, zeile = _urteil(probe)
    assert (code, zeile["urteil"]) == (0, "wackler"), zeile
    assert [w["test"] for w in zeile["wackler"]] == ["tests/test_rechnen.py::test_1wackel"]


# --- Befund 2: eine gefälschte Schlusszeile täuscht den Zähler nicht ----------------------------

_FAELSCHER = (
    "import atexit\nimport pytest\n\n"
    "def _faelschen():\n"
    "    lauf = pathlib.Path({zaehler}).read_text()\n"
    "    print({'2': '1 passed in 0.01s', '3': '2 passed in 0.01s'}.get(lauf, ''))\n\n"
    "atexit.register(_faelschen)\n\n" + _GRUEN + "def test_1wackel():\n" + _ZAEHLE + "    if lauf >= 2:\n"
    "        pytest.skip('weg')\n"
    "    assert False\n"
)


def test_gefaelschte_schlusszeile_per_atexit_macht_kein_wackler(tmp_path: Path) -> None:
    probe = wackler_3b._probe_mehrere(tmp_path, _FAELSCHER)
    code, zeile = _urteil(probe)
    assert (code, zeile["urteil"], zeile["wackler"]) == (1, "rot", [])


def test_zwei_schlusszeilen_sind_unbelegt() -> None:
    from to_spawn.pruef.adapter_py import ADAPTER

    echt_dann_falsch = ".s  [2/2]\n1 passed, 1 skipped in 0.01s\n2 passed in 0.01s\n"
    assert ADAPTER.bestanden(echt_dann_falsch) is None


# --- Befund 3: ``addopts = -q`` nimmt die Schlusszeile nicht weg --------------------------------


@pytest.mark.parametrize("ini", ["addopts = -q\n", "addopts = -qq\n", "addopts = -v\n"])
def test_wackler_mit_verbosity_in_addopts_bleibt_wackler(tmp_path: Path, ini: str) -> None:
    probe = wackler_3b._probe_mehrere(tmp_path, _WACKEL, ini)
    code, zeile = _urteil(probe)
    assert (code, zeile["urteil"]) == (0, "wackler"), zeile
    assert [w["test"] for w in zeile["wackler"]] == ["tests/test_rechnen.py::test_1wackel"]


# --- Altlast: geerbtes PYTEST_ADDOPTS/PYTEST_PLUGINS ändert nicht, was geprüft wird -------------

_ROT = _GRUEN + "def test_1rot():\n    assert False\n"


@pytest.mark.parametrize(
    "addopts",
    ["-k test_0gruen", "--deselect tests/test_rechnen.py::test_1rot", "--co", "-p ausstieg_149"],
)
def test_geerbtes_pytest_addopts_macht_rot_nie_gruen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, addopts: str
) -> None:
    probe = wackler_3b._probe_mehrere(tmp_path, _ROT)
    _ausstieg_plugin(tmp_path, monkeypatch)
    monkeypatch.setenv("PYTEST_ADDOPTS", addopts)
    code, zeile = _urteil(probe)
    assert (code, zeile["urteil"]) == (1, "rot"), zeile


def test_geerbtes_pytest_plugins_macht_rot_nie_gruen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe = wackler_3b._probe_mehrere(tmp_path, _ROT)
    _ausstieg_plugin(tmp_path, monkeypatch)
    monkeypatch.setenv("PYTEST_PLUGINS", "ausstieg_149")
    code, zeile = _urteil(probe)
    assert (code, zeile["urteil"]) == (1, "rot"), zeile


def _ausstieg_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Plugin außerhalb des Repos, das pytest vor jedem Test mit Exit 0 beendet."""
    ordner = tmp_path / "plugin"
    ordner.mkdir()
    (ordner / "ausstieg_149.py").write_text(
        "import pytest\n\ndef pytest_sessionstart(session):\n    pytest.exit('weg', returncode=0)\n",
        encoding="utf-8",
    )
    alt = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", str(ordner) + (os.pathsep + alt if alt else ""))
