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
#: Schlusszeile ``[===] 1 failed, 2 passed, 1 skipped in 0.1s [===]``; Zeit optional.
_SCHLUSS = re.compile(
    r"^=*\s*((?:\d+ (?:passed|failed|skipped|xfailed|xpassed|errors?|warnings?|deselected|rerun)"
    r"(?:, )?)+)(?: in [\d.]+s.*?)?\s*=*$",
    re.MULTILINE,
)
#: ``-o cache_dir=…`` im Befehl: je Lauf, der unabhängig vom vorigen sein muss, frisch.
_CACHE_PREFIX = "cache_dir="
#: Orte des Bau-Werkzeugs im App-Repo: deren Tests sammelt die App-Prüfung nie ein, auch wenn die
#: App ``norecursedirs`` selbst setzt (#425 Abnahme 4, #415). Auswahl und
#: Wiederholung bauen auf ``voll`` auf und erben den Ausschluss.
_WERKZEUG_ORTE = (".to-spawn", ".claude")


#: Dateien, in denen ein Repo pytest-xdist als eigene Abhängigkeit nennt.
_XDIST_QUELLEN = (
    "requirements*.txt",
    "tests/requirements*.txt",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "pytest.ini",
)


def _will_xdist(repo: Path) -> bool:
    """Nennt das Repo pytest-xdist selbst? Nur dann sind seine Tests für parallele Läufe gedacht.

    Ohne diese Zusage liefe ein Repo mit reihenfolge- oder ressourcen-abhängigen Tests parallel
    anders als seriell (z. B. Grün statt Rot bei geteiltem Modul-Zustand).
    """
    for muster in _XDIST_QUELLEN:
        for datei in repo.glob(muster):
            try:
                if "xdist" in datei.read_text(encoding="utf-8", errors="replace").lower():
                    return True
            except OSError:
                continue
    return False


def _python(repo: Path) -> str:
    """Interpreter des Repos: eigenes ``.venv``/``venv`` (Linux und Windows), sonst der laufende."""
    for ordner in (".venv", "venv"):
        for teil in (("bin", "python"), ("Scripts", "python.exe")):
            kandidat = repo.joinpath(ordner, *teil)
            if kandidat.is_file() and os.access(kandidat, os.X_OK):
                return str(kandidat)
    return sys.executable


#: Ordner, die nie das Anwendungs-Paket sind.
_KEIN_PAKET = frozenset({"tests", "test", "docs", "build", "dist", "venv", "node_modules"})


def _paket(repo: Path) -> str:
    """Das eine Anwendungs-Paket oben im Repo (Ordner mit ``__init__.py``); "" = keins oder mehrere.

    ponytail: nur ein Paket (pytest-impacted kennt ein Modul); mehrere → voll statt ``--impacted-module=.``,
    das in großen Repos > 3 min braucht [DP].
    """
    treffer = [
        ordner.name
        for ordner in repo.iterdir()
        if ordner.is_dir()
        and not ordner.name.startswith(".")
        and ordner.name not in _KEIN_PAKET
        and (ordner / "__init__.py").is_file()
    ]
    return treffer[0] if len(treffer) == 1 else ""


_cache_wurzel: tempfile.TemporaryDirectory[str] | None = None


