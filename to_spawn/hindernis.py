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

log = logging.getLogger("to_spawn.hindernis")


def _pfad() -> Path:
    roh = os.environ.get("TO_SPAWN_HINDERNIS_LOG")
    return (
        Path(roh).expanduser()
        if roh
        else Path.home() / ".claude" / "to-spawn" / "hindernisse.jsonl"
    )


def _wer() -> str:
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"
    except (OSError, KeyError):
        return "unbekannt"


def melden(art: str, text: str, **felder: Any) -> bool:
    """Hängt eine Zeile ``{zeit, art, text, wer, **felder}`` an; False = nicht schreibbar."""
    zeile = {
        "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
        "art": art,
        "text": text,
        "wer": _wer(),
        **felder,
    }
    try:
        datei = _pfad()
        datei.parent.mkdir(parents=True, exist_ok=True)
        with datei.open("a", encoding="utf-8") as f:
            f.write(json.dumps(zeile, ensure_ascii=False, default=str) + "\n")
    except OSError as fehler:
        log.warning("Hindernis-Log nicht schreibbar: %s", fehler)
        return False
    return True


def lesen(datei: Path | None = None) -> list[dict[str, Any]]:
    """Alle lesbaren Zeilen (älteste zuerst); fehlende Datei oder kaputte Zeile → übersprungen."""
    try:
        zeilen = (datei or _pfad()).read_text(encoding="utf-8").splitlines()
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
