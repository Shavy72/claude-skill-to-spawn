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
  startet die Tür einmal über die Shell. ``notnagel`` engt das ein (z. B. ``FileNotFoundError``)
  oder schaltet es mit ``None`` ab — der Fehler geht dann unverändert an den Aufrufer.
- Unter Test (``PYTEST_CURRENT_TEST`` oder ``TO_SPAWN_TESTLAUF`` gesetzt — beide erben sich in
  Kind-Prozesse wie ``wache.py``/``bau.py``) startet die Tür nur Programme, die im Ordner
  ``TO_SPAWN_TEST_BIN`` liegen. Alles andere bricht mit ``StartGesperrt`` ab, bevor ein Prozess
  entsteht. Warum Umgebung statt monkeypatch: monkeypatch erreicht keine Kind-Prozesse.
  Einzige Ausnahme: ``[sys.executable, <Skript>]`` mit einem Skript aus ``_SKILL_KINDER``,
  ``TO_SPAWN_TEST_BIN`` oder dem Attrappen-Ordner des Skills ``tests/hilfen`` (tmux-/claude-
  Ersatz), wenn die Kind-Umgebung den Test-Merker trägt — das Kind startet dann selbst wieder
  nur durch diese Tür.
- Windows, Ziel ``.cmd``/``.bat`` (npm-Shim ``claude.cmd``): kein Direktstart, denn
  ``CreateProcess`` reicht die Zeile still an cmd.exe weiter und ``a&calc`` liefe als zweiter
  Befehl (BatBadBut). Die Tür nimmt dann immer den entschärften Weg über ``_cmd_zeile``.

``tests/test_starter_1406.py`` prüft per Quelltext-Suche, dass keine Start-Stelle an dieser
Tür vorbeigeht.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, overload

from to_spawn import prozessbaum

log = logging.getLogger("to_spawn.starter")

#: Schalter „läuft unter Test“; setzt ``tests/conftest.py`` für jeden Test.
TESTLAUF_ENV = "TO_SPAWN_TESTLAUF"
#: Ordner der Test-Attrappen; nur was hier liegt, darf unter Test starten.
TEST_BIN_ENV = "TO_SPAWN_TEST_BIN"
_TEST_MERKER = ("PYTEST_CURRENT_TEST", TESTLAUF_ENV)
_SKILL = Path(__file__).resolve().parent.parent
#: Attrappen des Skills selbst (``tmux_stub_284.py``, ``claude_aufzeichnung_316.py`` …): unter
#: Test als ``[sys.executable, <Datei hier>]`` erlaubt, wie Dateien aus ``TO_SPAWN_TEST_BIN``.
_TEST_HILFEN = _SKILL / "tests" / "hilfen"
_BATCH = {".cmd", ".bat"}


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


def _innen(pfad: Path, ordner: Path) -> bool:
    """Liegt der absolute ``pfad`` in ``ordner``? Fehlt die Datei, meldet der Start selbst
    ``FileNotFoundError`` — ein fehlendes Programm im Attrappen-Ordner startet nichts Echtes."""
    try:
        return pfad.is_absolute() and pfad.resolve().is_relative_to(ordner.resolve())
    except OSError:
        return False


#: Skill-Skripte, die unter Test als Kind starten dürfen (Skript → Pflicht-Unterbefehl oder ``None``).
#: Beide starten selbst nur durch diese Tür. Neue Einträge nur mit Grund.
_SKILL_KINDER: dict[Path, tuple[str, ...] | None] = {
    # takt: waechter_takt.starte_abgeloest; komplett: komplett_lauf._im_hintergrund (#455)
    _SKILL / "to_spawn.py": ("takt", "komplett"),
    _SKILL / "skripte" / "bau.py": None,  # capo._starte_folge_runde, probesitz (Bau-Lauf)
}


def _skill_python(argv: list[str], ordner: Path | None, env: Mapping[str, str] | None) -> bool:
    """``[sys.executable, <Skript aus _SKILL_KINDER, Attrappen-Ordner oder _TEST_HILFEN>, …]``
    mit Test-Merker im Kind."""
    if len(argv) < 2 or not any((os.environ if env is None else env).get(n) for n in _TEST_MERKER):
        return False
    try:
        ist_python = Path(argv[0]).resolve() == Path(sys.executable).resolve()
    except OSError:
        return False
    skript = Path(argv[1])
    if not ist_python or not skript.is_file():
        return False
    if any(o is not None and _innen(skript, o) for o in (ordner, _TEST_HILFEN)):
        return True
    for kind, befehl in _SKILL_KINDER.items():
        if skript.is_absolute() and skript.resolve() == kind.resolve():
            return befehl is None or (len(argv) > 2 and argv[2] in befehl)
    return False


def _nur_attrappe(argv: list[str], env: Mapping[str, str] | None = None) -> list[str]:
    """Unter Test: ``argv`` nur zulassen, wenn das Programm im Attrappen-Ordner liegt."""
    ordner = _test_bin()
    programm = argv[0]
    if _skill_python(argv, ordner, env):
        return argv
    if ordner is None:
        raise StartGesperrt(f"echter Start im Test verboten: {programm} ({TEST_BIN_ENV} fehlt)")
    pfad = Path(programm) if ("/" in programm or os.sep in programm) else Path(finde(programm))
    if not _innen(pfad, ordner):
        raise StartGesperrt(f"echter Start im Test verboten: {programm} → {pfad} liegt nicht in {ordner}")
    return [*_windows_skript(pfad), *argv[1:]]


