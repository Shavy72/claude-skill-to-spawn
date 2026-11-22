"""Windows-Wissen des Hausmeisters (#1373, Laptop-Ordner ``C:/dev/wt-*``).

Nur :mod:`to_spawn.hausmeister` ruft dieses Modul; die Lösch-Regeln leben allein dort.
Hier steckt, wie man unter Windows erkennt, ob ein Prozess im Ordner arbeitet
(CIM-Prozessliste, Umbenennungs-Probe), wie die Sperre gegen parallele Läufe geht
(:func:`to_spawn.sperre.halte`) und wie ``rmtree`` Nur-Lese-Dateien wegbekommt. Was einen Ordner
festhält, meldet :class:`Bleibt`; ``hausmeister`` übersetzt es in seine Regel-Sprache.

Test-Nähte: :data:`_POWERSHELL`, :data:`PS_ZEITLIMIT_S`, :data:`_umbenennen`, :data:`_msvcrt`
(gesetzt = erzwingt den msvcrt-Zweig von :mod:`to_spawn.sperre` mit diesem Modul).
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from to_spawn import prozessbaum
from to_spawn import sperre as _sperre

log = logging.getLogger("to_spawn.hausmeister.windows")

#: Zeitlimit der PowerShell-Prozessliste in Sekunden.
PS_ZEITLIMIT_S = 60
_POWERSHELL = "powershell"
_umbenennen: Callable[[Path, Path], None] = os.rename
_msvcrt: Any = None


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
    """Text-Vergleichsform für Windows-Pfade: ``/`` statt Backslash, klein, ohne
    ``\\\\?\\``-Vorsatz und Schluss-``/``. Löst nichts auf (siehe :func:`pfad_echt`)."""
    text = str(pfad).replace("\\", "/")
    if text.startswith("//?/"):
        text = text[4:]
        if text[:4].lower() == "unc/":
            text = "//" + text[4:]
    return text.rstrip("/").lower()


def pfad_echt(pfad: str | Path) -> tuple[str, bool]:
    """Kanonische Vergleichsform: ``realpath`` (lange statt 8.3-Namen, Junctions/Symlinks
    aufgelöst), dann :func:`pfad_norm`. Zweiter Wert ``False`` = nicht ganz auflösbar
    (Pfad weg, Fehler); dann steht hier die Teil-Auflösung (existierender Anfang aufgelöst)
    bzw. die Text-Form — der Aufrufer darf daraus nie „frei“ folgern, wenn sie trifft."""
    try:
        return pfad_norm(os.path.realpath(pfad, strict=True)), True
    except (OSError, ValueError):
        pass
    try:
        return pfad_norm(os.path.realpath(pfad)), False
    except (OSError, ValueError):
        return pfad_norm(pfad), False


def _liegt_in(pfad: str, ordner: str) -> bool:
    """``pfad`` ist ``ordner`` oder liegt darin (beide :func:`pfad_norm`-Form), Vergleich
    je Pfad-Teil: ``wt-12`` trifft nicht ``wt-123``."""
    teile = ordner.split("/")
    return pfad.split("/")[: len(teile)] == teile


def kein_prozess(ordner: Path) -> None:
    """Prozessliste per CIM: :class:`Bleibt`, wenn ``ExecutablePath`` im Ordner liegt oder
    ``CommandLine`` den Ordnerpfad enthält (Groß/Klein egal, ``/`` = Backslash; ``wt-13``
    trifft nicht ``wt-130``). Die cwd steht dort nicht — sie kommt aus dem PEB jedes
    Prozesses (:func:`_kein_arbeitsordner_drin`, auch trocken; fängt Git-Bash/MSYS, die die
    Umbenennungs-Probe nicht sperren). Offene Dateien zeigt nur :func:`umbenennen_probe`
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
    if sys.platform == "win32":  # PEB gibt es nur auf echtem Windows (Tests simulieren win32)
        _kein_arbeitsordner_drin(ordner)


