"""Wackler im Prüf-Werkzeugkasten: rote Tests genau einmal wiederholen (L3 3b-4, #103).

Echt laufen: CLI, git, pytest im frischen Probe-Repo. Der Wackel-Test zählt seine Läufe in einer
Datei außerhalb des Repos: erster Lauf rot, zweiter grün. Nichts ist gestellt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import test_pruef_tuer_3b as tuer


def _probe_mit_zaehler(tmp_path: Path, bedingung: str) -> tuple[Path, Path]:
    """Probe-Repo mit ``test_addiere``; ``bedingung`` (über ``lauf``) entscheidet über Grün."""
    zaehler = tmp_path / "zaehler.txt"
    zaehler.write_text("0", encoding="utf-8")
    test_text = (
        f"z = pathlib.Path({str(zaehler)!r}); lauf = int(z.read_text()) + 1; "
        f"z.write_text(str(lauf)); assert {bedingung}"
    )
    return tuer._probe_py(tmp_path / "py", test_text=test_text), zaehler


def test_erst_rot_dann_gruen_ist_wackler_nie_still_gruen(tmp_path: Path) -> None:
    probe, zaehler = _probe_mit_zaehler(tmp_path, "lauf >= 2")
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "wackler"
    assert [w["test"] for w in zeile["wackler"]] == [
        "tests/test_rechnen.py::test_addiere"
    ]
    assert zaehler.read_text(encoding="utf-8") == "3"
    stufe = zeile["stufen"][0]
    assert stufe["wiederholung_s"] > 0
    assert stufe["voll_nochmal_s"] > 0
    assert "--lf" in stufe["wiederholung"]
    liste = probe / ".to-spawn" / "pruef" / "wackler.jsonl"
    eintrag = json.loads(liste.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert eintrag["test"] == "tests/test_rechnen.py::test_addiere"
    assert {"zeit", "basis", "ziel", "adapter"} <= eintrag.keys()


def test_zweimal_rot_bleibt_rot_nach_genau_einer_wiederholung(tmp_path: Path) -> None:
    probe, zaehler = _probe_mit_zaehler(tmp_path, "lauf < 0")
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "rot"
    assert zeile["wackler"] == []
    assert zaehler.read_text(encoding="utf-8") == "2"
    assert zeile["stufen"][0]["wiederholung_s"] > 0
    assert not (probe / ".to-spawn" / "pruef" / "wackler.jsonl").exists()


def test_alles_gruen_ohne_wiederholung(tmp_path: Path) -> None:
    probe, zaehler = _probe_mit_zaehler(tmp_path, "lauf >= 1")
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "grün"
    assert zeile["wackler"] == []
    assert zaehler.read_text(encoding="utf-8") == "1"
    assert zeile["stufen"][0]["wiederholung_s"] == 0


# --- Fix-Runde 1 (Review PR #126) -------------------------------------------------------------


def _probe_mehrere(tmp_path: Path, tests_text: str, ini_zusatz: str = "") -> Path:
    """Probe-Repo mit eigener Testdatei; ``{zaehler}`` im Text wird durch den Zählerpfad ersetzt."""
    zaehler = tmp_path / "zaehler.txt"
    zaehler.write_text("0", encoding="utf-8")
    probe = tuer._probe_py(tmp_path / "py")
    (probe / "pytest.ini").write_text(
        f"[pytest]\ntestpaths = tests\n{ini_zusatz}", encoding="utf-8"
    )
    (probe / "tests" / "test_rechnen.py").write_text(
        "import pathlib\n\n" + tests_text.replace("{zaehler}", repr(str(zaehler))),
        encoding="utf-8",
    )
    tuer._git(probe, "add", "-A")
    tuer._git(probe, "commit", "-q", "--amend", "-m", "base")
    tuer._git(probe, "tag", "-f", "base")
    return probe


_WACKEL = (
    "def test_1wackel():\n"
    "    z = pathlib.Path({zaehler}); lauf = int(z.read_text()) + 1\n"
    "    z.write_text(str(lauf)); assert lauf >= 2\n\n"
)
_DAUERROT = "def test_{n}dauerrot():\n    assert False\n\n"


@pytest.mark.parametrize("abbruch", ["-x", "--maxfail=1"])
def test_abbruch_in_addopts_macht_kein_falsches_gruen(
    tmp_path: Path, abbruch: str
) -> None:
    probe = _probe_mehrere(
        tmp_path, _WACKEL + _DAUERROT.format(n=2), f"addopts = {abbruch}\n"
    )
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "rot"
    assert [w["test"] for w in zeile["wackler"]] == [
        "tests/test_rechnen.py::test_1wackel"
    ]


def test_mehrere_rote_nur_einer_wackelt(tmp_path: Path) -> None:
    probe = _probe_mehrere(
        tmp_path, _WACKEL + _DAUERROT.format(n=2) + _DAUERROT.format(n=3)
    )
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "rot"
    assert [w["test"] for w in zeile["wackler"]] == [
        "tests/test_rechnen.py::test_1wackel"
    ]
    assert zeile["stufen"][0]["wiederholung_s"] > 0


def test_sammelfehler_wird_nicht_wiederholt(tmp_path: Path) -> None:
    probe = _probe_mehrere(tmp_path, "import gibt_es_nicht\n\n" + _WACKEL)
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "rot"
    assert zeile["wackler"] == []
    assert zeile["stufen"][0]["wiederholung_s"] == 0
    assert (tmp_path / "zaehler.txt").read_text(encoding="utf-8") == "0"


def test_testkennung_mit_bindestrich_bleibt_ganz() -> None:
    from to_spawn.pruef.adapter_py import ADAPTER

    ausgabe = (
        "FAILED tests/test_a.py::test_p[a - b] - assert False\n"
        "FAILED tests/test_a.py::test_q - Fehler - mit Strich\n"
        "FAILED tests/test_a.py::test_r[x]\n"
        "ERROR tests/test_b.py - ImportError: kein Modul\n"
    )
    assert ADAPTER.rote(ausgabe) == {
        "tests/test_a.py::test_p[a - b]",
        "tests/test_a.py::test_q",
        "tests/test_a.py::test_r[x]",
        "tests/test_b.py",
    }


class _Stellvertreter:
    """Adapter, dessen Wiederholung ein echter Prozess mit vorgegebenem Exit und Text ist."""

    def __init__(self, code: int, text: str, voll_text: str | None = None) -> None:
        self._befehl = [
            sys.executable,
            "-c",
            f"import sys; print({text!r}); sys.exit({code})",
        ]
        voll_ausgabe = text if voll_text is None else voll_text
        self._voll_befehl = [
            sys.executable,
            "-c",
            f"import sys; print({voll_ausgabe!r}); sys.exit({code})",
        ]

    def nochmal_befehl(self, befehl: list[str], code: int) -> list[str] | None:
        return self._befehl

    def voll_nochmal_befehl(self, befehl: list[str]) -> list[str]:
        return self._voll_befehl

    def rote(self, ausgabe: str) -> set[str]:
        from to_spawn.pruef.adapter_py import ADAPTER

        return ADAPTER.rote(ausgabe)


@pytest.mark.parametrize(
    ("code", "text"),
    [(3, "FAILED t.py::test_a - weg"), (5, ""), (1, "kein Rot-Name in der Ausgabe")],
)
def test_wiederholung_ohne_aussage_bleibt_rot(
    tmp_path: Path, code: int, text: str
) -> None:
    from to_spawn.pruef import ausfuehren, wackler

    erst = ausfuehren.Lauf(1, "FAILED t.py::test_a - assert False\n", 0.1)
    erg = wackler.nochmal(_Stellvertreter(code, text), tmp_path, ["x"], erst)
    assert erg is not None
    assert (erg.gruen, erg.wackler) == (False, ())


# --- Fix-Runde 2 (Review PR #126): Cache-Auswahl und unbelegt vollständige Läufe --------------


@pytest.mark.parametrize("auswahl", ["--sw", "--stepwise", "--lf"])
def test_cache_auswahl_in_addopts_ueberspringt_keinen_roten_test(
    tmp_path: Path, auswahl: str
) -> None:
    """Zwei Prüfungen nacheinander: der zweite Stand bricht einen vorher grünen Test."""
    probe = _probe_mehrere(
        tmp_path,
        "def test_0():\n    pass\n\ndef test_1():\n    assert False\n",
        f"addopts = {auswahl}\n",
    )
    assert tuer._tuer(probe, "base", "HEAD").returncode == 1
    (probe / "tests" / "test_rechnen.py").write_text(
        "def test_0():\n    assert False\n\ndef test_1():\n    pass\n", encoding="utf-8"
    )
    tuer._git(probe, "commit", "-q", "-am", "bricht test_0")
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    assert tuer._zeile(erg)["urteil"] == "rot"


def test_stepwise_abbruch_mit_wackler_bleibt_rot(tmp_path: Path) -> None:
    probe = _probe_mehrere(
        tmp_path, _WACKEL + _DAUERROT.format(n=2), "addopts = --sw\n"
    )
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    assert tuer._zeile(erg)["urteil"] == "rot"


class _EchtePytestDeutung(_Stellvertreter):
    """Wie ``_Stellvertreter``, aber mit allem, was der Python-Adapter aus Ausgaben liest."""

    def gelaufen(self, ausgabe: str) -> int | None:
        from to_spawn.pruef.adapter_py import ADAPTER

        return ADAPTER.gelaufen(ausgabe)

    def bestanden(self, ausgabe: str) -> int | None:
        from to_spawn.pruef.adapter_py import ADAPTER

        return ADAPTER.bestanden(ausgabe)


_ERST_GANZ = (
    ".F.                                                     [3/3]\n"
    "FAILED t.py::test_a - x\n"
    "1 failed, 2 passed in 0.1s\n"
)
_ERST_ABGEBROCHEN = (
    ".F\nFAILED t.py::test_a - x\n!!! Interrupted: stopping after 1 failures !!!\n"
)


@pytest.mark.parametrize(
    ("erst_text", "zweit_text"),
    [
        (
            _ERST_ABGEBROCHEN,
            ".                                           [1/1]\n1 passed",
        ),
        (_ERST_GANZ, ""),
        (_ERST_GANZ, "no tests ran"),
        (_ERST_GANZ, ".                                           [1/2]\n1 passed"),
    ],
    ids=[
        "erster-lauf-abgebrochen",
        "wiederholung-stumm",
        "nichts-gelaufen",
        "teilweise",
    ],
)
def test_unbelegt_vollstaendiger_lauf_ist_nie_gruen(
    tmp_path: Path, erst_text: str, zweit_text: str
) -> None:
    from to_spawn.pruef import ausfuehren, wackler

    erst = ausfuehren.Lauf(1, erst_text, 0.1)
    erg = wackler.nochmal(_EchtePytestDeutung(0, zweit_text), tmp_path, ["x"], erst)
    assert erg is None or erg.gruen is False


def test_belegt_vollstaendig_bleibt_wackler(tmp_path: Path) -> None:
    from to_spawn.pruef import ausfuehren, wackler

    erst = ausfuehren.Lauf(1, _ERST_GANZ, 0.1)
    zweit = ".                                                     [1/1]\n1 passed"
    voll = "...                                                   [3/3]\n3 passed"
    erg = wackler.nochmal(
        _EchtePytestDeutung(0, zweit, voll_text=voll), tmp_path, ["x"], erst
    )
    assert erg is not None
    assert (erg.gruen, erg.wackler) == (True, ("t.py::test_a",))


def test_gelaufen_liest_nur_den_fortschritt_vor_den_berichten() -> None:
    from to_spawn.pruef.adapter_py import ADAPTER

    assert ADAPTER.gelaufen(".F.  [3/3]\n1 failed, 2 passed") == 3
    assert (
        ADAPTER.gelaufen("t.py::test_a PASSED  [ 1/2]\nt.py::test_b PASSED  [ 2/2]\n")
        == 2
    )
    assert ADAPTER.gelaufen(".F\n!!! Interrupted !!!\n") is None
    assert ADAPTER.gelaufen(".F  [2/3]\n") is None
    # Gefälschter Fortschritt aus der Testausgabe (nach den Berichten) zählt nicht.
    gefaelscht = ".F\n==== FAILURES ====\n---- Captured stdout call ----\n.. [9/9]\n"
    assert ADAPTER.gelaufen(gefaelscht) is None


# --- Fix-Runde 3 (Review PR #126): nie grün gelaufen ist kein Wackler ---------------------------

_ROT_DANN = (
    "import pytest\n\n"
    "def test_0gruen():\n    pass\n\n"
    "def test_3rot_dann():\n"
    "    z = pathlib.Path({zaehler}); lauf = int(z.read_text()) + 1\n"
    "    z.write_text(str(lauf))\n"
    "    if lauf >= 2:\n        pytest.{art}('weg')\n"
    "    assert False\n"
)


@pytest.mark.parametrize("art", ["skip", "xfail"])
def test_rot_dann_uebersprungen_ist_kein_wackler(tmp_path: Path, art: str) -> None:
    """Fälle 7a/7b: Wiederholung überspringt den roten Test (Exit 0), er lief nie grün."""
    probe = _probe_mehrere(tmp_path, _ROT_DANN.replace("{art}", art))
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "rot"
    assert zeile["wackler"] == []


def test_nur_im_vollen_lauf_rot_ist_kein_wackler(tmp_path: Path) -> None:
    """Fall 7d: reihenfolge-abhängig, allein grün, im vollen Lauf immer rot."""
    probe = _probe_mehrere(
        tmp_path,
        "ZUSTAND = []\n\n"
        "def test_a_verschmutzt():\n    ZUSTAND.append(1)\n\n"
        "def test_b_braucht_sauber():\n    assert ZUSTAND == []\n",
    )
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "rot"
    assert zeile["wackler"] == []
    assert zeile["stufen"][0]["voll_nochmal_s"] > 0


def test_bestanden_liest_die_schlusszeile() -> None:
    from to_spawn.pruef.adapter_py import ADAPTER

    assert ADAPTER.bestanden("s  [1/1]\n1 skipped in 0.01s") == 0
    assert ADAPTER.bestanden("x  [1/1]\n1 xfailed in 0.01s") == 0
    assert ADAPTER.bestanden(".  [1/1]\n1 passed, 1 warning in 0.01s") == 1
    assert ADAPTER.bestanden("F.  [2/2]\n1 failed, 1 passed in 0.1s") == 1
    assert ADAPTER.bestanden("==== 3 passed, 2 deselected in 0.1s ====") == 3
    assert ADAPTER.bestanden("") is None


# --- #149: Härtung Wackler, zweiter voller Lauf, Bericht und PYTEST_ADDOPTS -------------------


def _probe_mit_modus(
    tmp_path: Path,
    tests_text: str,
    *,
    parallel: bool = False,
    ini_zusatz: str = "",
    conftest_text: str = "",
) -> Path:
    """Probe-Repo seriell oder mit pytest-xdist (``parallel=True``) und optionalem ``conftest.py``."""
    probe = _probe_mehrere(tmp_path, tests_text, ini_zusatz=ini_zusatz)
    geaendert = False
    if parallel:
        (probe / "requirements.txt").write_text("pytest-xdist\n", encoding="utf-8")
        geaendert = True
    if conftest_text:
        zaehler = tmp_path / "zaehler.txt"
        (probe / "tests" / "conftest.py").write_text(
            conftest_text.replace("{zaehler}", repr(str(zaehler))),
            encoding="utf-8",
        )
        geaendert = True
    if geaendert:
        tuer._git(probe, "add", "-A")
        tuer._git(probe, "commit", "-q", "--amend", "-m", "base")
        tuer._git(probe, "tag", "-f", "base")
    return probe


_VOLL_SKIP_ODER_XFAIL = (
    "import pytest\n\n"
    "def test_0gruen():\n    pass\n\n"
    "def test_1wackel():\n"
    "    z = pathlib.Path({zaehler}); lauf = int(z.read_text()) + 1\n"
    "    z.write_text(str(lauf))\n"
    "    if lauf == 1:\n        assert False\n"
    "    if lauf >= 3:\n        pytest.{art}('im vollen lauf weg')\n"
)


@pytest.mark.parametrize("art", ["skip", "xfail"])
@pytest.mark.parametrize("parallel", [False, True], ids=["seriell", "xdist"])
def test_wiederholung_gruen_voller_lauf_skip_oder_xfail_bleibt_rot(
    tmp_path: Path, art: str, parallel: bool
) -> None:
    """#149 Befund 1 + 4: Lauf 1 rot, Lauf 2 grün, Lauf 3 (voll) überspringt → rot, nie Wackler."""
    if parallel:
        pytest.importorskip("xdist")
    probe = _probe_mit_modus(
        tmp_path,
        _VOLL_SKIP_ODER_XFAIL.replace("{art}", art),
        parallel=parallel,
    )
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "rot"
    assert zeile["wackler"] == []
    assert zeile["stufen"][0]["voll_nochmal_s"] > 0


