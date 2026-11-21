"""Windows-Wissen des Hausmeisters (#1373, Laptop-Ordner ``C:/dev/wt-*``).

Nur :mod:`to_spawn.hausmeister` ruft dieses Modul; die Lösch-Regeln leben allein dort.
Hier steckt, wie man unter Windows erkennt, ob ein Prozess im Ordner arbeitet
(CIM-Prozessliste, Umbenennungs-Probe), wie die Sperre gegen parallele Läufe geht
(``msvcrt.locking``) und wie ``rmtree`` Nur-Lese-Dateien wegbekommt. Was einen Ordner
festhält, meldet :class:`Bleibt`; ``hausmeister`` übersetzt es in seine Regel-Sprache.

Test-Nähte: :data:`_POWERSHELL`, :data:`PS_ZEITLIMIT_S`, :data:`_umbenennen`, :data:`_msvcrt`.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

try:
    import msvcrt as _msvcrt_modul
except ImportError:  # nicht Windows
    _msvcrt_modul = None

log = logging.getLogger("to_spawn.hausmeister.windows")

#: Zeitlimit der PowerShell-Prozessliste in Sekunden.
PS_ZEITLIMIT_S = 60
_POWERSHELL = "powershell"
_umbenennen: Callable[[Path, Path], None] = os.rename
_msvcrt: Any = _msvcrt_modul
# msvcrt.locking meldet einen fremd gehaltenen Bereich je nach CRT mit EACCES oder EDEADLOCK.
_BELEGT = {errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)}


class Bleibt(Exception):
    """Der Ordner bleibt (Grund für Menschen); ``fehler`` = unklarer Zustand statt Regel."""

    def __init__(
        self, grund: str, *, fuer_david: bool = False, fehler: bool = False
    ) -> None:
        super().__init__(grund)
        self.grund = grund
        self.fuer_david = fuer_david
        self.fehler = fehler


def pfad_norm(pfad: str | Path) -> str:
    """Vergleichsform für Windows-Pfade: ``/`` statt Backslash, klein, ohne Schluss-``/``."""
    return str(pfad).replace("\\", "/").rstrip("/").lower()


def kein_prozess(ordner: Path) -> None:
    """Prozessliste per CIM: :class:`Bleibt`, wenn ``ExecutablePath`` im Ordner liegt oder
    ``CommandLine`` den Ordnerpfad enthält (Groß/Klein egal, ``/`` = Backslash; ``wt-13``
    trifft nicht ``wt-130``). Die cwd steht dort nicht — dafür :func:`umbenennen_probe`
    (nur im echten Lauf, sie fasst den Ordner an)."""
    o = pfad_norm(ordner)
    im_text = re.compile(re.escape(o) + r"(?![^/\s\"'])")
    for pid, befehl, exe in _prozesse():
        if pid == os.getpid():
            continue  # dieser Lauf selbst (z. B. Ordner als CLI-Argument)
        exe_n = pfad_norm(exe) if exe else ""
        if (
            exe_n == o
            or exe_n.startswith(o + "/")
            or (befehl and im_text.search(pfad_norm(befehl)))
        ):
            raise Bleibt(f"Prozess {pid} arbeitet im Ordner oder hat Dateien offen")


def _prozesse() -> list[tuple[int | None, str | None, str | None]]:
    """``(ProcessId, CommandLine, ExecutablePath)`` aller Prozesse; :class:`Bleibt` mit
    Fehler, wenn PowerShell fehlt, hängt oder Unlesbares liefert."""
    befehl = "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine,ExecutablePath | ConvertTo-Json -Compress"
    try:
        lauf = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", befehl],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PS_ZEITLIMIT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as fehler:
        raise Bleibt(
            f"Prozessliste nicht lesbar (PowerShell: {fehler})", fehler=True
        ) from None
    if lauf.returncode != 0:
        raise Bleibt(
            f"Prozessliste nicht lesbar (PowerShell Exit {lauf.returncode})",
            fehler=True,
        )
    kaputt = Bleibt("Prozessliste nicht lesbar (PowerShell-JSON)", fehler=True)
    try:
        daten = json.loads(lauf.stdout)
    except ValueError:
        raise kaputt from None
    if isinstance(daten, dict):
        daten = [daten]  # ConvertTo-Json: ein Prozess ⇒ Objekt statt Liste
    if not isinstance(daten, list) or not all(isinstance(e, dict) for e in daten):
        raise kaputt
    return [
        (
            e["ProcessId"] if isinstance(e.get("ProcessId"), int) else None,
            e["CommandLine"] if isinstance(e.get("CommandLine"), str) else None,
            e["ExecutablePath"] if isinstance(e.get("ExecutablePath"), str) else None,
        )
        for e in daten
    ]


def umbenennen_probe(ordner: Path) -> None:
    """Ordner kurz auf ``<name>.hausmeister-probe`` und zurück. Windows verweigert das
    Umbenennen, solange ein Prozess darin arbeitet oder eine Datei offen hat ⇒ bleibt."""
    probe = ordner.with_name(ordner.name + ".hausmeister-probe")
    if os.path.lexists(probe):
        raise Bleibt(f"Probe-Ziel {probe} existiert schon", fehler=True)
    try:
        _umbenennen(ordner, probe)
    except PermissionError:
        raise Bleibt(
            "Ordner in Benutzung (Windows verweigert das Umbenennen)"
        ) from None
    except OSError as fehler:
        raise Bleibt(f"Umbenennungs-Probe gescheitert: {fehler}", fehler=True) from None
    try:
        _umbenennen(probe, ordner)
    except OSError as fehler:
        log.error(
            "Hausmeister: Rückbenennen gescheitert — Ordner liegt jetzt unter %s "
            "statt %s (%s); bitte von Hand zurückbenennen",
            probe,
            ordner,
            fehler,
        )
        raise Bleibt(
            f"Rückbenennen gescheitert, Ordner liegt unter {probe}",
            fuer_david=True,
            fehler=True,
        ) from None


def sperre_nehmen(datei: Path) -> IO[str] | None | bool:
    """Nicht blockierende Sperre per ``msvcrt.locking`` (1 Byte ab Anfang): offene Datei =
    gehalten, ``False`` = anderer Lauf hält sie, ``None`` = ohne msvcrt kein Sperren."""
    if _msvcrt is None:
        return None
    datei.parent.mkdir(parents=True, exist_ok=True)
    f = datei.open("a", encoding="utf-8")
    try:
        f.seek(0)
        # ponytail: Freigabe erst beim close() durchs Betriebssystem; reicht bei einem Lauf
        # pro Tag. Upgrade: vor close() LK_UNLCK, falls Läufe dicht folgen.
        _msvcrt.locking(f.fileno(), _msvcrt.LK_NBLCK, 1)
    except OSError as fehler:
        f.close()
        if fehler.errno in _BELEGT:
            return False
        raise
    return f


def schreibschutz_weg(funktion: Callable[[str], object], pfad: str, _: object) -> None:
    """rmtree-Handler: Schreibschutz von Elternordner und Eintrag nehmen (Windows:
    Nur-Lese-Dateien in ``.git/objects``), dann erneut; scheitert das, fliegt es."""
    for ziel in (os.path.dirname(pfad), pfad):
        if os.path.lexists(ziel) and not os.path.islink(ziel):
            os.chmod(ziel, os.stat(ziel).st_mode | stat.S_IWRITE)
    funktion(pfad)
