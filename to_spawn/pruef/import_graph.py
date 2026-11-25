"""Statischer AST-Import-Graph für Python-Testauswahl ohne Einzelpaket-Zwang (#795, #580).

Ermittelt ausgehend von geänderten Dateien per ``ast``-Analyse alle Tests, die diese Module
direkt oder transitiv importieren — plattformübergreifend für Linux und Windows, ohne externe
Abhängigkeiten und unabhängig davon, ob ein Repo ein Paket, mehrere Pakete, ein ``src/``-Layout
oder lose Skripte oben im Repo hat.

Schnittstellen:
- :func:`baue_graph` — scannt Python-Dateien (mit in-place-Prüfung ausgeschlossener Ordner) und
  baut den statischen Vorwärts-/Rückwärts-Import-Graph.
- :func:`auswaehlen` — bestimmt alle direkt oder transitiv betroffenen Testdateien und misst die
  lokale Laufzeit.
- :func:`ziele_mit_roten` — kombiniert zuletzt rote Tests (zuerst) mit der Ticket-Auswahl für die
  „Nach Rot“-Regel, ohne doppelte Läufe und ohne Absturz bei gelöschten/umbenannten Tests.
- CLI: ``python -m to_spawn.pruef.import_graph --repo <pfad> (--basis <ref> | --geaendert <pfad> …)``.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import json
import logging
import os
import sys
import time
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from to_spawn.pruef import regeln

log = logging.getLogger("to_spawn.pruef")

#: Ordner, die nie zum statischen Import-Graph eines Repos gehören (Werkzeug-Orte kommen zusätzlich).
_IGNORIERT_ORDNER: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        ".env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "node_modules",
        "build",
        "dist",
        ".eggs",
        "deps",
    }
)
#: Standard-Testordner, wenn keine ``testpaths`` in der pytest-Konfig stehen.
_STANDARD_TEST_ORDNER: tuple[str, ...] = ("tests", "test")
#: Test-Wurzelordner, deren Hilfsmodule lokal ohne Ordner-Präfix importiert werden können.
_TEST_HILFS_ORDNER: frozenset[str] = frozenset({"tests", "test"})


@dataclass(frozen=True)
class ImportGraph:
    """Statischer Python-Import-Graph eines Repos (alle Pfade relativ als POSIX-Text)."""

    #: Alle gescannten Python-Dateien (sortiert).
    dateien: tuple[str, ...]
    #: Rückwärts-Kanten: ``ziel_pfad -> {dateien, die ziel_pfad statisch importieren}``.
    nutzer: dict[str, frozenset[str]]
    #: Vorwärts-Kanten: ``pfad -> {statisch importierte ziel_pfade}``.
    importe: dict[str, frozenset[str]]
    #: Gemessene Zeit für Dateiscan + AST-Analyse in Sekunden.
    dauer_s: float


@dataclass(frozen=True)
class Auswahl:
    """Ergebnis einer Import-Graph-Testauswahl (#795, #580)."""

    #: Betroffene Testdateien (POSIX-Relativpfade, sortiert).
    tests: tuple[str, ...]
    #: Alle erkannten Testdateien im Suchbereich (POSIX-Relativpfade, sortiert).
    alle_tests: tuple[str, ...]
    #: Geänderte Dateien als Startpunkte (POSIX-Relativpfade, sortiert).
    geaendert: tuple[str, ...]
    #: Alle transitiv betroffenen Python-Dateien (inkl. Startpunkte und Tests, sortiert).
    betroffene_module: tuple[str, ...]
    #: Gemessene Gesamtlaufzeit für Graph + Auswahl in Sekunden.
    dauer_s: float


def _werkzeug_orte() -> frozenset[str]:
    from to_spawn.pruef import leiter

    return frozenset(leiter.WERKZEUG_ORTE)


def _ordner_ignoriert(name: str, werkzeug: frozenset[str]) -> bool:
    return name in _IGNORIERT_ORDNER or name in werkzeug or name.startswith(".") or name.endswith(".egg-info")


def _modul_aus_teilen(teile: Sequence[str]) -> str:
    liste = list(teile)
    if liste and liste[-1] == "__init__":
        liste = liste[:-1]
    return ".".join(liste)


def _py_dateien(repo: Path) -> list[str]:
    """Alle Python-Dateien unter ``repo`` als sortierte POSIX-Relativpfade (ohne ignorierte Ordner)."""
    werkzeug = _werkzeug_orte()
    wurzel = repo.resolve()
    gefunden: list[str] = []
    for aktuell, ordner, dateien in os.walk(wurzel, topdown=True, followlinks=False):
        ordner[:] = sorted(name for name in ordner if not _ordner_ignoriert(name, werkzeug))
        rel_ordner = Path(aktuell).relative_to(wurzel)
        for datei in sorted(dateien):
            if not datei.endswith(".py"):
                continue
            rel = (rel_ordner / datei).as_posix() if rel_ordner != Path(".") else datei
            gefunden.append(rel)
    return sorted(gefunden)


def _modul_index(dateien: Sequence[str]) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    """Ordnet jedem möglichen Python-Modulnamen die zugehörigen Dateipfade zu (Mehrpaket + ``src/`` + Tests).

    Nicht-Test-Dateien werden zuerst registriert; lokale Test-Helfer unter ``tests/`` bzw. ``test/``
    bekommen ihren verkürzten Namen (ohne ``tests.``) nur, wenn dieser noch nicht durch Produktivcode
    belegt ist — so überschattet ein Test-Helfer nie ein gleichnamiges Anwendungs-Modul.
    """
    modul_zu_pfaden: dict[str, set[str]] = {}
    pfad_zu_modulen: dict[str, list[str]] = {pfad: [] for pfad in dateien}

    def _merke(mod: str, pfad: str) -> None:
        if not mod:
            return
        modul_zu_pfaden.setdefault(mod, set()).add(pfad)
        if mod not in pfad_zu_modulen[pfad]:
            pfad_zu_modulen[pfad].append(mod)

    # 1. Pakete (``__init__.py``) haben in Python immer Vorrang vor gleichnamigen ``.py``-Skripten
    #    (z. B. ``to_spawn/__init__.py`` vor ``to_spawn.py``).
    paket_namen: set[str] = set()
    for pfad in dateien:
        p = PurePosixPath(pfad)
        if p.name != "__init__.py":
            continue
        teile = list(p.with_suffix("").parts)
        mod = _modul_aus_teilen(teile)
        if mod:
            _merke(mod, pfad)
            paket_namen.add(mod)
        if len(teile) >= 2 and teile[0] == "src":
            src_mod = _modul_aus_teilen(teile[1:])
            if src_mod:
                _merke(src_mod, pfad)
                paket_namen.add(src_mod)

    # 2. Übrige Module ab Repo-Wurzel sowie ``src/``-Layout (ohne von Paketen verdeckte Skripte).
    for pfad in dateien:
        p = PurePosixPath(pfad)
        if p.name == "__init__.py":
            continue
        teile = list(p.with_suffix("").parts)
        mod = _modul_aus_teilen(teile)
        if mod and mod not in paket_namen:
            _merke(mod, pfad)
        if len(teile) >= 2 and teile[0] == "src":
            src_mod = _modul_aus_teilen(teile[1:])
            if src_mod and src_mod not in paket_namen:
                _merke(src_mod, pfad)

    # 3. Lokale Hilfsmodule unter ``tests/`` oder ``test/`` (pytest setzt Test-Ordner auf sys.path).
    belegt = frozenset(modul_zu_pfaden)
    for pfad in dateien:
        teile = list(PurePosixPath(pfad).with_suffix("").parts)
        if len(teile) >= 2 and teile[0] in _TEST_HILFS_ORDNER:
            kurz = _modul_aus_teilen(teile[1:])
            if kurz and kurz not in belegt:
                _merke(kurz, pfad)

    return modul_zu_pfaden, pfad_zu_modulen


def _import_anweisungen(baum: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    """Sammelt alle ``import``/``from … import``-Anweisungen ohne Ausdrucks-Unterbäume zu durchlaufen."""
    treffer: list[ast.Import | ast.ImportFrom] = []
    stapel: list[Sequence[ast.stmt]] = [baum.body]
    while stapel:
        block = stapel.pop()
        for knoten in block:
            if isinstance(knoten, (ast.Import, ast.ImportFrom)):
                treffer.append(knoten)
            elif isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.With, ast.AsyncWith)):
                stapel.append(knoten.body)
            elif isinstance(knoten, (ast.If, ast.For, ast.AsyncFor, ast.While)):
                stapel.append(knoten.body)
                if knoten.orelse:
                    stapel.append(knoten.orelse)
            elif isinstance(knoten, (ast.Try, ast.TryStar)):
                stapel.append(knoten.body)
                for handler in knoten.handlers:
                    stapel.append(handler.body)
                if knoten.orelse:
                    stapel.append(knoten.orelse)
                if knoten.finalbody:
                    stapel.append(knoten.finalbody)
            elif isinstance(knoten, ast.Match):
                for fall in knoten.cases:
                    stapel.append(fall.body)
    return treffer