def _endung(programm: str) -> str:
    """Endung des Programms, wie Windows es auflöst (``claude`` → ``.cmd`` beim npm-Shim)."""
    # Windows schneidet „.“/Leerzeichen am Ende ab: ``x.cmd.`` startet doch über cmd.exe (BatBadBut)
    return Path(Path(shutil.which(programm) or programm).name.rstrip(". ")).suffix.lower()


_CMD_SONDER = re.compile(r'([()%!^"<>&|])')


def _cmd_zeile(argv: list[str]) -> str:
    """Befehlszeile für ``cmd.exe /c``: jedes Argument nach MS-C-Regeln in ``"…"``, danach jedes
    cmd-Sonderzeichen (auch ``"`` und ``%``) mit ``^`` entschärft. So wertet cmd.exe ``& | < > ^ %``
    nie aus und ein ``.exe``-Ziel erhält genau ``argv``.

    Grenzen (``ValueError``, kein Start): ``\\n``/``\\r`` schneidet cmd.exe ab — immer abgelehnt.
    Ein Batch-Ziel (``.cmd``/``.bat`` wie der npm-Shim, oder Endung unklar) wertet ``%*`` ein
    zweites Mal aus; ein ``"`` im Argument öffnet dort eine Befehlsinjektion (BatBadBut) — dann
    abgelehnt. Prompts mit solchen Zeichen gehören per Datei/stdin übergeben."""
    if any("\n" in arg or "\r" in arg for arg in argv):
        raise ValueError("Notnagel: Zeilenumbruch im Argument würde von cmd.exe abgeschnitten")
    if _endung(argv[0]) not in {".exe", ".com"} and any(
        '"' in arg for arg in argv[1:]
    ):
        raise ValueError("Notnagel: Anführungszeichen im Argument eines Batch-Ziels (BatBadBut)")

    def quote(arg: str) -> str:
        teile: list[str] = []
        striche = 0
        for zeichen in arg:
            if zeichen == "\\":
                striche += 1
                continue
            teile.append("\\" * (2 * striche + 1) + '"' if zeichen == '"' else "\\" * striche + zeichen)
            striche = 0
        return '"' + "".join(teile) + "\\" * (2 * striche) + '"'

    return _CMD_SONDER.sub(r"^\1", " ".join(quote(arg) for arg in argv))


@overload
def starte(
    cmd: Sequence[str], *, cwd: Path | str | None = ..., warten: Literal[False] = ..., notnagel: type[OSError] | None = ..., **popen_args: Any
) -> subprocess.Popen[Any]: ...
@overload
def starte(
    cmd: Sequence[str], *, cwd: Path | str | None = ..., warten: Literal[True], notnagel: type[OSError] | None = ..., **popen_args: Any
) -> subprocess.CompletedProcess[Any]: ...
def starte(
    cmd: Sequence[str],
    *,
    cwd: Path | str | None = None,
    warten: bool = False,
    notnagel: type[OSError] | None = OSError,
    **popen_args: Any,
) -> subprocess.Popen[Any] | subprocess.CompletedProcess[Any]:
    """Startet ``cmd`` (siehe Modul-Kommentar); unter Test nur Attrappen, sonst ``StartGesperrt``."""
    argv = [str(teil) for teil in cmd]
    if not argv:
        raise ValueError("starte: leerer Befehl")
    if unter_test():
        argv = _nur_attrappe(argv, popen_args.get("env"))
    ordner = str(cwd) if cwd is not None else None
    popen_args = {**prozessbaum.fensterlos(), **popen_args}  # #658: Aufrufer-Flags gehen vor
    log.info("Start: %s (cwd %s)", argv[0], ordner)
    lauf: Any = subprocess.run if warten else subprocess.Popen
    if os.name == "nt" and _endung(argv[0]) in _BATCH:
        # Batch-Ziel: Direktstart reichte argv unentschärft an cmd.exe (BatBadBut, #1406)
        log.info("Batch-Ziel %s — Start über die entschärfte cmd-Zeile", argv[0])
        return lauf(_cmd_zeile(argv), cwd=ordner, shell=True, **popen_args)  # noqa: S602
    try:
        return lauf(argv, cwd=ordner, **popen_args)  # noqa: S603 — Liste, kein Shell
    except OSError as fehler:
        if notnagel is None or not isinstance(fehler, notnagel):
            raise
        # Windows: ``claude`` ist ein .cmd-Shim, der nur über die Shell startet.
        log.warning("Direkter Start von %s scheiterte (%s) — Notnagel über die Shell", argv[0], fehler)
        zeile = _cmd_zeile(argv) if os.name == "nt" else shlex.join(argv)
        return lauf(zeile, cwd=ordner, shell=True, **popen_args)  # noqa: S602
