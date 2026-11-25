"""Konsolen-Ausgabe der Skill-Skripte: immer UTF-8 (Windows-Pipes sind cp1252)."""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any, TypedDict

from to_spawn import prozessbaum


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
    return wert if isinstance(wert, str) else (wert or b"").decode("utf-8")


def lauf_daten(befehl: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[str] | None":
    """``subprocess.run`` mit Daten-Lesart (UTF-8 strict); ``None`` = Ausgabe nicht lesbar.

    ``OSError``/``TimeoutExpired`` bleiben beim Aufrufer. Ein Nicht-UTF-8-Byte (z. B. Windows-Hinweis in
    OEM-Kodierung) ist kein Absturz, sondern „nicht lesbar“: Aufräum- und Prüf-Wege laufen weiter.
    """
    roh = subprocess.run(befehl, capture_output=True, **{**prozessbaum.fensterlos(), **kwargs})
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