def _statische_importe(
    datei: Path,
    pfad: str,
    eigene_module: Sequence[str],
    modul_zu_pfaden: dict[str, set[str]],
) -> set[str]:
    """Statische Import-Ziele (Dateipfade) einer Python-Datei per AST; dynamische Strings bleiben unsichtbar."""
    try:
        quelle = datei.read_text(encoding="utf-8", errors="replace")
        baum = ast.parse(quelle, filename=pfad)
    except (OSError, SyntaxError, ValueError) as fehler:
        log.debug("Import-Graph: %s nicht lesbar/parsbar (%s)", pfad, fehler)
        return set()

    ist_paket = PurePosixPath(pfad).name == "__init__.py"
    ziele: set[str] = set()

    def _aufloesen(name: str) -> None:
        if name in modul_zu_pfaden:
            for ziel in modul_zu_pfaden[name]:
                if ziel != pfad:
                    ziele.add(ziel)

    for knoten in _import_anweisungen(baum):
        if isinstance(knoten, ast.Import):
            for eintrag in knoten.names:
                _aufloesen(eintrag.name)
        elif isinstance(knoten, ast.ImportFrom):
            if knoten.level:
                for basis_mod in eigene_module:
                    basis_teile = basis_mod.split(".") if ist_paket else basis_mod.split(".")[:-1]
                    hoch = len(basis_teile) - (knoten.level - 1)
                    if hoch < 0:
                        continue
                    vorsatz = basis_teile[:hoch]
                    mod = ".".join([*vorsatz, knoten.module]) if knoten.module else ".".join(vorsatz)
                    _aufloesen(mod)
                    for eintrag in knoten.names:
                        if eintrag.name != "*":
                            _aufloesen(f"{mod}.{eintrag.name}" if mod else eintrag.name)
            else:
                mod = knoten.module or ""
                _aufloesen(mod)
                for eintrag in knoten.names:
                    if eintrag.name != "*":
                        _aufloesen(f"{mod}.{eintrag.name}" if mod else eintrag.name)

    return ziele