@pytest.mark.parametrize("parallel", [False, True], ids=["seriell", "xdist"])
def test_wiederholung_gruen_voller_lauf_abwahl_bleibt_rot(
    tmp_path: Path, parallel: bool
) -> None:
    """#149 Befund 1 + 4: Lauf 2 grün, im zweiten vollen Lauf wird der Wackel-Test abgewählt."""
    if parallel:
        pytest.importorskip("xdist")
    conftest = (
        "import pathlib\n\n"
        "def pytest_collection_modifyitems(items):\n"
        "    if int(pathlib.Path({zaehler}).read_text()) >= 2:\n"
        "        items[:] = [i for i in items if i.name != 'test_1wackel']\n"
    )
    probe = _probe_mit_modus(
        tmp_path,
        "def test_0gruen():\n    pass\n\n" + _WACKEL,
        parallel=parallel,
        conftest_text=conftest,
    )
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "rot"
    assert zeile["wackler"] == []
    assert zeile["stufen"][0]["voll_nochmal_s"] > 0


def test_wiederholung_gruen_voller_lauf_pytest_exit_null_bleibt_rot(
    tmp_path: Path,
) -> None:
    """#149 Befund 1: Lauf 2 grün, zweiter voller Lauf steigt mit pytest.exit(..., returncode=0) aus."""
    tests = (
        "import pytest\n\n"
        "def test_0gruen():\n    pass\n\n"
        "def test_1wackel():\n"
        "    z = pathlib.Path({zaehler}); lauf = int(z.read_text()) + 1\n"
        "    z.write_text(str(lauf))\n"
        "    if lauf == 1:\n        assert False\n"
        "    if lauf >= 3:\n        pytest.exit('abgebrochen', returncode=0)\n"
    )
    probe = _probe_mehrere(tmp_path, tests)
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "rot"
    assert zeile["wackler"] == []
    assert zeile["stufen"][0]["voll_nochmal_s"] > 0


