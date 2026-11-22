"""Welche ``bash`` der Skill startet und wie — einzige Stelle dieses Wissens (#68).

Warum: Unter Windows trifft ein nacktes ``bash`` zuerst ``System32\\bash.exe`` (WSL-Stub ohne
``/bin/bash``), weil ``CreateProcess`` ``System32`` vor ``PATH`` absucht. Der Skill braucht dort
Git-Bash. Zweite Falle: Claude Code vererbt ``MSYS_NO_PATHCONV=1``; dann reicht Git-Bash
``/c/…``-Pfade (etwa aus ``~``) unverändert an Windows-Programme wie ``node``/``python``, die
sie als ``C:\\c\\…`` missverstehen. Linux/macOS: ``bash`` aus dem ``PATH``, Umgebung unverändert.

Tür für Aufrufer: ``lauf`` startet bash samt passender Umgebung — die Pfadsperre lässt sich so
nicht vergessen. ``bash`` nur, wer allein den Pfad braucht (Test-Helfer); ``ist_wsl_stub`` ist
die Regel, an der ein gefundener Pfad als WSL-Stub erkannt wird.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PureWindowsPath
from typing import Any

log = logging.getLogger("to_spawn.bash_wahl")

#: Standard-Installationsort von Git für Windows.
_GIT_BASH_STANDARD = "C:/Program Files/Git/bin/bash.exe"
#: Schalter, mit denen MSYS die Pfad-Umsetzung für Windows-Programme abschaltet.
_PFAD_SPERREN = ("MSYS_NO_PATHCONV", "MSYS2_ARG_CONV_EXCL")
#: Ordner, in denen unter Windows nur der WSL-Stub als ``bash.exe`` liegt.
_STUB_ORDNER = frozenset({"system32", "windowsapps"})


def ist_wsl_stub(pfad: str) -> bool:
    """``True``, wenn ``pfad`` der WSL-Stub ist (``System32``/``WindowsApps``), keine echte bash."""
    return bool({t.lower() for t in PureWindowsPath(pfad).parts} & _STUB_ORDNER)


def bash() -> str:
    """Pfad der bash, die der Skill startet.

    Windows: Git-Bash aus ``CLAUDE_CODE_GIT_BASH_PATH``, ``GIT_BASH``, dem Standardpfad, neben
    ``git`` oder ``bash`` im ``PATH`` (nie der WSL-Stub). Sonst ``bash`` im ``PATH``.
    Fehlt sie → ``FileNotFoundError`` mit verständlicher Meldung, nie stiller Rückfall.
    """
    if sys.platform != "win32":
        pfad = shutil.which("bash")
        if pfad is None:
            raise FileNotFoundError("bash fehlt im PATH (Paket bash installieren)")
        return pfad
    kandidaten = [
        os.environ.get("CLAUDE_CODE_GIT_BASH_PATH") or "",
        os.environ.get("GIT_BASH") or "",
        _GIT_BASH_STANDARD,
    ]
    git = shutil.which("git")
    if git:
        for wurzel in Path(git).resolve().parents[:3]:  # git liegt in <Git>/cmd, bin, mingw64/bin
            kandidaten += [str(wurzel / "bin" / "bash.exe"), str(wurzel / "usr" / "bin" / "bash.exe")]
    kandidaten.append(shutil.which("bash") or "")
    for kandidat in kandidaten:
        if kandidat and not ist_wsl_stub(kandidat) and Path(kandidat).is_file():
            return kandidat
    log.error("Git-Bash nicht gefunden, geprüft: %s", [k for k in kandidaten if k])
    raise FileNotFoundError(
        "Git-Bash nicht gefunden (Git für Windows installieren oder GIT_BASH auf bash.exe setzen)"
        " — die WSL-bash aus System32 taugt nicht"
    )


def lauf(
    argumente: Sequence[str], *, env: Mapping[str, str] | None = None, **optionen: Any
) -> subprocess.CompletedProcess[Any]:
    """``bash <argumente>`` über ``subprocess.run`` starten, Umgebung für Git-Bash bereinigt.

    ``env`` fehlt → ``os.environ``. Unter Windows fallen ``MSYS_NO_PATHCONV``/
    ``MSYS2_ARG_CONV_EXCL`` heraus, sonst geht die Umgebung unverändert durch. ``optionen``
    gehen an ``subprocess.run`` (``check`` Vorgabe ``False``). Fehlt bash →
    ``FileNotFoundError`` aus ``bash()``.
    """
    basis = os.environ if env is None else env
    if sys.platform == "win32":
        umgebung = {k: v for k, v in basis.items() if k.upper() not in _PFAD_SPERREN}
    else:
        umgebung = dict(basis)
    optionen.setdefault("check", False)
    return subprocess.run([bash(), *argumente], env=umgebung, **optionen)  # noqa: S603
