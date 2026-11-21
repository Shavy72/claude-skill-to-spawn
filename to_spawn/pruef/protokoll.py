"""Prüf-Protokoll: eine JSON-Zeile je Lauf, Format und Ort nur hier (Paket-intern, #99)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("to_spawn.pruef")

#: Ort im geprüften Repo; der Ordner bekommt ein eigenes ``.gitignore``, damit nichts mitcommittet wird.
ORDNER = Path(".to-spawn") / "pruef"
DATEI = "protokoll.jsonl"
URTEIL = {0: "grün", 1: "rot", 2: "gate-fehler"}


def zeile(
    *,
    basis: str,
    ziel: str,
    exit_code: int,
    stufen: list[dict[str, Any]],
    werkzeug: list[str],
    fehler_kurz: str,
    gesamt_s: float,
) -> dict[str, Any]:
    """Felder nach PRUEFTOR_VERGLEICH 6.2; Auswahl, Cache und Wackler folgen in 3b-2a/3b-3/3b-4."""
    return {
        "zeit": datetime.now(UTC).isoformat(timespec="seconds"),
        "basis": basis,
        "ziel": ziel,
        "urteil": URTEIL[exit_code],
        "stufen": stufen,
        "auswahl": None,
        "cache_treffer": False,
        "fehler_kurz": fehler_kurz,
        "wackler": [],
        "gesamt_s": round(gesamt_s, 2),
        "werkzeug": werkzeug,
    }


def text(daten: dict[str, Any]) -> str:
    return json.dumps(daten, ensure_ascii=False, separators=(",", ":"))


def schreibe(repo: Path, daten: dict[str, Any]) -> None:
    """Hängt die Zeile an; scheitert das Schreiben, bleibt es bei einer Warnung (stdout trägt sie)."""
    ordner = repo / ORDNER
    try:
        ordner.mkdir(parents=True, exist_ok=True)
        ignorieren = ordner / ".gitignore"
        if not ignorieren.exists():
            ignorieren.write_text("*\n", encoding="utf-8")
        with (ordner / DATEI).open("a", encoding="utf-8", newline="\n") as datei:
            datei.write(text(daten) + "\n")
    except OSError as fehler:
        log.warning("Prüf-Protokoll nicht geschrieben (%s): %s", ordner / DATEI, fehler)