def _kein_arbeitsordner_drin(ordner: Path) -> None:
    """:class:`Bleibt`, wenn die cwd eines Prozesses ``ordner`` ist oder darin liegt.

    Beide Seiten gehen durch :func:`pfad_echt`: der Prozess setzt seine cwd in beliebiger
    Form (8.3-Kurzpfad, über Junction/Symlink, ``\\\\?\\``), der Ordner kommt kanonisch.
    Im Zweifel bleibt der Ordner: cwd eines laufenden, öffnbaren Prozesses unlesbar ⇒ bleibt
    mit Fehler; cwd lesbar, aber nicht ganz auflösbar ⇒ bleibt mit Fehler, sobald ihre
    Text-Form oder ihre Teil-Auflösung auf den Ordner (kanonisch oder wie übergeben) zeigt.
    Nicht auflösbare cwds woanders halten nichts, sonst blockierte jeder Systemprozess mit
    verschwundener cwd jeden Lauf. Prozesse, die sich nicht öffnen lassen (fremd, erhöht),
    fehlen schon in :func:`to_spawn.prozessbaum.arbeitsordner_windows`."""
    try:
        cwds = prozessbaum.arbeitsordner_windows()
    except OSError as fehler:
        raise Bleibt(f"Prozessliste nicht lesbar ({fehler})", fehler=True) from None
    o, _ = pfad_echt(ordner)
    ziele = {o, pfad_norm(ordner)}
    echt: dict[str, tuple[str, bool]] = {}  # viele Prozesse teilen dieselbe cwd
    for pid, cwd in cwds.items():
        if pid == os.getpid():
            continue  # dieser Lauf selbst
        if cwd is None:
            raise Bleibt(f"Prozess {pid}: Arbeitsordner nicht lesbar", fehler=True)
        if cwd not in echt:
            echt[cwd] = pfad_echt(cwd)
        c, ganz = echt[cwd]
        if ganz:
            if _liegt_in(c, o):
                raise Bleibt(f"Prozess {pid} arbeitet im Ordner oder hat Dateien offen")
        elif any(_liegt_in(k, z) for k in (c, pfad_norm(cwd)) for z in ziele):
            raise Bleibt(
                f"Prozess {pid}: Arbeitsordner nicht auflösbar, zeigt evtl. in den Ordner",
                fehler=True,
            )


def _prozesse() -> list[tuple[int | None, str | None, str | None]]:
    """``(ProcessId, CommandLine, ExecutablePath)`` aller Prozesse; :class:`Bleibt` mit
    Fehler, wenn PowerShell fehlt, hängt oder Unlesbares liefert."""
    # Ausgabe fest auf UTF-8 (#1496): Windows PowerShell 5.1 schreibt sonst in der
    # Konsolen-Codepage und macht aus „…“ in einer CommandLine nacktes " — kaputtes JSON.
    befehl = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine,ExecutablePath | ConvertTo-Json -Compress"
    )
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


def sperre_nehmen(datei: Path) -> ExitStack | bool:
    """Nicht blockierende Sperre über :func:`to_spawn.sperre.halte`: Stapel = gehalten
    (``close()`` gibt frei), ``False`` = anderer Lauf hält sie."""
    stapel = ExitStack()
    if not stapel.enter_context(_sperre.halte(datei, msvcrt=_msvcrt)):
        stapel.close()
        return False
    return stapel


def schreibschutz_weg(funktion: Callable[[str], object], pfad: str, _: object) -> None:
    """rmtree-Handler: Schreibschutz von Elternordner und Eintrag nehmen (Windows:
    Nur-Lese-Dateien in ``.git/objects``), dann erneut; scheitert das, fliegt es."""
    for ziel in (os.path.dirname(pfad), pfad):
        if os.path.lexists(ziel) and not os.path.islink(ziel):
            os.chmod(ziel, os.stat(ziel).st_mode | stat.S_IWRITE)
    funktion(pfad)
