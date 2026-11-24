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
import signal
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
#: Sekunden, die :func:`lauf` nach dem Gruppen-Abbruch noch auf die Pipes wartet (#455).
NACHLAUF_S = 5.0
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
    argumente: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    zeitgrenze_s: float | None = None,
    **optionen: Any,
) -> subprocess.CompletedProcess[Any]:
    """``bash <argumente>`` über ``subprocess.run`` starten, Umgebung für Git-Bash bereinigt.

    ``env`` fehlt → ``os.environ``. Unter Windows fallen ``MSYS_NO_PATHCONV``/
    ``MSYS2_ARG_CONV_EXCL`` heraus, sonst geht die Umgebung unverändert durch. ``optionen``
    gehen an ``subprocess.run`` (``check`` Vorgabe ``False``). Fehlt bash →
    ``FileNotFoundError`` aus ``bash()``.

    ``zeitgrenze_s`` (#455): bash läuft in eigener Prozessgruppe; nach Ablauf endet die ganze
    Gruppe samt Unterprozessen (die sonst die Ausgabe offen halten) und es kommt
    ``subprocess.TimeoutExpired`` mit der Ausgabe bis dahin. ``subprocess.run(timeout=…)``
    beendete nur bash selbst und wartete dann auf die Enkel.
    """
    basis = os.environ if env is None else env
    if sys.platform == "win32":
        umgebung = {k: v for k, v in basis.items() if k.upper() not in _PFAD_SPERREN}
    else:
        umgebung = dict(basis)
    optionen.setdefault("check", False)
    if zeitgrenze_s is None:
        return subprocess.run([bash(), *argumente], env=umgebung, **optionen)  # noqa: S603
    pruefen, eingabe = optionen.pop("check"), optionen.pop("input", None)
    if sys.platform == "win32":
        optionen["creationflags"] = (
            optionen.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        optionen["start_new_session"] = True
    with subprocess.Popen([bash(), *argumente], env=umgebung, **optionen) as prozess:
        try:
            aus, fehler = prozess.communicate(eingabe, timeout=zeitgrenze_s)
        except subprocess.TimeoutExpired:
            _gruppe_beenden(prozess)
            try:
                aus, fehler = prozess.communicate(timeout=NACHLAUF_S)
            except subprocess.TimeoutExpired as rest:
                # Enkel mit eigenem setsid hält die Pipe: Teilausgabe nehmen, Pipes zu, weiter.
                log.warning(
                    "bash PID %d: Pipe nach Zeitgrenze noch offen (Enkel außerhalb der Gruppe)",
                    prozess.pid,
                )
                aus, fehler = _text(rest.output, optionen), _text(rest.stderr, optionen)
                for rohr in (prozess.stdout, prozess.stderr):
                    if rohr is not None:
                        rohr.close()
            raise subprocess.TimeoutExpired(
                prozess.args, zeitgrenze_s, output=aus, stderr=fehler
            ) from None
    if pruefen and prozess.returncode:
        raise subprocess.CalledProcessError(
            prozess.returncode, prozess.args, aus, fehler
        )
    return subprocess.CompletedProcess(prozess.args, prozess.returncode, aus, fehler)


def _text(roh: Any, optionen: Mapping[str, Any]) -> Any:
    """Teilausgabe aus ``TimeoutExpired`` (immer Bytes) im Modus des Aufrufers (``text=True`` → str)."""
    if isinstance(roh, bytes) and (
        optionen.get("text") or optionen.get("universal_newlines") or optionen.get("encoding")
    ):
        return roh.decode(optionen.get("encoding") or "utf-8", errors="replace")
    return roh


def _gruppe_beenden(prozess: subprocess.Popen[Any]) -> None:
    """bash aus :func:`lauf` samt allen Unterprozessen hart beenden.

    Linux/macOS: ``SIGKILL`` an die eigene Prozessgruppe. Windows: ganzer Prozessbaum über
    :mod:`to_spawn.prozessbaum` (dort ist jedes Signal ``TerminateProcess``).
    ponytail: ein Enkel, der selbst ``setsid`` ruft, verlässt die Gruppe und lebt weiter;
    :func:`lauf` wartet dann nur :data:`NACHLAUF_S` auf die Pipe. Upgrade-Pfad: Ausgabe in Datei.
    """
    if sys.platform == "win32":
        from to_spawn import prozessbaum  # spät: nur Windows braucht die Prozessliste

        try:
            prozessbaum.senden(prozessbaum.baum(prozess.pid), signal.SIGTERM)
        except OSError as fehler:
            log.warning("Prozessbaum von PID %d nicht beendet: %s", prozess.pid, fehler)
        prozess.kill()
        return
    try:
        os.killpg(prozess.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as fehler:
        log.warning(
            "Prozessgruppe %d nicht beendet: %s — nur bash selbst", prozess.pid, fehler
        )
        prozess.kill()