def baue_graph(repo: Path, *, extra_dateien: Iterable[str] = ()) -> ImportGraph:
    """Scannt alle Python-Dateien in ``repo`` und baut den statischen AST-Import-Graph.

    ``extra_dateien`` nimmt auch gelöschte/umbenannte ``.py``-Pfade aus dem Diff als virtuelle
    Zielknoten auf, damit verbliebene Dateien mit Alt-Import weiterhin gefunden werden.
    """
    start = time.perf_counter()
    dateien = _py_dateien(repo)
    vorhanden = frozenset(dateien)
    virtuell = sorted(
        {norm for pfad in extra_dateien if (norm := regeln.normal(pfad)).endswith(".py") and norm not in vorhanden}
    )
    alle = sorted([*dateien, *virtuell])
    modul_zu_pfaden, pfad_zu_modulen = _modul_index(alle)

    importe_roh: dict[str, set[str]] = {pfad: set() for pfad in dateien}
    nutzer_roh: dict[str, set[str]] = {pfad: set() for pfad in alle}

    for pfad in dateien:
        ziele = _statische_importe(repo / pfad, pfad, pfad_zu_modulen[pfad], modul_zu_pfaden)
        importe_roh[pfad] = ziele
        for ziel in ziele:
            nutzer_roh[ziel].add(pfad)

    return ImportGraph(
        dateien=tuple(dateien),
        nutzer={k: frozenset(v) for k, v in nutzer_roh.items()},
        importe={k: frozenset(v) for k, v in importe_roh.items()},
        dauer_s=round(time.perf_counter() - start, 4),
    )