def _frischer_cache() -> str:
    """Neuer, leerer pytest-``cache_dir`` je Plan; der Adapter räumt ihn selbst (#355).

    Alle liegen in einem Ordner ``to_spawn_pytest_cache_*`` je Prozess, den ``TemporaryDirectory``
    beim Prozessende löscht — nach Erfolg, Ausnahme oder Zeitgrenze im Lauf. Eine Prüfung = ein
    Prozess (``to_spawn.py pruefen``), Wiederholung und zweiter voller Lauf leben darin.
    ponytail: bleibt bei SIGKILL liegen; je Prüfung räumen (Kontext in ``leiter.stufen``), wenn ein
    Dauer-Prozess viele Prüfungen fährt.
    """
    global _cache_wurzel
    if _cache_wurzel is None or not Path(_cache_wurzel.name).is_dir():
        _cache_wurzel = tempfile.TemporaryDirectory(prefix="to_spawn_pytest_cache_", ignore_cleanup_errors=True)
    return tempfile.mkdtemp(dir=_cache_wurzel.name)


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
        cache = _frischer_cache()
        # Parallel nur, wenn das Repo pytest-xdist selbst nennt, es im Repo-Interpreter da ist und
        # > 1 Worker passt: ``worksteal`` gibt freien Workern Tests der langsamen ab (statt
        # ``loadfile``: eine Datei hält einen Worker fest).
        # ponytail: Teilen nach Laufzeit (pytest-split) erst, wenn mehrere Gate-Rechner teilen (3b-6).
        anzahl = ausfuehren.worker()
        parallel = (
            ["-n", str(anzahl), "--dist", "worksteal"]
            if anzahl > 1 and _will_xdist(repo) and ausfuehren.lauf([python, "-c", "import xdist"], repo).code == 0
            else []
        )
        return Plan(
            werkzeug=f"pytest@{version}" + (f"+xdist×{anzahl}" if parallel else ""),
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
                *(f"--ignore={ort}" for ort in _WERKZEUG_ORTE),
                *parallel,
            ],
            fehler="",
        )

    def auswahl(self, repo: Path, basis: str) -> Plan:
        # pytest-impacted: git-Diff ``basis``..Stand + statischer Import-Graph (PRUEFTOR_VERGLEICH A).
        plan = self.voll(repo)
        if plan.fehler:
            return plan
        python = plan.befehl[0]
        probe = ausfuehren.lauf(
            [python, "-c", "import importlib.metadata as m; print(m.version('pytest-impacted'))"], repo
        )
        if probe.code != 0:
            return Plan(werkzeug=plan.werkzeug, befehl=[], fehler="pytest-impacted fehlt")
        paket = _paket(repo)
        if not paket:
            return Plan(werkzeug=plan.werkzeug, befehl=[], fehler="kein eindeutiges Paket für den Import-Graph")
        tests = ["--impacted-tests-dir=tests"] if (repo / "tests").is_dir() else []
        return Plan(
            werkzeug=f"{plan.werkzeug}+impacted@{probe.ausgabe.strip() or '?'}",
            befehl=[
                *plan.befehl,
                "--impacted",
                f"--impacted-module={paket}",
                "--impacted-git-mode=branch",
                f"--impacted-base-branch={basis}",
                "--impacted-no-merge-base",
                *tests,
            ],
            fehler="",
        )

    def zaehle(self, code: int, ausgabe: str) -> tuple[int | None, int | None]:
        # Exit 5 = nichts gewählt; sonst ``[N/N]`` plus ``deselected`` aus der Schlusszeile.
        if code == 5:
            return 0, None
        n = self.gelaufen(ausgabe)
        schluss = _SCHLUSS.findall(ausgabe)
        abgewaehlt = re.search(r"(\d+) deselected", schluss[-1]) if schluss else None
        if n is None and abgewaehlt and not re.search(r"passed|failed|error|skipped|xfailed|xpassed", schluss[-1]):
            n = 0  # alles abgewählt: pytest-impacted endet mit Exit 0 und „N deselected“
        if n is None:
            return None, None
        return n, n + (int(abgewaehlt.group(1)) if abgewaehlt else 0)

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

    def voll_nochmal_befehl(self, befehl: list[str]) -> list[str]:
        # Zweiter voller Lauf mit frischem ``cache_dir``: ``--lf``/``--sw`` aus ``addopts`` sehen
        # weder den ersten Lauf noch die Wiederholung, also läuft wieder alles.
        cache = _frischer_cache()
        return [f"{_CACHE_PREFIX}{cache}" if teil.startswith(_CACHE_PREFIX) else teil for teil in befehl]

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

    def bestanden(self, ausgabe: str) -> int | None:
        # Zahl ``passed`` aus der letzten Schlusszeile; skip/xfail/xpass zählen nicht. Fehlt die
        # Schlusszeile (``-p no:terminal``, Absturz), ist es unbelegt.
        schluss = _SCHLUSS.findall(ausgabe)
        if not schluss:
            return None
        treffer = re.search(r"(\d+) passed", schluss[-1])
        return int(treffer.group(1)) if treffer else 0


ADAPTER = PythonAdapter()
