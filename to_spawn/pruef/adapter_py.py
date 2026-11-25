"""Python-Adapter: erkennt ein Python-Repo und weiß, wie pytest es voll prüft (Paket-intern, #99)."""

from __future__ import annotations

import itertools
import logging
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

from to_spawn.pruef import ausfuehren, leiter, wackler
from to_spawn.pruef.leiter import Plan

log = logging.getLogger("to_spawn.pruef")

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
#: ``--junitxml=…`` im Befehl: je Lauf ein eigener maschinenlesbarer Bericht (#149).
_BERICHT_PREFIX = "--junitxml="
#: Abbruch-Zeile von pytest (``pytest.exit(..., returncode=0)`` oder ``Interrupted``).
_ABBRUCH = re.compile(r"^!{3,}.*(?:Interrupted|_pytest\.outcomes\.Exit).*!{3,}$", re.MULTILINE)


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


def _ohne_xdist(befehl: list[str]) -> list[str]:
    """Entfernt ``-n``/``--dist``, damit Nach-Rot-Ziele streng in Reihenfolge (Rote zuerst) laufen."""
    ergebnis: list[str] = []
    idx = 0
    while idx < len(befehl):
        if befehl[idx] in ("-n", "--dist"):
            idx += 2
            continue
        if befehl[idx].startswith(("-n=", "--dist=")):
            idx += 1
            continue
        ergebnis.append(befehl[idx])
        idx += 1
    return ergebnis


_cache_wurzel: tempfile.TemporaryDirectory[str] | None = None
_bericht_nr = itertools.count(1)


def _wurzel() -> str:
    """Prozessweiter Temp-Ordner für pytest-Caches und -Berichte; räumt beim Prozessende auf (#355).

    Alle liegen in einem Ordner ``to_spawn_pytest_cache_*`` je Prozess, den ``TemporaryDirectory``
    beim Prozessende löscht — nach Erfolg, Ausnahme oder Zeitgrenze im Lauf. Eine Prüfung = ein
    Prozess (``to_spawn.py pruefen``), Wiederholung und zweiter voller Lauf leben darin.
    ponytail: bleibt bei SIGKILL liegen; je Prüfung räumen (Kontext in ``leiter.stufen``), wenn ein
    Dauer-Prozess viele Prüfungen fährt.
    """
    global _cache_wurzel
    if _cache_wurzel is None or not Path(_cache_wurzel.name).is_dir():
        _cache_wurzel = tempfile.TemporaryDirectory(prefix="to_spawn_pytest_cache_", ignore_cleanup_errors=True)
    return _cache_wurzel.name


def _frischer_cache() -> str:
    """Neuer, leerer pytest-``cache_dir`` je Plan; der Adapter räumt ihn selbst (#355)."""
    return tempfile.mkdtemp(dir=_wurzel())


def _frischer_bericht() -> str:
    """Neuer, noch nicht angelegter JUnit-XML-Pfad je Lauf; fehlt er danach, ist nichts belegt (#149)."""
    return str(Path(_wurzel()) / f"bericht-{next(_bericht_nr)}.xml")


def _mit_bericht(befehl: list[str], pfad: str) -> list[str]:
    """Setzt oder ersetzt ``--junitxml=…`` im Befehl für einen eigenen Lauf."""
    eintrag = f"{_BERICHT_PREFIX}{pfad}"
    if any(teil.startswith(_BERICHT_PREFIX) for teil in befehl):
        return [eintrag if teil.startswith(_BERICHT_PREFIX) else teil for teil in befehl]
    return [*befehl, eintrag]


def _bericht_pfad(befehl: list[str]) -> Path | None:
    """Letzter ``--junitxml=…``-Pfad aus ``befehl``; ``None``, wenn keiner gesetzt ist."""
    pfad: str | None = None
    for idx, teil in enumerate(befehl):
        if teil.startswith(_BERICHT_PREFIX):
            pfad = teil[len(_BERICHT_PREFIX) :]
        elif teil == "--junitxml" and idx + 1 < len(befehl):
            pfad = befehl[idx + 1]
    return Path(pfad) if pfad else None