@pytest.mark.parametrize("parallel", [False, True], ids=["seriell", "xdist"])
def test_gefaelschte_schlusszeile_per_atexit_taeuscht_zaehler_nicht(
    tmp_path: Path, parallel: bool
) -> None:
    """#149 Befund 2: rot → skip mit gefälschtem ``1 passed`` per atexit bleibt rot."""
    if parallel:
        pytest.importorskip("xdist")
    tests = (
        "import atexit, pytest\n\n"
        "def test_0gruen():\n    pass\n\n"
        "def test_1rot_dann_skip():\n"
        "    z = pathlib.Path({zaehler}); lauf = int(z.read_text()) + 1\n"
        "    z.write_text(str(lauf))\n"
        "    if lauf >= 2:\n"
        "        atexit.register(lambda: print('\\n1 passed in 0.01s'))\n"
        "        pytest.skip('weg')\n"
        "    assert False\n"
    )
    probe = _probe_mit_modus(tmp_path, tests, parallel=parallel)
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "rot"
    assert zeile["wackler"] == []


def test_addopts_q_mit_echtem_wackler_wird_aus_bericht_erkannt(tmp_path: Path) -> None:
    """#149 Befund 3: ``addopts = -q`` (wirkt als ``-qq``, ohne Schlusszeile) liest aus dem Bericht."""
    probe = _probe_mehrere(
        tmp_path,
        "def test_0gruen():\n    pass\n\n" + _WACKEL,
        ini_zusatz="addopts = -q\n",
    )
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "wackler"
    assert [w["test"] for w in zeile["wackler"]] == [
        "tests/test_rechnen.py::test_1wackel"
    ]