def _testpaths_aus_konfig(repo: Path) -> tuple[str, ...]:
    """Liest konfigurierte ``testpaths`` aus ``pytest.ini``, ``pyproject.toml`` oder ``setup.cfg``."""
    ini = repo / "pytest.ini"
    if ini.is_file():
        parser = configparser.ConfigParser()
        try:
            parser.read(ini, encoding="utf-8")
            if parser.has_option("pytest", "testpaths"):
                werte = [regeln.normal(teil) for teil in parser.get("pytest", "testpaths").split() if teil.strip()]
                vorhanden = tuple(w.rstrip("/") for w in werte if (repo / w).exists())
                if vorhanden:
                    return vorhanden
        except (OSError, configparser.Error) as fehler:
            log.debug("pytest.ini unlesbar (%s): %s", ini, fehler)

    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            daten = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            optionen = daten.get("tool", {}).get("pytest", {}).get("ini_options", {})
            roh = optionen.get("testpaths") if isinstance(optionen, dict) else None
            if isinstance(roh, str):
                werte = [regeln.normal(teil) for teil in roh.split() if teil.strip()]
            elif isinstance(roh, list):
                werte = [regeln.normal(str(teil)) for teil in roh if isinstance(teil, str) and teil.strip()]
            else:
                werte = []
            vorhanden = tuple(w.rstrip("/") for w in werte if (repo / w).exists())
            if vorhanden:
                return vorhanden
        except (OSError, ValueError, AttributeError) as fehler:
            log.debug("pyproject.toml unlesbar (%s): %s", pyproject, fehler)

    setup_cfg = repo / "setup.cfg"
    if setup_cfg.is_file():
        parser = configparser.ConfigParser()
        try:
            parser.read(setup_cfg, encoding="utf-8")
            if parser.has_option("tool:pytest", "testpaths"):
                werte = [regeln.normal(teil) for teil in parser.get("tool:pytest", "testpaths").split() if teil.strip()]
                vorhanden = tuple(w.rstrip("/") for w in werte if (repo / w).exists())
                if vorhanden:
                    return vorhanden
        except (OSError, configparser.Error) as fehler:
            log.debug("setup.cfg unlesbar (%s): %s", setup_cfg, fehler)

    return ()


def test_wurzeln(repo: Path, test_pfade: Sequence[str] | None = None) -> tuple[str, ...]:
    """Bestimmt die Suchwurzeln für Testdateien in ``repo`` (leer = ganzes Repo)."""
    if test_pfade is not None:
        return tuple(regeln.normal(p).rstrip("/") for p in test_pfade if p.strip())
    konfig = _testpaths_aus_konfig(repo)
    if konfig:
        return konfig
    standard = tuple(ordner for ordner in _STANDARD_TEST_ORDNER if (repo / ordner).is_dir())
    return standard


def ist_test_datei(pfad: str, wurzeln: Sequence[str] = ()) -> bool:
    """True, wenn ``pfad`` eine pytest-Testdatei (``test_*.py`` oder ``*_test.py``) unter ``wurzeln`` ist."""
    norm = regeln.normal(pfad)
    name = PurePosixPath(norm).name
    if not (name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))):
        return False
    if not wurzeln:
        return True
    return any(norm == w or norm.startswith(f"{w}/") for w in wurzeln)


def geaenderte_dateien(repo: Path, basis: str) -> list[str] | None:
    """Geänderte Dateien seit ``basis`` über die eine Stelle :func:`sofort.geaendert`."""
    from to_spawn.pruef import sofort

    return sofort.geaendert(repo, basis)


