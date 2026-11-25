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
    """Echter Adapter-Lauf schreibt den Bericht; nur der genaue Knoten-Name steht in ``bestanden_namen``."""
    from to_spawn.pruef import ausfuehren
    from to_spawn.pruef.adapter_py import ADAPTER

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_r():\n    pass\n", encoding="utf-8")
    (tmp_path / "tests" / "test_b.py").write_text(
        "import pytest\n\n@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_p(n):\n    assert n == 1\n\ndef test_r():\n    assert False\n",
        encoding="utf-8",
    )
    plan = ADAPTER.voll(tmp_path)
    assert not plan.fehler, plan.fehler
    lauf = ausfuehren.lauf(plan.befehl, tmp_path)
    bericht = ADAPTER.bericht(plan.befehl, lauf.ausgabe)
    assert bericht is not None and bericht.bestanden_namen is not None
    assert {"tests/test_a.py::test_r", "tests/test_b.py::test_p[1]"} <= bericht.bestanden_namen
    assert "tests/test_b.py::test_r" not in bericht.bestanden_namen
    assert "tests/test_b.py::test_p[2]" not in bericht.bestanden_namen
    assert "tests/test_c.py::test_r" not in bericht.bestanden_namen


# --- Rückfall ohne Berichtbar (nur ``Zaehlbar``): Rechenlogik ------------------------------------


def test_rueckfall_ohne_berichtbar_zaehlt(tmp_path: Path) -> None:
    import sys

    from to_spawn.pruef import ausfuehren, wackler

    class _NurZaehlbar:
        def __init__(self, zweit_text: str, voll_text: str) -> None:
            self._zweit = [sys.executable, "-c", f"print({zweit_text!r})"]
            self._voll = [sys.executable, "-c", f"print({voll_text!r})"]

        def nochmal_befehl(self, befehl: list[str], code: int) -> list[str] | None:
            return self._zweit

        def voll_nochmal_befehl(self, befehl: list[str]) -> list[str]:
            return self._voll

        def rote(self, ausgabe: str) -> set[str]:
            from to_spawn.pruef.adapter_py import ADAPTER

            return ADAPTER.rote(ausgabe)

        def gelaufen(self, ausgabe: str) -> int | None:
            from to_spawn.pruef.adapter_py import ADAPTER

            return ADAPTER.gelaufen(ausgabe)

        def bestanden(self, ausgabe: str) -> int | None:
            from to_spawn.pruef.adapter_py import ADAPTER

            return ADAPTER.bestanden(ausgabe)

    erst = ausfuehren.Lauf(1, ".sF  [3/3]\nFAILED t.py::test_r\n1 failed, 1 passed, 1 skipped in 0.1s", 0.1)
    zweit_ok = ".  [1/1]\n1 passed in 0.1s"
    # Voller Lauf: gelaufen gleich, bestanden == vorher + rot → grün.
    ok = wackler.nochmal(_NurZaehlbar(zweit_ok, "..s  [3/3]\n2 passed, 1 skipped in 0.1s"), tmp_path, ["x"], erst)
    assert ok is not None and ok.gruen
    # Ein Test mehr übersprungen → rot.
    skip = wackler.nochmal(_NurZaehlbar(zweit_ok, ".ss  [3/3]\n1 passed, 2 skipped in 0.1s"), tmp_path, ["x"], erst)
    assert skip is not None and not skip.gruen
    # Ein Test abgewählt → rot.
    abwahl = wackler.nochmal(_NurZaehlbar(zweit_ok, "..  [2/2]\n2 passed in 0.1s"), tmp_path, ["x"], erst)
    assert abwahl is not None and not abwahl.gruen
    # Voller Lauf ohne Zählung → rot.
    leer = wackler.nochmal(_NurZaehlbar(zweit_ok, "...\n"), tmp_path, ["x"], erst)
    assert leer is not None and not leer.gruen


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
