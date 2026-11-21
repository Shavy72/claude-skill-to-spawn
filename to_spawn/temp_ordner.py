"""Temp-Ordner des Bau-Systems (#136): wo Temp-Dateien liegen.

Warum: der System-Temp ist auf dem Bau-Server eine RAM-Platte (tmpfs). Testläufe und alte
Ticket-Kopien füllten sie am 21.11.2026 bis „No space left on device“. Darum bekommen
Bau-Sessions, Aufseher und die Prüf-Tür ``TMPDIR``/``TEMP``/``TMP`` aus genau einer Stelle
(:func:`umgebung`); pytest legt seinen ``--basetemp`` von selbst unter ``TMPDIR`` an.
Aufrufer kennen keinen Pfad.

Schnittstelle:

- :func:`pfad` — der Temp-Ordner auf der Platte (Test-Tür ``TO_SPAWN_TEMP_ORDNER``, sonst
  Maschinen-Liste ``temp_ordner``, Rückfall ``config.MASCHINE_DEFAULTS``), wird angelegt.
  Vorgabe ``~/tmp-bau`` gilt unter Linux und Windows (Home liegt auf der Platte). Nie ``/``,
  das Home oder ein Ordner darüber, auch nicht über einen Symlink (sonst Vorgabe mit Warnung).
  Länger als :data:`MAX_LAENGE` Zeichen gibt eine Warnung: tmux-/Unix-Sockets unter
  pytest-``tmp_path`` brauchen < 108 Zeichen.
- :func:`umgebung` — die drei Variablen für ``os.environ``/``subprocess``; ``{}``, wenn
  der Ordner nicht anlegbar ist (dann bleibt der System-Temp, nie ein Start-Abbruch).

Räumen des Ordners gehört nicht hierher (#177).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from . import config

log = logging.getLogger("to_spawn.temp_ordner")

#: Längster Temp-Ordner ohne Warnung (Vorgabe auf dem Bau-Server: 17 Zeichen).
MAX_LAENGE = 24


def _gefaehrlich(ordner: Path) -> bool:
    """``/``, das Home oder ein Ordner darüber (aufgelöst): kein Temp-Ordner für fremde Dateien."""
    echt = Path(os.path.realpath(ordner))
    heim = Path(os.path.realpath(Path("~").expanduser()))
    return echt == Path(echt.anchor) or echt == heim or echt in heim.parents


def pfad() -> Path:
    """Temp-Ordner auf der Platte (angelegt); ``OSError`` geht an den Aufrufer."""
    vorgabe = str(config.MASCHINE_DEFAULTS["temp_ordner"])
    umg = os.environ.get("TO_SPAWN_TEMP_ORDNER", "").strip()  # Test-Tür
    roh = umg or str(config.maschine().get("temp_ordner") or "").strip() or vorgabe
    ordner = Path(os.path.abspath(Path(roh).expanduser()))
    if _gefaehrlich(ordner):
        log.warning("Temp-Ordner %s abgelehnt (Wurzel oder Home) — Vorgabe gilt", ordner)
        ordner = Path(os.path.abspath(Path(vorgabe).expanduser()))
    elif not umg and len(str(ordner)) > MAX_LAENGE:
        log.warning(
            "Temp-Ordner %s ist länger als %s Zeichen — Unix-Sockets darunter scheitern",
            ordner,
            MAX_LAENGE,
        )
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner


def umgebung() -> dict[str, str]:
    """``TMPDIR``/``TEMP``/``TMP`` auf :func:`pfad`; ``{}``, wenn nicht anlegbar."""
    try:
        ordner = str(pfad())
    except OSError as fehler:
        log.warning("Temp-Ordner nicht anlegbar (%s) — System-Temp bleibt", fehler)
        return {}
    return {"TMPDIR": ordner, "TEMP": ordner, "TMP": ordner}