def test_echter_wackler_mit_dauer_skip_bleibt_wackler(tmp_path: Path) -> None:
    """Ein dauerhaft übersprungener Test (z. B. Plattform-Skip) blockiert echte Wackler nicht."""
    tests = (
        "import pytest\n\n"
        "def test_0gruen():\n    pass\n\n"
        "def test_2plattform_skip():\n    pytest.skip('andere Plattform')\n\n"
        + _WACKEL
    )
    probe = _probe_mehrere(tmp_path, tests)
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "wackler"
    assert [w["test"] for w in zeile["wackler"]] == [
        "tests/test_rechnen.py::test_1wackel"
    ]


@pytest.mark.parametrize(
    "addopts",
    [
        "-k test_0gruen",
        "--deselect tests/test_rechnen.py::test_1dauerrot",
        "--co",
    ],
    ids=["k-filter", "deselect", "collect-only"],
)
def test_pytest_addopts_aus_umgebung_wird_bereinigt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, addopts: str
) -> None:
    """#149 Altlast: geerbtes ``PYTEST_ADDOPTS`` darf rote Tests nie ausblenden."""
    probe = _probe_mehrere(
        tmp_path,
        "def test_0gruen():\n    pass\n\n" + _DAUERROT.format(n=1),
    )
    monkeypatch.setenv("PYTEST_ADDOPTS", addopts)
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    assert tuer._zeile(erg)["urteil"] == "rot"


