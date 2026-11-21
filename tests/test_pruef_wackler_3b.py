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
    assert zaehler.read_text(encoding="utf-8") == "2"
    stufe = zeile["stufen"][0]
    assert stufe["wiederholung_s"] > 0
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

    def __init__(self, code: int, text: str) -> None:
        self._befehl = [
            sys.executable,
            "-c",
            f"import sys; print({text!r}); sys.exit({code})",
        ]

    def nochmal_befehl(self, befehl: list[str], code: int) -> list[str] | None:
        return self._befehl

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


_ERST_GANZ = ".F.                                                     [3/3]\nFAILED t.py::test_a - x\n"
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
    erg = wackler.nochmal(_EchtePytestDeutung(0, zweit), tmp_path, ["x"], erst)
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