def _xml_zahl(knoten: ET.Element, feld: str, vorgabe: int | None = None) -> int | None:
    roh = knoten.get(feld)
    if roh is None:
        return vorgabe
    try:
        wert = int(roh)
    except ValueError:
        return None
    return wert if wert >= 0 else None


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
        bericht = _frischer_bericht()
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
                f"{_BERICHT_PREFIX}{bericht}",
                *(f"--ignore={ort}" for ort in leiter.WERKZEUG_ORTE),
                *parallel,
            ],
            fehler="",
        )

    def auswahl(
        self,
        repo: Path,
        basis: str,
        *,
        extra_tests: Sequence[str] = (),
        pfade: Sequence[str] | None = None,
    ) -> Plan:
        """Testauswahl per statischem AST-Import-Graph ohne Einzelpaket-Zwang (#795).

        Mit ``extra_tests`` (zuletzt rote Tests aus dem Protokoll) stehen diese streng vor den
        geänderten Ticket-Tests; ohne Treffer bleibt ``befehl=[]`` (0 betroffene Tests, kein
        unnötiger pytest-Start).
        """
        plan = self.voll(repo)
        if plan.fehler:
            return plan
        from to_spawn.pruef import import_graph

        geaendert = list(pfade) if pfade is not None else import_graph.geaenderte_dateien(repo, basis)
        if geaendert is None:
            return Plan(werkzeug=plan.werkzeug, befehl=[], fehler="geänderte Dateien unbekannt (git diff gescheitert)")
        wahl = import_graph.auswaehlen(repo, geaendert=geaendert)
        if extra_tests:
            ziele = import_graph.ziele_mit_roten(repo, wahl.tests, extra_tests)
            if not ziele:
                return Plan(
                    werkzeug=f"{plan.werkzeug}+import-graph",
                    befehl=[],
                    fehler="zuletzt rote Tests nicht mehr auffindbar",
                )
            basis_befehl = _ohne_xdist(plan.befehl)
        else:
            ziele = wahl.tests
            basis_befehl = plan.befehl
        if not ziele:
            return Plan(werkzeug=f"{plan.werkzeug}+import-graph", befehl=[], fehler="")
        return Plan(
            werkzeug=f"{plan.werkzeug}+import-graph",
            befehl=[*basis_befehl, *ziele],
            fehler="",
        )

    def zaehle(self, code: int, ausgabe: str) -> tuple[int | None, int | None]:
        # Exit 5 = nichts gewählt; sonst ``[N/N]`` plus optionales ``deselected`` aus der Schlusszeile.
        if code == 5:
            return 0, None
        n = self.gelaufen(ausgabe)
        schluss = _SCHLUSS.findall(ausgabe)
        abgewaehlt = re.search(r"(\d+) deselected", schluss[-1]) if schluss else None
        if n is None and abgewaehlt and not re.search(r"passed|failed|error|skipped|xfailed|xpassed", schluss[-1]):
            n = 0  # alles abgewählt: endet mit Exit 0 und „N deselected“
        if n is None:
            return None, None
        return n, (n + int(abgewaehlt.group(1)) if abgewaehlt else None)

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
        # (Exit 5) statt alles, dann bleibt es beim ersten Rot. Eigener JUnit-Bericht je Lauf.
        if code != 1:
            return None
        return [*_mit_bericht(befehl, _frischer_bericht()), "--lf", "--lfnf", "none"]

    def voll_nochmal_befehl(self, befehl: list[str]) -> list[str]:
        # Zweiter voller Lauf mit frischem ``cache_dir`` und frischem JUnit-Bericht: ``--lf``/``--sw``
        # aus ``addopts`` sehen weder den ersten Lauf noch die Wiederholung, also läuft wieder alles.
        cache = _frischer_cache()
        mit_cache = [f"{_CACHE_PREFIX}{cache}" if teil.startswith(_CACHE_PREFIX) else teil for teil in befehl]
        return _mit_bericht(mit_cache, _frischer_bericht())

    def bericht(self, befehl: list[str], ausgabe: str) -> wackler.Bericht | None:
        """Liest Test-Zähler aus dem maschinenlesbaren JUnit-XML-Bericht statt aus der Textzeile (#149)."""
        pfad = _bericht_pfad(befehl)
        if pfad is None:
            return None
        try:
            wurzel = ET.parse(pfad).getroot()
        except (OSError, ET.ParseError) as fehler:
            log.warning("JUnit-Bericht %s unlesbar: %s", pfad, fehler)
            return None
        if wurzel.tag == "testsuite":
            suites = [wurzel]
        elif wurzel.tag == "testsuites":
            suites = wurzel.findall("testsuite")
        else:
            return None
        if not suites:
            return None

        gesamt = fehler_anz = ausnahmen = uebersprungen = 0
        faelle: set[tuple[str, str]] = set()
        abgebrochen = bool(_ABBRUCH.search(ausgabe))
        for suite in suites:
            t = _xml_zahl(suite, "tests")
            f = _xml_zahl(suite, "failures", 0)
            e = _xml_zahl(suite, "errors", 0)
            s = _xml_zahl(suite, "skipped", 0)
            if t is None or f is None or e is None or s is None:
                return None
            gesamt += t
            fehler_anz += f
            ausnahmen += e
            uebersprungen += s
            for fall in suite.findall("testcase"):
                name = (fall.get("name") or "").strip()
                if not name:
                    # Offener Testfall ohne Namen entsteht bei ``pytest.exit(..., returncode=0)`` im Test.
                    abgebrochen = True
                    continue
                faelle.add((fall.get("classname") or "", name))

        bestanden = gesamt - fehler_anz - ausnahmen - uebersprungen
        if bestanden < 0:
            return None
        im_bericht = len(faelle)
        if abgebrochen:
            return wackler.Bericht(None, None, im_bericht=im_bericht, abgebrochen=True)
        fortschritt = self.gelaufen(ausgabe)
        gelaufen = fortschritt if fortschritt is not None and fortschritt == im_bericht else None
        return wackler.Bericht(gelaufen, bestanden, im_bericht=im_bericht, abgebrochen=False)

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
