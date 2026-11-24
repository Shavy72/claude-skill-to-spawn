"""Konsolen-Ausgabe der Skill-Skripte: immer UTF-8 (Windows-Pipes sind cp1252)."""

from __future__ import annotations

import sys
from typing import TypedDict


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


def utf8_ausgabe() -> None:
    """Stellt stdout/stderr auf UTF-8 (``errors="replace"``), damit Haken/Umlaute nie abstürzen."""
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(encoding="utf-8", errors="replace")
