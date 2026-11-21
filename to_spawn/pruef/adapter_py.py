"""Python-Adapter: erkennt ein Python-Repo und weiß, wie pytest es voll prüft (Paket-intern, #99)."""

from __future__ import annotations

import os
import re
import sys
import tempfile
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
#: Eine Parameter-Klammer gehört ganz zur Kennung, auch mit „ - “ darin (``test_p[a - b]``).
_ROT_ZEILE = re.compile(r"^(?:FAILED|ERROR) (.+?(?:\[.*?\])?)(?: - .*)?$", re.MULTILINE)
#: Fortschritt bei ``console_output_style=count``: Zeile endet auf ``[k/N]``.
_FORTSCHRITT = re.compile(r"\[\s*(\d+)/(\d+)\]$")
#: Abschnitts-Kopf (``==== FAILURES ====`` …): ab hier Berichte, kein Fortschritt mehr.
_ABSCHNITT = re.compile(r"^={3,} .+ ={3,}$")


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
        # Kommandozeile schlägt ``addopts``/``PYTEST_ADDOPTS``:
        # - ``--maxfail=0`` übersteuert ``-x``/``--maxfail=N``.
        # - frischer ``cache_dir`` je Prüfung: ``--lf``/``--sw`` aus ``addopts`` wählen nie anhand
        #   eines alten Laufs Tests ab (sonst Grün trotz rotem Test); die Wiederholung erbt ihn.
        # - ``console_output_style=count`` belegt ``[k/N]``: Grundlage für ``gelaufen``.
        # ``--sw`` bricht trotzdem nach dem ersten Rot ab (Exit 2 = rot, nie Wackler).
        # ponytail: Cache-Ordner bleiben im Temp-Verzeichnis liegen, Aufräumen im Leiter, wenn es stört.
        cache = tempfile.mkdtemp(prefix="to_spawn_pytest_cache_")
        return Plan(
            werkzeug=f"pytest@{version}",
            befehl=[
                python,
                "-m",
                "pytest",
                "-q",
                "--maxfail=0",
                "-o",
                f"cache_dir={cache}",
                "-o",
                "console_output_style=count",
            ],
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

    def gelaufen(self, ausgabe: str) -> int | None:
        # Letzter Fortschritt vor dem ersten Bericht-Abschnitt; nur ``[N/N]`` belegt „alle
        # ausgewählten liefen“. Fehlt er (``-s``, Abbruch, ``Interrupted``), ist es unbelegt.
        letzter = None
        for zeile in ausgabe.splitlines():
            if _ABSCHNITT.match(zeile) and "test session starts" not in zeile:
                break
            letzter = _FORTSCHRITT.search(zeile.rstrip()) or letzter
        if letzter is None or letzter.group(1) != letzter.group(2):
            return None
        return int(letzter.group(2))


ADAPTER = PythonAdapter()
