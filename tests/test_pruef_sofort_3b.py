"""Stufe 1 Sofort-Prüfung + Stufe 2 „nur Doku“ der Prüf-Tür (L3 3b-1b, #100).

Echt laufen: CLI, git, ruff und pytest im frischen Probe-Repo. Gestellt ist nur ein fehlendes
ruff (externes Werkzeug, im Prozess ausgeblendet). Ein Test des Probe-Repos schreibt eine
Marker-Datei — fehlt sie, lief kein Test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
sys.path.insert(0, str(SKILL))


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
    )


def _probe(ordner: Path) -> Path:
    """Python-Repo mit Code, README und einem Test, der ``lief.txt`` schreibt; Tag ``base``."""
    ordner.mkdir()
    (ordner / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (ordner / "README.md").write_text("# Probe\n", encoding="utf-8")
    (ordner / "rechnen.py").write_text("def addiere(a, b):\n    return a + b\n", encoding="utf-8")
    (ordner / "tests").mkdir()
    (ordner / "tests" / "test_rechnen.py").write_text(
        textwrap.dedent("""\
            import pathlib, sys
            WURZEL = pathlib.Path(__file__).parent.parent
            sys.path.insert(0, str(WURZEL))
            from rechnen import addiere

            def test_addiere():
                (WURZEL / "lief.txt").write_text("ja")
                assert addiere(1, 2) == 3
            """),
        encoding="utf-8",
    )
    (ordner / ".gitignore").write_text("lief.txt\n", encoding="utf-8")
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "base")
    _git(ordner, "tag", "base")
    return ordner


def _commit(ordner: Path, datei: str, text: str) -> None:
    (ordner / datei).write_text(text, encoding="utf-8")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", f"ändert {datei}")


def _tuer(ordner: Path) -> tuple[int, dict]:
    erg = subprocess.run(
        [sys.executable, str(CLI), "pruefen", "base", "HEAD"],
        cwd=ordner,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    zeilen = erg.stdout.strip().splitlines()
    assert len(zeilen) == 1, erg.stdout + erg.stderr
    return erg.returncode, json.loads(zeilen[0])


def _stufe(zeile: dict, name: str) -> dict:
    treffer = [stufe for stufe in zeile["stufen"] if stufe["name"] == name]
    assert len(treffer) == 1, zeile["stufen"]
    return treffer[0]


def test_m11_fall_1_nur_readme_laeuft_kein_test(tmp_path: Path) -> None:
    probe = _probe(tmp_path / "py")
    _commit(probe, "README.md", "# Probe\n\nNeuer Absatz.\n")
    start = time.monotonic()
    code, zeile = _tuer(probe)
    dauer = time.monotonic() - start
    assert code == 0, zeile
    assert not (probe / "lief.txt").exists(), "kein Test darf laufen"
    assert _stufe(zeile, "doku")["grund"] == "nur Doku"
    voll = _stufe(zeile, "voll")
    assert (voll["lief"], voll["grund"]) == (False, "nur Doku")
    assert zeile["gesamt_s"] < 10 and dauer < 10, (zeile["gesamt_s"], dauer)
    assert {"dauer_s"} <= _stufe(zeile, "sofort").keys()


def test_syntax_fehler_ist_rot_in_stufe_1_und_voll_laeuft_nicht(tmp_path: Path) -> None:
    assert shutil.which("ruff"), "ruff muss für diesen Weg-Test installiert sein"
    probe = _probe(tmp_path / "py")
    _commit(probe, "rechnen.py", "def addiere(a, b)\n    return a + b\n")
    code, zeile = _tuer(probe)
    assert code == 1, zeile
    sofort = _stufe(zeile, "sofort")
    assert (sofort["lief"], sofort["adapter"]) == (True, "py")
    assert "rechnen.py" in zeile["fehler_kurz"]
    assert _stufe(zeile, "voll")["lief"] is False
    assert not (probe / "lief.txt").exists(), "Stufe voll darf nach Rot nicht laufen"
    assert any(w.startswith("ruff@") for w in zeile["werkzeug"])


def test_fehlendes_ruff_ist_ausgelassen_nicht_gruen_und_voll_prueft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from to_spawn import pruef
    from to_spawn.pruef import sofort

    probe = _probe(tmp_path / "py")
    _commit(probe, "rechnen.py", "def addiere(a, b):\n    return b + a\n")
    echt = shutil.which
    monkeypatch.setattr(sofort.shutil, "which", lambda name, **kw: None if name == "ruff" else echt(name, **kw))
    urteil = pruef.pruefen(probe, "base", "HEAD")
    assert urteil.exit == 0, urteil.zeile
    stufe = _stufe(urteil.zeile, "sofort")
    assert (stufe["lief"], stufe["grund"]) == (False, "Werkzeug fehlt (ruff)")
    assert _stufe(urteil.zeile, "voll")["lief"] is True
    assert (probe / "lief.txt").exists()


def test_doku_die_ein_test_liest_ist_nicht_nur_doku(tmp_path: Path) -> None:
    from to_spawn import pruef

    probe = _probe(tmp_path / "py")
    _commit(probe, "tests/test_readme.py", "def test_readme():\n    assert 'README.md'\n")
    _git(probe, "tag", "-f", "base")
    _commit(probe, "README.md", "# Probe\n\nGeändert.\n")
    urteil = pruef.pruefen(probe, "base", "HEAD")
    assert _stufe(urteil.zeile, "doku")["grund"] == "nicht nur Doku geändert"
    assert _stufe(urteil.zeile, "voll")["lief"] is True


def test_ungespeicherte_code_aenderung_hebt_nur_doku_auf(tmp_path: Path) -> None:
    from to_spawn import pruef

    probe = _probe(tmp_path / "py")
    _commit(probe, "README.md", "# Probe\n\nGeändert.\n")
    (probe / "neu.py").write_text("X = 1\n", encoding="utf-8")  # neu, nicht committet
    urteil = pruef.pruefen(probe, "base", "HEAD")
    assert _stufe(urteil.zeile, "doku")["grund"] == "nicht nur Doku geändert"
    assert _stufe(urteil.zeile, "voll")["lief"] is True
