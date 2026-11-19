"""Start-Tür: der einzige Ort, der echte Sessions, Terminal-Tabs und tmux-Fenster startet (#1406).

Für Aufrufer:

- ``finde(name)`` löst ein Programm (z. B. ``claude``) auf. Unter Test zuerst im
  Attrappen-Ordner ``TO_SPAWN_TEST_BIN`` — auch eine Datei ohne Endung, die ``shutil.which``
  auf Windows übersieht (dort fand es sonst die echte ``claude.EXE``, Vorfall #1406). Danach
  wie ``shutil.which``; nichts gefunden → ``name`` unverändert.
- ``starte(cmd, cwd=…)`` startet ``cmd`` als ``subprocess.Popen``, mit ``warten=True`` als
  ``subprocess.run``. Alle weiteren Schlüsselwort-Argumente (``env``, ``creationflags`` wie
  ``CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP``, ``stdin`` …) gehen unverändert durch.
  Scheitert der direkte Start mit ``OSError`` (Windows: ``claude`` ist ein ``.cmd``-Shim),
  startet die Tür einmal über die Shell.
- Unter Test (``PYTEST_CURRENT_TEST`` oder ``TO_SPAWN_TESTLAUF`` gesetzt — beide erben sich in
  Kind-Prozesse wie ``wache.py``/``bau.py``) startet die Tür nur Programme, die im Ordner
  ``TO_SPAWN_TEST_BIN`` liegen. Alles andere bricht mit ``StartGesperrt`` ab, bevor ein Prozess
  entsteht. Warum Umgebung statt monkeypatch: monkeypatch erreicht keine Kind-Prozesse.

``tests/test_starter_1406.py`` prüft per Quelltext-Suche, dass keine Start-Stelle an dieser
Tür vorbeigeht.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, overload

log = logging.getLogger("to_spawn.starter")

#: Schalter „läuft unter Test“; setzt ``tests/conftest.py`` für jeden Test.
TESTLAUF_ENV = "TO_SPAWN_TESTLAUF"
#: Ordner der Test-Attrappen; nur was hier liegt, darf unter Test starten.
TEST_BIN_ENV = "TO_SPAWN_TEST_BIN"
_TEST_MERKER = ("PYTEST_CURRENT_TEST", TESTLAUF_ENV)


class StartGesperrt(RuntimeError):
    """Echter Start unter Test: Programm liegt nicht im Attrappen-Ordner."""


def unter_test() -> bool:
    """``True``, wenn ein Test-Merker in der Umgebung steht (auch im Kind-Prozess)."""
    return any(os.environ.get(name) for name in _TEST_MERKER)


def _test_bin() -> Path | None:
    roh = os.environ.get(TEST_BIN_ENV, "").strip()
    return Path(roh) if roh else None


def finde(name: str) -> str:
    """Pfad zu ``name``; unter Test zuerst im Attrappen-Ordner, sonst wie ``shutil.which``."""
    ordner = _test_bin() if unter_test() else None
    if ordner is not None:
        treffer = shutil.which(name, path=str(ordner))
        if treffer:
            return treffer
        if (ordner / name).is_file():  # Attrappe ohne Endung (Windows übersieht sie)
            return str(ordner / name)
    return shutil.which(name) or name


def _windows_skript(pfad: Path) -> list[str]:
    """Attrappe ohne Windows-Endung mit Python-Shebang → über den laufenden Python starten."""
    # ponytail: nur Python-Shebang-Attrappen, andere Skript-Arten unter Windows bei Bedarf ergänzen
    endungen = {e.lower() for e in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").split(os.pathsep) if e}
    if os.name != "nt" or pfad.suffix.lower() in endungen:
        return [str(pfad)]
    try:
        with pfad.open(encoding="utf-8", errors="replace") as fh:
            kopf = fh.readline()
    except OSError:
        return [str(pfad)]
    if kopf.startswith("#!") and "python" in kopf:
        return [sys.executable, str(pfad)]
    return [str(pfad)]


def _nur_attrappe(argv: list[str]) -> list[str]:
    """Unter Test: ``argv`` nur zulassen, wenn das Programm im Attrappen-Ordner liegt."""
    ordner = _test_bin()
    programm = argv[0]
    if ordner is None:
        raise StartGesperrt(f"echter Start im Test verboten: {programm} ({TEST_BIN_ENV} fehlt)")
    pfad = Path(programm) if ("/" in programm or os.sep in programm) else Path(finde(programm))
    try:
        drin = pfad.is_file() and pfad.resolve().is_relative_to(ordner.resolve())
    except OSError:
        drin = False
    if not drin:
        raise StartGesperrt(f"echter Start im Test verboten: {programm} → {pfad} liegt nicht in {ordner}")
    return [*_windows_skript(pfad), *argv[1:]]


@overload
def starte(
    cmd: Sequence[str], *, cwd: Path | str | None = ..., warten: Literal[False] = ..., **popen_args: Any
) -> subprocess.Popen[Any]: ...
@overload
def starte(
    cmd: Sequence[str], *, cwd: Path | str | None = ..., warten: Literal[True], **popen_args: Any
) -> subprocess.CompletedProcess[Any]: ...
def starte(
    cmd: Sequence[str], *, cwd: Path | str | None = None, warten: bool = False, **popen_args: Any
) -> subprocess.Popen[Any] | subprocess.CompletedProcess[Any]:
    """Startet ``cmd`` (siehe Modul-Kommentar); unter Test nur Attrappen, sonst ``StartGesperrt``."""
    argv = [str(teil) for teil in cmd]
    if not argv:
        raise ValueError("starte: leerer Befehl")
    if unter_test():
        argv = _nur_attrappe(argv)
    ordner = str(cwd) if cwd is not None else None
    log.info("Start: %s (cwd %s)", argv[0], ordner)
    lauf: Any = subprocess.run if warten else subprocess.Popen
    try:
        return lauf(argv, cwd=ordner, **popen_args)  # noqa: S603 — Liste, kein Shell
    except OSError as fehler:
        # Windows: ``claude`` ist ein .cmd-Shim, der nur über die Shell startet.
        log.warning("Direkter Start von %s scheiterte (%s) — Notnagel über die Shell", argv[0], fehler)
        zeile = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        return lauf(zeile, cwd=ordner, shell=True, **popen_args)  # noqa: S602