def auswaehlen(
    repo: Path,
    geaendert: Iterable[str] | None = None,
    *,
    basis: str | None = None,
    test_pfade: Sequence[str] | None = None,
    graph: ImportGraph | None = None,
) -> Auswahl:
    """Wählt alle Tests, die mindestens ein geändertes Modul direkt oder transitiv importieren."""
    start = time.perf_counter()
    if geaendert is None:
        if basis is None:
            raise ValueError("import_graph.auswaehlen: geaendert oder basis erforderlich")
        diff_pfade = geaenderte_dateien(repo, basis)
        if diff_pfade is None:
            raise RuntimeError(f"geänderte Dateien gegen {basis!r} in {repo} nicht bestimmbar")
        roh_pfade = diff_pfade
    else:
        roh_pfade = list(geaendert)

    norm_geaendert = tuple(sorted({regeln.normal(p) for p in roh_pfade if p and p.strip()}))
    fertiger_graph = (
        graph if graph is not None else baue_graph(repo, extra_dateien=(p for p in norm_geaendert if p.endswith(".py")))
    )
    wurzeln = test_wurzeln(repo, test_pfade)
    alle_tests = tuple(p for p in fertiger_graph.dateien if ist_test_datei(p, wurzeln))
    bekannt = frozenset(fertiger_graph.dateien) | frozenset(fertiger_graph.nutzer)

    betroffen: set[str] = {p for p in norm_geaendert if p in bekannt}
    # Wird eine Paket-``__init__.py`` direkt geändert, läuft sie beim Import jedes Untermoduls mit.
    for pfad in norm_geaendert:
        p = PurePosixPath(pfad)
        if p.name == "__init__.py" and p.parent != PurePosixPath("."):
            praefix = f"{p.parent.as_posix()}/"
            for kandidat in fertiger_graph.dateien:
                if kandidat.startswith(praefix):
                    betroffen.add(kandidat)

    warteschlange = list(betroffen)
    while warteschlange:
        aktuell = warteschlange.pop()
        for abhaengig in fertiger_graph.nutzer.get(aktuell, ()):
            if abhaengig not in betroffen:
                betroffen.add(abhaengig)
                warteschlange.append(abhaengig)

    tests = tuple(p for p in alle_tests if p in betroffen)
    dauer_s = round(time.perf_counter() - start, 4)
    return Auswahl(
        tests=tests,
        alle_tests=alle_tests,
        geaendert=norm_geaendert,
        betroffene_module=tuple(sorted(betroffen)),
        dauer_s=dauer_s,
    )


def _knoten_vorhanden(datei: Path, knoten: str) -> bool:
    """Prüft per AST, ob ein pytest-Testknoten (z. B. ``TestA::test_x[1]``) in ``datei`` existiert."""
    teile = [teil.split("[", 1)[0] for teil in knoten.split("::") if teil]
    if not teile:
        return False
    try:
        baum = ast.parse(datei.read_text(encoding="utf-8", errors="replace"), filename=str(datei))
    except (OSError, SyntaxError, ValueError):
        return False

    koerper: Sequence[ast.stmt] = baum.body
    for idx, name in enumerate(teile):
        letzter = idx == len(teile) - 1
        treffer: ast.AST | None = None
        for anweisung in koerper:
            if letzter and isinstance(anweisung, (ast.FunctionDef, ast.AsyncFunctionDef)) and anweisung.name == name:
                treffer = anweisung
                break
            if not letzter and isinstance(anweisung, ast.ClassDef) and anweisung.name == name:
                treffer = anweisung
                break
        if treffer is None:
            return False
        if isinstance(treffer, ast.ClassDef):
            koerper = treffer.body
    return True


