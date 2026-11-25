"""Hindernis-Log (#1369): eine JSON-Zeile je Hindernis, das einen Start aufhält.

Beispiel: Platte fast voll, der Start-Check lässt keine neue Session zu. Die Zeilen
sind für die spätere Anzeige (``/stand``, Folge-Ticket) gedacht — nie Mail, nie
Benachrichtigung. Eigene Datei statt Repo-Doku (eine im Ticket genannte
``docs/GRILL_…_hindernis-log.md`` gibt es nicht, und Cron darf keine Repo-Doku
committen). Datei: ``TO_SPAWN_HINDERNIS_LOG``, Standard
``~/.claude/to-spawn/hindernisse.jsonl``. Beide Türen werfen nie.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import socket
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("to_spawn.hindernis")


def daten_ordner() -> Path:
    """Einheitlicher Daten- und Log-Ordner des Bau-Systems: ``~/.claude/to-spawn``."""
    return config.heim_ordner() / ".claude" / "to-spawn"


def _pfad() -> Path:
    roh = os.environ.get("TO_SPAWN_HINDERNIS_LOG")
    return config.expand_heim(roh) if roh else daten_ordner() / "hindernisse.jsonl"


def wer() -> str:
    """``nutzer@rechner`` für Log-Einträge; bei Systemfehler ``unbekannt``."""
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"
    except (OSError, KeyError):
        return "unbekannt"


_wer = wer


def zeile_anhaengen(datei: Path, eintrag: dict[str, Any]) -> bool:
    """Hängt eine JSONL-Zeile (UTF-8, ``\\n``) an ``datei`` an; ``False`` = nicht schreibbar."""
    try:
        datei.parent.mkdir(parents=True, exist_ok=True)
        with datei.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False, default=str) + "\n")
    except OSError as fehler:
        log.warning("Log nicht schreibbar (%s): %s", datei, fehler)
        return False
    return True


def zeilen_lesen(datei: Path) -> list[dict[str, Any]]:
    """Alle lesbaren JSON-Objekt-Zeilen aus ``datei`` (älteste zuerst); fehlend/kaputt → übersprungen."""
    try:
        zeilen = datei.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    ergebnis: list[dict[str, Any]] = []
    for zeile in zeilen:
        try:
            daten = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(daten, dict):
            ergebnis.append(daten)
    return ergebnis


def melden(art: str, text: str, **felder: Any) -> bool:
    """Hängt eine Zeile ``{zeit, art, text, wer, **felder}`` an; False = nicht schreibbar."""
    zeile = {
        "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
        "art": art,
        "text": text,
        "wer": wer(),
        **felder,
    }
    try:
        datei = _pfad()
    except config.KeinHeimOrdner as fehler:
        log.warning("Hindernis-Log ohne Heim-Ordner nicht schreibbar: %s", fehler)
        return False
    return zeile_anhaengen(datei, zeile)


def lesen(datei: Path | None = None) -> list[dict[str, Any]]:
    """Alle lesbaren Zeilen (älteste zuerst); fehlende Datei oder kaputte Zeile → übersprungen."""
    try:
        ziel = datei or _pfad()
    except config.KeinHeimOrdner:
        return []
    return zeilen_lesen(ziel)
