"""Konsolen-Ausgabe der Skill-Skripte: immer UTF-8 (Windows-Pipes sind cp1252)."""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any, TypedDict


class _TextLesart(TypedDict):
    encoding: str
    errors: str


#: Lesart für Text aus Unterprozessen (git, claude, Skill-CLI): immer UTF-8. Ohne Angabe
#: dekodiert ``subprocess`` mit der Locale — unter Windows cp1252, das macht aus „ü“ „Ã¼“
#: oder bricht im Lese-Thread ab (``stdout`` bleibt ``None``). Einsatz: ``**UNTERPROZESS_TEXT``
#: statt ``text=True`` allein.
UNTERPROZESS_ANZEIGE: _TextLesart = {"encoding": "utf-8", "errors": "replace"}
UNTERPROZESS_TEXT = UNTERPROZESS_ANZEIGE  # bisheriger Name = Anzeige-Variante

#: „Daten“: Ausgabe, die verglichen oder weiterverwendet wird (SHA, Pfade, Zahlen). Ungültige Bytes
#: sollen laut auffallen, nicht still zu U+FFFD werden. Für Dateinamen, die zurück an git/Dateisystem
#: gehen: ``{**UNTERPROZESS_DATEN, "errors": "surrogateescape"}``. Im Zweifel diese Variante.
UNTERPROZESS_DATEN: _TextLesart = {"encoding": "utf-8", "errors": "strict"}


#: Für Dateinamen, die zurück an git/Dateisystem gehen: ungültige Bytes bleiben verlustfrei erhalten.
UNTERPROZESS_PFADE: _TextLesart = {"encoding": "utf-8", "errors": "surrogateescape"}


def _text(wert: bytes | str | None) -> str:
    text = wert if isinstance(wert, str) else (wert or b"").decode("utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def lauf_daten(befehl: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[str] | None":
    """``subprocess.run`` mit Daten-Lesart (UTF-8 strict); ``None`` = Ausgabe nicht lesbar.

    Nur für Lese-/Prüf-Befehle mit gefangener Ausgabe — nie zum Starten von Sessions,
    Terminals oder fremden Programmen (dafür gilt ``to_spawn.starter.starte``; der
    AST-Prüf-Test in ``tests/test_starter_1406.py`` prüft auch Aufrufe von ``lauf_daten``).

    Zeilenenden werden wie bei ``text=True`` auf LF vereinheitlicht (CRLF/CR → LF), damit
    z. B. Docker- oder Windows-Fehlertexte kein ``\\r`` durchschleifen.
    ``OSError``/``TimeoutExpired`` bleiben beim Aufrufer. Ein Nicht-UTF-8-Byte (z. B.
    Windows-Hinweis in OEM-Kodierung) ist kein Absturz, sondern „nicht lesbar“: Aufräum-
    und Prüf-Wege laufen weiter.
    """
    roh = subprocess.run(befehl, capture_output=True, **kwargs)
    try:
        aus = _text(roh.stdout)
        fehler = _text(roh.stderr)
    except UnicodeDecodeError as ursache:
        logging.getLogger(__name__).warning("Ausgabe von %s nicht UTF-8: %s", befehl[:1], ursache)
        return None
    return subprocess.CompletedProcess(roh.args, roh.returncode, aus, fehler)


def utf8_ausgabe() -> None:
    """Stellt stdout/stderr auf UTF-8 (``errors="replace"``), damit Haken/Umlaute nie abstürzen."""
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(encoding="utf-8", errors="replace")


def stdin_ist_terminal() -> bool:
    """Ist stdin ein echtes Terminal? Die eine Prüf-Stelle für „nur ein Mensch im Terminal“ (#553).

    Unter Windows meldet ``isatty()`` auch das Gerät ``NUL`` (``< /dev/null``, ``stdin=DEVNULL``) als
    Terminal; dort zählt darum nur eine echte Konsole (``GetConsoleMode`` gelingt). Im Zweifel ``False``.
    Prüf-Test: ``tests/test_terminal_pruefung_windows_553.py`` (kein ``isatty()`` daneben).
    """
    if sys.stdin is None or not sys.stdin.isatty():
        return False
    if sys.platform != "win32":
        return True
    import ctypes

    try:
        if sys.stdin.fileno() != 0:
            return False
    except (OSError, ValueError):
        return False
    # Griff über die Windows-Schnittstelle statt über das Sperren-Modul der Laufzeit:
    # dessen Import ist außerhalb von ``to_spawn.sperre`` verboten (tests/test_sperre_e2.py).
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    griff = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
    if griff in (None, 0, ctypes.c_void_p(-1).value):
        return False
    modus = ctypes.c_uint32()
    return bool(kernel32.GetConsoleMode(ctypes.c_void_p(griff), ctypes.byref(modus)))
