"""Skill-Testsuite ausführen — Windows je Testdatei im Job-Objekt, Linux per xdist (#197).

Ein Skript für Windows und Linux im Skill-Repo (kein zweiter Job-Weg: nutzt allein
``to_spawn/sitzungsjob.py``):

- Ohne Wurzel-``conftest.py`` Abbruch mit Exit 64 (wie ``skripte/suite.sh``).
- Windows (oder ``--je-datei``): startet je Testdatei einen eigenen ``pytest``-Prozess
  (``CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`` über ``prozessbaum.ohne_fenster``)
  und bindet ihn per ``sitzungsjob.gebunden`` an ein Job-Objekt (``KILL_ON_JOB_CLOSE``).
  Endet die Datei oder wird der Lauf abgebrochen, sterben alle Kinder mit — auch
  Prozesse, die ``SIGTERM`` ignorieren oder aus Git Bash stammen.
- Linux/macOS (ohne ``--je-datei``): ein ``pytest -n 4``-Lauf wie ``skripte/suite.sh``.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

_SKILL = Path(__file__).resolve().parent.parent
if str(_SKILL) not in sys.path:
    sys.path.insert(0, str(_SKILL))

from to_spawn import prozessbaum, sitzungsjob, temp_ordner  # noqa: E402

# Windows-Konsole/Pipe nutzt ohne PYTHONUTF8 oft cp1252 — Meldungen mit Umlauten/Gedankenstrich brauchen UTF-8.
for _strom in (sys.stdout, sys.stderr):
    if hasattr(_strom, "reconfigure"):
        _strom.reconfigure(encoding="utf-8", errors="replace")

#: Ordner, die beim Datei-Sammeln immer übersprungen werden.
_IMMER_IGNORIEREN = frozenset(
    {".git", ".review", ".ruff_cache", ".pytest_cache", "__pycache__", ".venv", "node_modules"}
)

#: pytest-Schalter, die das nächste Argument als Wert verbrauchen (kein Test-Pfad).
_MIT_WERT = frozenset(
    {
        "-k",
        "-m",
        "-o",
        "-p",
        "-c",
        "-n",
        "-W",
        "--basetemp",
        "--junitxml",
        "--rootdir",
        "--confcutdir",
        "--maxfail",
        "--durations",
        "--durations-min",
        "--timeout",
        "--dist",
        "--numprocesses",
        "--ignore",
        "--ignore-glob",
        "--deselect",
    }
)


def _ignoriert_aus_conftest(wurzel: Path) -> set[str]:
    """Liest ``collect_ignore``-Namen aus ``wurzel/conftest.py`` (ohne Import/Ausführung)."""
    pfad = wurzel / "conftest.py"
    try:
        baum = ast.parse(pfad.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    namen: set[str] = set()
    for knoten in baum.body:
        if not isinstance(knoten, ast.Assign):
            continue
        if any(isinstance(ziel, ast.Name) and ziel.id == "collect_ignore" for ziel in knoten.targets):
            try:
                wert = ast.literal_eval(knoten.value)
            except (ValueError, SyntaxError):
                continue
            if isinstance(wert, (list, tuple, set)):
                namen.update(str(eintrag) for eintrag in wert)
    return namen


def _ist_testdatei(pfad: Path) -> bool:
    return pfad.suffix == ".py" and (pfad.name.startswith("test_") or pfad.name.endswith("_test.py"))


def _dateien_unter(ordner: Path, ignoriert: set[str]) -> list[Path]:
    gefunden: list[Path] = []
    for basis, unter, dateien in os.walk(ordner):
        unter[:] = sorted(u for u in unter if u not in ignoriert and not u.startswith("."))
        for name in sorted(dateien):
            kandidat = Path(basis) / name
            if _ist_testdatei(kandidat):
                gefunden.append(kandidat)
    return gefunden


def sammle_dateien(wurzel: Path, pfade: Sequence[str]) -> list[str]:
    """Testdateien (oder explizite Node-IDs) für den Datei-Lauf bestimmen."""
    ignoriert = set(_IMMER_IGNORIEREN) | _ignoriert_aus_conftest(wurzel)
    if not pfade:
        return [str(p.relative_to(wurzel)) for p in _dateien_unter(wurzel, ignoriert)]
    ziele: list[str] = []
    for eintrag in pfade:
        datei_teil = eintrag.split("::", 1)[0]
        kandidat = Path(datei_teil)
        aufgeloest = kandidat if kandidat.is_absolute() else (wurzel / kandidat)
        if aufgeloest.is_dir():
            ziele.extend(str(p) for p in _dateien_unter(aufgeloest, ignoriert))
        else:
            ziele.append(eintrag)
    return ziele


def zerlege_args(argv: Sequence[str], wurzel: Path) -> tuple[Path, bool, list[str], list[str]]:
    """Trennt eigene Schalter (``--wurzel``, ``--je-datei``), Test-Pfade und pytest-Schalter."""
    ziel_wurzel = wurzel
    je_datei = sys.platform == "win32"
    pfade: list[str] = []
    extra: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--wurzel" and i + 1 < len(argv):
            ziel_wurzel = Path(argv[i + 1]).resolve()
            i += 2
            continue
        if arg.startswith("--wurzel="):
            ziel_wurzel = Path(arg.split("=", 1)[1]).resolve()
            i += 1
            continue
        if arg == "--je-datei":
            je_datei = True
            i += 1
            continue
        if arg in _MIT_WERT and i + 1 < len(argv):
            extra.extend([arg, argv[i + 1]])
            i += 2
            continue
        if arg.startswith("-"):
            extra.append(arg)
            i += 1
            continue
        datei_teil = arg.split("::", 1)[0]
        if Path(datei_teil).exists() or (ziel_wurzel / datei_teil).exists():
            pfade.append(arg)
        else:
            extra.append(arg)
        i += 1
    return ziel_wurzel, je_datei, pfade, extra


def _datei_lauf(befehl: list[str], wurzel: Path, *, env: Mapping[str, str] | None = None) -> int:
    """Startet genau einen ``pytest``-Prozess gebunden an ``sitzungsjob.gebunden`` (#197)."""
    prozess = subprocess.Popen(befehl, cwd=wurzel, env=env, **prozessbaum.ohne_fenster())  # noqa: S603
    with sitzungsjob.gebunden(prozess):
        try:
            return prozess.wait()
        except KeyboardInterrupt:
            prozess.terminate()
            try:
                prozess.wait(timeout=5)
            except subprocess.TimeoutExpired:
                prozess.kill()
                prozess.wait(timeout=5)
            return 130


def _hat_parallel_schalter(extra: Sequence[str]) -> bool:
    return any(
        a == "-n" or a.startswith("-n") or a == "--numprocesses" or a.startswith("--numprocesses=") for a in extra
    )


def main(argv: Sequence[str] | None = None, *, wurzel: Path = _SKILL) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    ziel_wurzel, je_datei, pfade, extra = zerlege_args(args, wurzel)
    if not (ziel_wurzel / "conftest.py").is_file():
        print(
            "suite.py: keine Wurzel-conftest.py — läuft nur im Klon, nicht im installierten Skill.",
            file=sys.stderr,
        )
        return 64
    basis = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if not je_datei:
        parallel = [] if _hat_parallel_schalter(extra) else ["-n", "4", "--dist", "worksteal"]
        umg: dict[str, str] | None = None
        if not os.environ.get("TMPDIR"):
            temp_umg = temp_ordner.umgebung()
            if temp_umg:
                umg = {**os.environ, **temp_umg}
        if sys.platform != "win32" and "TO_SPAWN_SUITE_BASETEMP_RAEUMEN" not in os.environ:
            umg = {**(os.environ if umg is None else umg), "TO_SPAWN_SUITE_BASETEMP_RAEUMEN": "1"}
        return _datei_lauf([*basis, *parallel, *pfade, *extra], ziel_wurzel, env=umg)

    ziele = sammle_dateien(ziel_wurzel, pfade)
    if not ziele:
        return 5
    abbruch_bei_rot = "-x" in extra or "--exitfirst" in extra
    erster_fehler = 0
    lief_test = False
    for ziel in ziele:
        code = _datei_lauf([*basis, ziel, *extra], ziel_wurzel)
        if code == 130:
            return 130
        if code == 0:
            lief_test = True
        elif code != 5:
            lief_test = True
            if erster_fehler == 0:
                erster_fehler = code
            if abbruch_bei_rot:
                return erster_fehler
    if erster_fehler:
        return erster_fehler
    return 0 if lief_test else 5


if __name__ == "__main__":
    sys.exit(main())
