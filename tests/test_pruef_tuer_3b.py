"""Tür des Prüf-Werkzeugkastens ``to_spawn.py pruefen <basis> <ziel>`` (L3 3b-1a, #99).

Echt laufen: CLI, git, pytest im frischen Probe-Repo ohne ``.to-spawn/``, ein echtes venv ohne
pytest (fehlendes Werkzeug). Gestellt ist nur ``gh`` (über das ``repo``-Fixture aus #206) und —
für Node — ein fehlendes ``npm`` im PATH (externes Werkzeug, im Prozess ausgeblendet).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import test_regularien_206 as reg
from test_regularien_206 import repo  # noqa: F401  (repo = Fixture)

CLI = reg.CLI
FELDER = {
    "basis",
    "ziel",
    "urteil",
    "stufen",
    "auswahl",
    "cache_treffer",
    "fehler_kurz",
    "wackler",
    "gesamt_s",
    "werkzeug",
}


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
    )


def _probe_py(ordner: Path, test_text: str = "assert addiere(1, 2) == 3") -> Path:
    """Frisches Python-Repo: Code, ein Test, Commit + Tag ``base`` — kein ``.to-spawn/``."""
    ordner.mkdir()
    (ordner / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\n", encoding="utf-8"
    )
    (ordner / "rechnen.py").write_text(
        "def addiere(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (ordner / "tests").mkdir()
    (ordner / "tests" / "test_rechnen.py").write_text(
        textwrap.dedent(f"""\
            import sys, pathlib
            sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
            from rechnen import addiere

            def test_addiere():
                {test_text}
            """),
        encoding="utf-8",
    )
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "base")
    _git(ordner, "tag", "base")
    return ordner


def _tuer(ordner: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), "pruefen", *args],
        cwd=ordner,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _zeile(erg: subprocess.CompletedProcess[str]) -> dict:
    zeilen = erg.stdout.strip().splitlines()
    assert len(zeilen) == 1, erg.stdout + erg.stderr
    return json.loads(zeilen[0])


def test_gruen_im_frischen_repo_ohne_vorarbeit(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py")
    erg = _tuer(probe, "base", "HEAD")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    zeile = _zeile(erg)
    assert FELDER <= zeile.keys()
    assert zeile["urteil"] == "grün"
    assert zeile["werkzeug"][0].startswith("pytest@")
    stufe = zeile["stufen"][0]
    assert (stufe["name"], stufe["adapter"], stufe["lief"]) == ("voll", "py", True)
    assert {"grund", "dauer_s", "befehl"} <= stufe.keys()
    protokoll = (probe / ".to-spawn" / "pruef" / "protokoll.jsonl").read_text(
        encoding="utf-8"
    )
    assert json.loads(protokoll.strip().splitlines()[-1]) == zeile
    assert not (probe / ".to-spawn" / "config.json").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", ".to-spawn"],
        cwd=probe,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout == "", "Protokoll darf das Repo nicht verschmutzen"


def test_eingebauter_fehler_ist_rot_mit_fehler_kurz(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py", test_text="assert addiere(2, 2) == 5")
    erg = _tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    zeile = _zeile(erg)
    assert zeile["urteil"] == "rot"
    assert "test_addiere" in zeile["fehler_kurz"]
    assert len(zeile["fehler_kurz"].splitlines()) <= 20


def test_fehlendes_pytest_ist_gate_fehler_nicht_rot(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py")
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(probe / ".venv")],
        check=True,
        capture_output=True,
    )
    erg = _tuer(probe, "base", "HEAD")
    assert erg.returncode == 2, erg.stdout + erg.stderr
    zeile = _zeile(erg)
    assert zeile["urteil"] == "gate-fehler"
    assert "pytest fehlt" in zeile["fehler_kurz"]


def test_fehlendes_npm_ist_gate_fehler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from to_spawn import pruef
    from to_spawn.pruef import adapter_node

    probe = tmp_path / "node"
    probe.mkdir()
    (probe / "package.json").write_text(
        '{"scripts": {"test": "vitest run"}}', encoding="utf-8"
    )
    _git(probe, "init", "-q")
    _git(probe, "add", "-A")
    _git(probe, "commit", "-qm", "base")
    monkeypatch.setattr(adapter_node.shutil, "which", lambda _name: None)
    urteil = pruef.pruefen(probe, "HEAD", "HEAD")
    assert urteil.exit == 2
    assert "npm fehlt" in urteil.zeile["fehler_kurz"]


def test_unbekannter_stand_und_nicht_ausgecheckt_sind_gate_fehler(
    tmp_path: Path,
) -> None:
    probe = _probe_py(tmp_path / "py")
    assert _tuer(probe, "gibt-es-nicht", "HEAD").returncode == 2
    (probe / "neu.txt").write_text("x", encoding="utf-8")
    _git(probe, "add", "-A")
    _git(probe, "commit", "-qm", "zwei")
    erg = _tuer(probe, "base", "base")
    assert erg.returncode == 2
    assert "nicht ausgecheckt" in _zeile(erg)["fehler_kurz"]


def test_alter_aufruf_pruefen_s_leitet_auf_regularien(repo: Path) -> None:  # noqa: F811
    neu = reg._cli(repo, "regularien", str(reg.SPEC), "--ohne-github")
    alt = reg._cli(repo, "pruefen", str(reg.SPEC), "--ohne-github")
    assert (alt.returncode, alt.stdout) == (neu.returncode, neu.stdout)
    assert "heißt jetzt „to_spawn.py regularien" in alt.stderr
    assert "heißt jetzt" not in neu.stderr
