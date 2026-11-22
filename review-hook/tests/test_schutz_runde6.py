"""Runde 6 (Rest 3): Schutz-Fixture `u` darf nur Zeilen des eigenen Tests in der echten
mensch_noetig.jsonl bemängeln. Parallele Bau-Sessions schreiben dort echte Zeilen — das
darf keinen Hook-Test rot machen. Läuft als echter pytest-Unterprozess gegen eine
Ersatz-Datei (die echte Datei wird nie berührt)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent

INNEN = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, {tests!r})
import test_hooks
from test_hooks import Umgebung, u  # noqa: F401

test_hooks.ECHT_MENSCH_NOETIG = Path(os.environ["SCHUTZ_ERSATZ"])


def _anhaengen(repo: str) -> None:
    with test_hooks.ECHT_MENSCH_NOETIG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({{"session_id": "x", "repo": repo, "grund": "review_offen"}}) + "\\n")


def test_fremde_session_schreibt(u: Umgebung) -> None:
    _anhaengen("C:/dev/wt-314")


def test_eigener_test_schreibt(u: Umgebung) -> None:
    _anhaengen((u.tmp / "repo").resolve().as_posix())
"""


def _innen_laufen(tmp_path: Path) -> dict[str, str]:
    ersatz = tmp_path / "mensch_noetig.jsonl"
    ersatz.write_text('{"session_id": "alt", "repo": "C:/dev/wt-300"}\n', encoding="utf-8")
    datei = tmp_path / "innen" / "test_innen.py"
    datei.parent.mkdir()
    datei.write_text(INNEN.format(tests=str(TESTS)), encoding="utf-8")
    bericht = tmp_path / "bericht.xml"
    env = {**os.environ, "SCHUTZ_ERSATZ": str(ersatz)}
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(datei),
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            f"--junitxml={bericht}",
            "--basetemp",
            str(tmp_path / "bt"),
        ],
        cwd=datei.parent,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    import xml.etree.ElementTree as ET

    ergebnis: dict[str, str] = {}
    for fall in ET.parse(bericht).getroot().iter("testcase"):
        fehler = fall.find("error")
        if fehler is None:
            fehler = fall.find("failure")
        ergebnis[fall.get("name", "")] = "ok" if fehler is None else (fehler.get("message") or "rot")
    return ergebnis


def test_fremde_zeile_macht_test_nicht_rot(tmp_path: Path) -> None:
    ergebnis = _innen_laufen(tmp_path)
    assert ergebnis["test_fremde_session_schreibt"] == "ok", ergebnis


def test_eigene_zeile_bleibt_rot(tmp_path: Path) -> None:
    ergebnis = _innen_laufen(tmp_path)
    assert ergebnis["test_eigener_test_schreibt"] != "ok", ergebnis
    assert "mensch_noetig" in json.dumps(ergebnis)