def ziele_mit_roten(
    repo: Path,
    auswahl_tests: Sequence[str],
    rote_tests: Sequence[str],
) -> tuple[str, ...]:
    """Kombiniert zuletzt rote Tests (zuerst!) mit der Ticket-Auswahl für pytest (#795).

    - Zuletzt rote Tests stehen immer vor der übrigen Auswahl.
    - Liegt die Datei eines roten Tests ohnehin in ``auswahl_tests``, wird die ganze Datei einmal
      vorn eingereiht (keine doppelte Ausführung derselben Tests).
    - Wurde ein roter Test umbenannt, fällt die Prüfung auf dessen noch vorhandene Testdatei zurück;
      gelöschte Dateien werden übersprungen (kein pytest-Exit 4).
    """
    norm_auswahl = tuple(dict.fromkeys(regeln.normal(p) for p in auswahl_tests if p and p.strip()))
    auswahl_menge = frozenset(norm_auswahl)

    ziele: list[str] = []
    abgedeckte_dateien: set[str] = set()
    gesehen: set[str] = set()

    for roh in rote_tests:
        eintrag = roh.strip()
        if not eintrag:
            continue
        datei_roh, trenner, knoten = eintrag.partition("::")
        datei = regeln.normal(datei_roh)
        datei_pfad = repo / datei
        if not datei_pfad.is_file():
            log.info("Nach Rot: gemerkter Test %s existiert nicht mehr (Datei fehlt)", eintrag)
            continue
        if datei in auswahl_menge:
            if datei not in abgedeckte_dateien:
                ziele.append(datei)
                abgedeckte_dateien.add(datei)
                gesehen.add(datei)
            continue
        if trenner and knoten:
            if _knoten_vorhanden(datei_pfad, knoten):
                ziel = f"{datei}::{knoten}"
                if ziel not in gesehen and datei not in abgedeckte_dateien:
                    ziele.append(ziel)
                    gesehen.add(ziel)
            elif datei not in abgedeckte_dateien:
                log.info("Nach Rot: Test %s umbenannt/entfernt — prüfe ganze Datei %s", eintrag, datei)
                ziele.append(datei)
                abgedeckte_dateien.add(datei)
                gesehen.add(datei)
        elif datei not in abgedeckte_dateien:
            ziele.append(datei)
            abgedeckte_dateien.add(datei)
            gesehen.add(datei)

    for pfad in norm_auswahl:
        if pfad not in abgedeckte_dateien and pfad not in gesehen:
            ziele.append(pfad)
            abgedeckte_dateien.add(pfad)
            gesehen.add(pfad)

    return tuple(ziele)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI für lokale Aufrufe und CI-Vorlagen (``python -m to_spawn.pruef.import_graph``)."""
    parser = argparse.ArgumentParser(
        prog="python -m to_spawn.pruef.import_graph",
        description="Wählt betroffene Python-Tests per AST-Import-Graph aus.",
    )
    parser.add_argument("--repo", type=Path, default=Path("."), help="Pfad zum Repository (Vorgabe: .)")
    quelle = parser.add_mutually_exclusive_group(required=True)
    quelle.add_argument("--basis", help="Git-Basis-Ref für die Änderungsermittlung (z. B. origin/main)")
    quelle.add_argument("--geaendert", nargs="+", help="Geänderte Relativpfade statt git diff")
    parser.add_argument("--test-pfad", action="append", default=None, help="Suchordner für Tests (mehrfach möglich)")
    parser.add_argument("--mit-roten", action="store_true", help="Zuletzt rote Tests aus dem Protokoll voranstellen")
    parser.add_argument("--json", action="store_true", dest="als_json", help="Ergebnis als JSON ausgeben")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    try:
        wahl = auswaehlen(repo, geaendert=args.geaendert, basis=args.basis, test_pfade=args.test_pfad)
    except (OSError, RuntimeError, ValueError) as fehler:
        sys.stderr.write(f"Fehler: {fehler}\n")
        return 2

    rote: tuple[str, ...] = ()
    ziele = wahl.tests
    if args.mit_roten:
        from to_spawn.pruef import protokoll

        rote = protokoll.letzte_rote(repo, "py")
        ziele = ziele_mit_roten(repo, wahl.tests, rote)

    if args.als_json:
        daten = {
            "tests": list(wahl.tests),
            "alle_tests": list(wahl.alle_tests),
            "geaendert": list(wahl.geaendert),
            "rote_tests": list(rote),
            "ziele": list(ziele),
            "dauer_s": wahl.dauer_s,
        }
        sys.stdout.write(json.dumps(daten, ensure_ascii=False) + "\n")
    else:
        for ziel in ziele:
            sys.stdout.write(ziel + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
