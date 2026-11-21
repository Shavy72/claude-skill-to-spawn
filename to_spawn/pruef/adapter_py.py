"""Python-Adapter: erkennt ein Python-Repo und weiß, wie pytest es voll prüft (Paket-intern, #99)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from to_spawn.pruef import ausfuehren
from to_spawn.pruef.leiter import Plan

#: Dateien, an denen ein Python-Repo zu erkennen ist.
_MERKMALE = (
    "pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "conftest.py",
    "requirements.txt",
)
#: Zeile der pytest-Kurzfassung je rotem Test: ``FAILED <knoten-id>[ - <grund>]`` (auch ERROR).
_ROT_ZEILE = re.compile(r"^(?:FAILED|ERROR) (.+?)(?: - .*)?$", re.MULTILINE)


def _python(repo: Path) -> str:
    """Interpreter des Repos: eigenes ``.venv``/``venv`` (Linux und Windows), sonst der laufende."""
    for ordner in (".venv", "venv"):
        for teil in (("bin", "python"), ("Scripts", "python.exe")):
            kandidat = repo.joinpath(ordner, *teil)
            if kandidat.is_file() and os.access(kandidat, os.X_OK):
                return str(kandidat)
    return sys.executable


class PythonAdapter:
    name = "py"

    def erkennt(self, repo: Path) -> bool:
        if any((repo / merkmal).is_file() for merkmal in _MERKMALE):
            return True
        tests = repo / "tests"
        return tests.is_dir() and any(tests.rglob("test_*.py"))

    def voll(self, repo: Path) -> Plan:
        python = _python(repo)
        probe = ausfuehren.lauf([python, "-m", "pytest", "--version"], repo)
        if probe.code != 0:
            return Plan(
                werkzeug="pytest@?",
                befehl=[],
                fehler=f"pytest fehlt für {python} (pip install pytest)",
            )
        version = probe.ausgabe.strip().split()[-1] if probe.ausgabe.strip() else "?"
        return Plan(
            werkzeug=f"pytest@{version}",
            befehl=[python, "-m", "pytest", "-q"],
            fehler="",
        )

    def deute(self, code: int) -> int:
        # pytest: 0 grün, 1 Tests rot, 2 abgebrochen/Sammelfehler (Import-Fehler im Code = rot),
        # 5 keine Tests gefunden, 3/4 innerer bzw. Aufruf-Fehler → Werkzeug, nicht Code.
        if code == 0:
            return 0
        if code in (1, 2):
            return 1
        return 2

    def nochmal_befehl(self, befehl: list[str], code: int) -> list[str] | None:
        # Wiederholung nur bei echtem Test-Rot (1); Sammelfehler (2) wackeln nicht.
        # ``--lf`` ist pytest-eigen (cacheprovider); ``--lfnf none``: ohne Cache läuft nichts
        # (Exit 5) statt alles, dann bleibt es beim ersten Rot.
        return [*befehl, "--lf", "--lfnf", "none"] if code == 1 else None

    def rote(self, ausgabe: str) -> set[str]:
        return {treffer.strip() for treffer in _ROT_ZEILE.findall(ausgabe)}


ADAPTER = PythonAdapter()
