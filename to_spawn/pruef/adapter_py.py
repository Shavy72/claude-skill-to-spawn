"""Python-Adapter: erkennt ein Python-Repo und weiß, wie pytest es voll prüft (Paket-intern, #99)."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

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


def _schluss(ausgabe: str) -> str | None:
    """Die eine Schlusszeile von pytest; ``None``, wenn keine oder mehrere da sind.

    Mehrere heißt: eine stammt nicht von pytest (Testausgabe, ``atexit`` nach dem Ende, #149) —
    welche echt ist, steht nicht fest, also ist die Zählung unbelegt.
    """
    treffer = _SCHLUSS.findall(ausgabe)
    return treffer[0] if len(treffer) == 1 else None


def _junit_kennung(knoten: str) -> tuple[str, str, str]:
    """(file, classname, name) im JUnit-XML (``xunit1``) von pytest zur Knoten-Kennung.

    ``classname`` allein ist nicht eindeutig (wie ``mangle_test_address``: ``/`` und ``.`` werden
    beide ``.``, ``tests/a.b/`` gleich ``tests/a/b/``, #149 Fix 2); erst mit dem Datei-Pfad aus
    ``file`` ist es eine Kennung je Test. Steht die Testfunktion in einer anderen Datei als ihr
    Knoten (importiert), passt ``file`` nicht — dann ist sie nicht belegt (fail-closed).
    """
    pfad, klammer, parameter = knoten.partition("[")
    teile = pfad.split("::")
    datei = teile[0]
    teile[0] = re.sub(r"\.py$", "", datei.replace("/", "."))
    teile[-1] += klammer + parameter
    return datei, ".".join(teile[:-1]), teile[-1]


#: ``-o cache_dir=…`` im Befehl: je Lauf, der unabhängig vom vorigen sein muss, frisch.
_CACHE_PREFIX = "cache_dir="


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
        # - ``--verbosity=-1`` setzt die Stufe fest (statt ``-q``, das sich mit ``-q``/``-v`` aus
        #   ``addopts`` aufaddiert): ``-qq`` nähme die Schlusszeile weg, ``-v`` änderte den Fortschritt.
        # ``--sw`` bricht trotzdem nach dem ersten Rot ab (Exit 2 = rot, nie Wackler).
        # ponytail: Cache-Ordner bleiben im Temp-Verzeichnis liegen, Aufräumen im Leiter, wenn es stört.
        cache = tempfile.mkdtemp(prefix="to_spawn_pytest_cache_")
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
                "--verbosity=-1",
                "--maxfail=0",
                "-o",
                f"cache_dir={cache}",
                "-o",
                "console_output_style=count",
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
        schluss = _schluss(ausgabe)
        abgewaehlt = re.search(r"(\d+) deselected", schluss) if schluss else None
        if n is None and abgewaehlt and not re.search(r"passed|failed|error|skipped|xfailed|xpassed", schluss or ""):
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
        cache = tempfile.mkdtemp(prefix="to_spawn_pytest_cache_")
        return [f"{_CACHE_PREFIX}{cache}" if teil.startswith(_CACHE_PREFIX) else teil for teil in befehl]

    def mit_beleg(self, befehl: list[str], beleg: Path) -> list[str]:
        # JUnit-XML ist pytest-eigen; ``junit_prefix`` aus der Repo-Konfig fälschte die Kennungen,
        # ``junit_family=xunit1`` schreibt je Test den Datei-Pfad (``file``): eindeutige Kennung.
        return [*befehl, f"--junitxml={beleg}", "-o", "junit_prefix=", "-o", "junit_family=xunit1"]

    def alle_bestanden(self, beleg: Path, namen: set[str], gelaufen: int | None = None) -> bool:
        # Bestanden = ``testcase`` ohne failure/error/skipped (skip und xfail stehen als skipped).
        # Ein Name, der auch nur einmal anders als bestanden auftaucht, gilt nicht. Weicht die Zahl
        # der ``testcase`` von ``gelaufen`` ab, fehlt ein Test oder kam einer dazu: nicht belegt.
        try:
            wurzel = ElementTree.parse(beleg).getroot()
        except (OSError, ElementTree.ParseError):
            return False
        faelle = list(wurzel.iter("testcase"))
        if gelaufen is not None and len(faelle) != gelaufen:
            return False
        gut: set[tuple[str, str, str]] = set()
        schlecht: set[tuple[str, str, str]] = set()
        for fall in faelle:
            datei = fall.get("file", "").replace("\\", "/")
            kennung = (datei, fall.get("classname", ""), fall.get("name", ""))
            rot = any(kind.tag in ("failure", "error", "skipped") for kind in fall)
            (schlecht if rot else gut).add(kennung)
        return all(_junit_kennung(name) in gut - schlecht for name in namen)

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
        # Zahl ``passed`` aus der Schlusszeile; skip/xfail/xpass zählen nicht. Fehlt sie
        # (``-p no:terminal``, Absturz) oder ist sie nicht eindeutig, ist es unbelegt.
        schluss = _schluss(ausgabe)
        if schluss is None:
            return None
        treffer = re.search(r"(\d+) passed", schluss)
        return int(treffer.group(1)) if treffer else 0


ADAPTER = PythonAdapter()
