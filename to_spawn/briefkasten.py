"""Entscheidungs-Briefkasten (Tür B, #278; Lastenheft L8 A45–A48).

Einziges Modul, das Format und Ablage der Klasse-3-Fragen kennt. Ort aus
:func:`befugnis.briefkasten_datei` (Hauptbaum, gitignored ``.to-spawn/``). Aufrufer fragen nur:

- :func:`einwerfen` — die Session wirft eine Frage mit eigenem Vorschlag ein.
- :func:`lesen` — :class:`Antwort`, :data:`WARTET` oder :data:`NUTZER` (Klasse 2: nie freigegeben,
  auch nicht durch eine von Hand eingetragene Antwort).
- :func:`antworten` — der Aufseher antwortet; verweigert bei Klasse 2.
- :func:`offene` — Tickets mit unbeantworteter Frage.
- :func:`liste` — die eine Liste für die Gesamtabnahme: Klasse-1-Einträge aus dem Bau-Log plus alle
  Briefkasten-Antworten (wer, was, warum, Beleg); keine eigene Datei.

Eine Datei je Ticket hält alle Fragen in Reihenfolge; es gilt die jüngste. Eine halbe oder kaputte
Datei zählt als :data:`WARTET` (sichere Richtung).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from to_spawn import bau_log, befugnis, config, manifest

log = logging.getLogger("to_spawn.briefkasten")

WARTET = "WARTET"
NUTZER = "NUTZER"
_KLASSEN = (2, 3)  # Klasse 1 braucht keine Frage
_NUMMER = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class Antwort:
    """Verbindliche Antwort des Aufsehers auf die jüngste Frage."""

    wahl: str
    grund: str
    belege: list[str]
    von: str
    ts: str


def _hauptbaum(hauptbaum: Path | None) -> Path:
    """Vorgabe: Hauptbaum aus der Umgebung (``bau.py`` setzt ihn), sonst die Git-Wurzel."""
    return hauptbaum or bau_log.log_rueckfall() or config.repo_wurzel()


def _datei(n: str | int, hauptbaum: Path | None) -> Path:
    nummer = str(n).strip()
    if not _NUMMER.fullmatch(nummer):  # Trust-Grenze: Nummer wird Teil des Dateinamens
        raise ValueError(f"Ticket-Nummer {n!r} ist keine Zahl.")
    return _hauptbaum(hauptbaum) / befugnis.briefkasten_datei(nummer)


def _lade(datei: Path) -> dict[str, Any] | None:
    """Inhalt oder ``None`` (fehlt, halb, kaputt, falsche Form)."""
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as fehler:
        log.warning("Briefkasten %s nicht lesbar (%s) — gilt als WARTET.", datei, fehler)
        return None
    if not isinstance(daten, dict) or not isinstance(daten.get("fragen"), list):
        return None
    if not all(isinstance(f, dict) for f in daten["fragen"]):
        return None
    return daten


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def einwerfen(
    n: str | int,
    frage: str,
    vorschlag: str,
    klasse: int,
    belege: Iterable[str],
    *,
    hauptbaum: Path | None = None,
) -> Path:
    """Frage der Session einwerfen; ``klasse`` 3 (Aufseher entscheidet) oder 2 (nur der Nutzer)."""
    if klasse not in _KLASSEN:
        raise ValueError(f"Klasse {klasse!r}: in den Briefkasten gehören nur Klasse 2 und 3.")
    datei = _datei(n, hauptbaum)
    datei.parent.mkdir(parents=True, exist_ok=True)
    daten = _lade(datei)
    if daten is None and datei.exists():
        # Nie überschreiben, was nicht lesbar ist (Datenverlust): beiseitelegen.
        datei.replace(datei.with_name(datei.name + ".kaputt"))
        log.warning("Kaputter Briefkasten %s nach .kaputt verschoben.", datei)
    daten = daten or {"ticket": str(n).strip(), "fragen": []}
    daten["fragen"].append(
        {
            "ts": _jetzt(),
            "frage": frage,
            "vorschlag": vorschlag,
            "klasse": klasse,
            "belege": list(belege),
            "antwort": None,
        }
    )
    # ponytail: read-modify-write ohne Sperre; Session und Aufseher schreiben nie gleichzeitig
    # (Einwurf vor dem Warten, Antwort danach). Sperre nachrüsten, wenn mehrere Schreiber kommen.
    manifest.schreibe_atomar(datei, daten)
    return datei


def lesen(n: str | int, *, hauptbaum: Path | None = None) -> Antwort | str:
    """Stand der jüngsten Frage: :class:`Antwort`, :data:`WARTET` oder :data:`NUTZER`."""
    daten = _lade(_datei(n, hauptbaum))
    if not daten or not daten["fragen"]:
        return WARTET
    letzte = daten["fragen"][-1]
    if letzte.get("klasse") == 2:
        return NUTZER  # auch eine eingetragene Antwort gibt Klasse 2 nicht frei
    antwort = letzte.get("antwort")
    if letzte.get("klasse") != 3 or not isinstance(antwort, dict) or not antwort.get("wahl"):
        return WARTET
    return Antwort(
        wahl=str(antwort["wahl"]),
        grund=str(antwort.get("grund", "")),
        belege=[str(b) for b in antwort.get("belege") or []],
        von=str(antwort.get("von", "")),
        ts=str(antwort.get("ts", "")),
    )


def antworten(
    n: str | int,
    wahl: str,
    belege: Iterable[str],
    grund: str,
    von: str = "aufseher",
    *,
    hauptbaum: Path | None = None,
) -> Path:
    """Antwort auf die offene Frage schreiben.

    ``ValueError`` ohne offene Frage, ``PermissionError`` bei Klasse 2 (geht an den Nutzer).
    """
    datei = _datei(n, hauptbaum)
    daten = _lade(datei)
    letzte = daten["fragen"][-1] if daten and daten["fragen"] else None
    if letzte is None or letzte.get("antwort"):
        raise ValueError(f"Ticket {n}: keine offene Frage im Briefkasten.")
    if letzte.get("klasse") != 3:
        raise PermissionError(f"Ticket {n}: Klasse 2 entscheidet nur der Nutzer.")
    letzte["antwort"] = {
        "ts": _jetzt(),
        "wahl": wahl,
        "grund": grund,
        "belege": list(belege),
        "von": von,
    }
    manifest.schreibe_atomar(datei, daten)
    return datei


def offene(tickets: Iterable[str | int] | None = None, *, hauptbaum: Path | None = None) -> list[str]:
    """Tickets, deren jüngste Frage noch unbeantwortet ist (``tickets`` None = alle)."""
    ordner = _hauptbaum(hauptbaum) / Path(befugnis.briefkasten_datei(0)).parent
    if tickets is None:
        tickets = sorted(
            (d.stem.removeprefix("entscheidung-") for d in ordner.glob("entscheidung-*.json")),
            key=lambda t: int(t) if t.isdigit() else -1,
        )
    ergebnis = []
    for t in tickets:
        try:
            daten = _lade(_datei(t, hauptbaum))
        except ValueError:
            continue
        if daten and daten["fragen"] and not daten["fragen"][-1].get("antwort"):
            ergebnis.append(str(t).strip())
    return ergebnis


def _was(frage: Any, wahl: Any) -> str:
    return " → ".join(str(t) for t in (frage, wahl) if t)


def liste(
    tickets: Iterable[str | int],
    *,
    log_repo: Path | None = None,
    hauptbaum: Path | None = None,
) -> list[dict[str, str]]:
    """Alle Entscheidungen je Ticket: Bau-Log ``entscheidung`` (Klasse 1) + Briefkasten-Antworten."""
    basis = _hauptbaum(hauptbaum)
    repo = log_repo or basis
    zeilen: list[dict[str, str]] = []
    for t in tickets:
        nummer = str(t).strip()
        for z in bau_log.lese(repo, nummer, hauptbaum=basis):
            if z.get("typ") != "entscheidung":
                continue
            zeilen.append(
                {
                    "ticket": nummer,
                    "ts": str(z.get("ts", "")),
                    "wer": "Bau-Session",
                    "was": _was(z.get("frage"), z.get("wahl")) or str(z.get("entscheidungen") or z.get("text") or ""),
                    "warum": str(z.get("grund") or ""),
                    "beleg": f"Bau-Log #{nummer} {z.get('ts', '')}",
                }
            )
        try:
            daten = _lade(_datei(nummer, basis))
        except ValueError:
            continue
        for f in (daten or {}).get("fragen", []):
            antwort = f.get("antwort")
            if f.get("klasse") != 3 or not isinstance(antwort, dict):
                continue  # Klasse 2 hat keine gültige Antwort
            zeilen.append(
                {
                    "ticket": nummer,
                    "ts": str(antwort.get("ts", "")),
                    "wer": str(antwort.get("von", "")),
                    "was": _was(f.get("frage"), antwort.get("wahl")),
                    "warum": str(antwort.get("grund", "")),
                    "beleg": ", ".join(str(b) for b in antwort.get("belege") or []),
                }
            )
    return zeilen