@pytest.mark.parametrize(
    ("tests_text", "conftest_text"),
    [
        (
            (
                "import pytest\n\n"
                "def test_0exit():\n    pytest.exit('stop', returncode=0)\n\n"
                "def test_1rot():\n    assert False\n"
            ),
            "",
        ),
        (
            (
                "import pytest\n\n"
                "@pytest.fixture\n"
                "def ausstieg():\n    yield\n    pytest.exit('stop', returncode=0)\n\n"
                "def test_0gruen(ausstieg):\n    pass\n\n"
                "def test_1rot():\n    assert False\n"
            ),
            "",
        ),
        (
            "def test_0rot():\n    assert False\n",
            (
                "import pytest\n\n"
                "def pytest_collection_modifyitems(items):\n"
                "    pytest.exit('stop', returncode=0)\n"
            ),
        ),
    ],
    ids=["im-test", "im-teardown", "bei-sammlung"],
)
def test_pytest_exit_null_im_vollen_lauf_ist_nie_gruen(
    tmp_path: Path, tests_text: str, conftest_text: str
) -> None:
    """#149 Altlast: ``pytest.exit(..., returncode=0)`` ergibt nie Grün, wenn Tests fehlen."""
    probe = _probe_mit_modus(tmp_path, tests_text, conftest_text=conftest_text)
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode != 0, erg.stdout + erg.stderr
    assert tuer._zeile(erg)["urteil"] != "grün"
