"""Klick-Sperre für lokal gespawnte Claude-Sessions (Maus-Müll im Eingabefeld).

Claude Code im Vollbild-Modus (``"tui": "fullscreen"``) schaltet die volle Maus-Meldung
ein (DEC 1000+1002+1003+1006); 1003 meldet jede Mausbewegung. Werden auf Windows viele
Tabs gleichzeitig gespawnt (``bau <N>``, ``wache <S>``, ``leitstand <S>``), trennt die
Konsole unter Last das ESC ab — Bewegungs-Codes wie ``[555;10;1M`` landen als Text im
Eingabefeld.

``CLAUDE_CODE_DISABLE_MOUSE_CLICKS=1`` stellt Claude Code auf Maus-Modus ``scroll``
(nur 1000+1006): keine Bewegungsmeldungen, das Mausrad scrollt weiter, Klicken und
Markieren in Claude sind aus. Text kopieren geht dort per Shift+Ziehen (natives
Markieren des Terminals).

Die Sperre gilt nur für Sessions, die der Skill auf Windows startet — nicht global und
nicht auf dem Bau-Server (Linux/tmux). Achtung: ein ``env``-Eintrag gleichen Namens in
``~/.claude/settings.json`` überschreibt den Prozess-Wert.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import MutableMapping

log = logging.getLogger("to_spawn.terminal_maus")

_VARIABLE = "CLAUDE_CODE_DISABLE_MOUSE_CLICKS"


def maus_ruhig(env: MutableMapping[str, str] = os.environ) -> None:
    """Setzt in ``env`` die Klick-Sperre für die gleich startende Claude-Session.

    Aufrufer: jeder lokale Starter direkt vor dem Start von ``claude`` — ``bau.py`` und
    ``wache.py`` auf ``os.environ`` (die Kinder erben es), ``leitstand.py`` auf sein
    eigenes Umgebungs-Dict. Auf anderen Plattformen als Windows bleibt ``env`` unverändert.
    """
    if sys.platform != "win32":
        return
    env[_VARIABLE] = "1"
    log.debug("Klick-Sperre an (%s=1): keine Maus-Bewegungsmeldungen in der Session.", _VARIABLE)
