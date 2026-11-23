"""Entscheidungs-Briefkasten (Tür B, #278; Lastenheft L8 A45–A48).

Einziges Modul, das Ort, Dateimuster, Format und Ablage der Fragen kennt (Hauptbaum, gitignored
``.to-spawn/``). Aufrufer fragen nur:

- :func:`einwerfen` — die Session wirft eine Frage mit eigenem Vorschlag ein und bekommt deren
  ``id``. Die Klasse ordnet der Briefkasten selbst ein (:func:`befugnis.ist_klasse_2`); eine
  genannte Klasse macht nur strenger. Nur das eigene Ticket (``TO_SPAWN_TICKET``).
- :func:`lesen` — :class:`Antwort` (nur zur passenden ``id``), :data:`WARTET` oder :data:`NUTZER`.
- :func:`antworten` — der Aufseher antwortet auf die jüngste offene Frage mit passender ``id``;
  verweigert (:class:`Verweigert`) in einer Bau-Session und solange eine Klasse-2-Frage offen ist.
- :func:`offene` — Tickets mit unbeantworteter Frage.
- :func:`liste` — die eine Liste für die Gesamtabnahme: Klasse-1-Einträge aus dem Bau-Log plus alle
  Briefkasten-Antworten (wer, was, warum, Beleg); keine eigene Datei.
- :func:`datei_relativ` — Ort der Datei für Texte (Start-Auftrag), relativ zum Hauptbaum.

Eine Datei je Ticket hält alle Fragen in Reihenfolge. Klasse 2 gibt der Briefkasten nie frei; eine
offene Klasse-2-Frage (ohne Antwort ``von`` :data:`NUTZER_VON`) sperrt das ganze Ticket. Eine halbe
oder kaputte Datei zählt als :data:`WARTET` (sichere Richtung). Jedes Lesen-Ändern-Schreiben läuft
unter :func:`sperre.halte` auf ``entscheidung-<N>.lock``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from to_spawn import bau_log, befugnis, config, manifest, sperre

log = logging.getLogger("to_spawn.briefkasten")

WARTET = "WARTET"
NUTZER = "NUTZER"
#: ``von`` einer Antwort, die eine Klasse-2-Frage schließt (nie über :func:`antworten` schreibbar).
NUTZER_VON = "nutzer"
_KLASSEN = (2, 3)  # Klasse 1 braucht keine Frage
_NUMMER = re.compile(r"[0-9]+")  # nur ASCII-Ziffern (int() nähme auch „٧“)
_ORDNER = ".to-spawn"
_PRAEFIX = "entscheidung-"
#: So lange wartet ein Schreiber auf die Sperre, dann ``TimeoutError``.
SPERRE_WARTEN_S = 10.0


class Verweigert(Exception):
    """Der Briefkasten nimmt diese Handlung nicht an (Klasse 2, Bau-Session, fremdes Ticket)."""


@dataclass(frozen=True)
class Antwort:
    """Verbindliche Antwort des Aufsehers auf eine Frage."""

    wahl: str
    grund: str
    belege: list[str]
    von: str
    ts: str


def datei_relativ(n: str | int) -> str:
    """Ort der Entscheidungs-Datei für Ticket ``n``, relativ zum Hauptbaum (ohne Prüfung, nur Text)."""
    return f"{_ORDNER}/{_PRAEFIX}{n}.json"


def _nummer(n: str | int) -> int:
    """Ticket-Nummer als ``int``; ``ValueError`` bei allem außer ASCII-Ziffern (Trust-Grenze)."""
    nummer = str(n).strip()
    if not _NUMMER.fullmatch(nummer):  # Nummer wird Teil des Dateinamens
        raise ValueError(f"Ticket-Nummer {n!r} ist keine Zahl.")
    return int(nummer)


def _eigenes_ticket() -> int | None:
    """Nummer aus ``TO_SPAWN_TICKET`` (setzt ``bau.py`` = Bau-Session) oder ``None``."""
    wert = os.environ.get("TO_SPAWN_TICKET", "").strip()
    if not wert:
        return None
    try:
        return _nummer(wert)
    except ValueError:
        return -1  # gesetzt, aber unlesbar: gilt als Bau-Session, passt zu keinem Ticket


def _hauptbaum(hauptbaum: Path | None) -> Path:
    """Vorgabe: Hauptbaum aus der Umgebung (``bau.py`` setzt ihn), sonst die Git-Wurzel."""
    return hauptbaum or bau_log.log_rueckfall() or config.repo_wurzel()


def _datei(n: str | int, hauptbaum: Path | None) -> Path:
    return _hauptbaum(hauptbaum) / datei_relativ(_nummer(n))


@contextmanager
def _gesperrt(datei: Path) -> Iterator[None]:
    """Sperre für Lesen-Ändern-Schreiben; ``TimeoutError``, wenn sie belegt bleibt."""
    with sperre.halte(datei.with_suffix(".lock"), warten=SPERRE_WARTEN_S) as gehalten:
        if not gehalten:
            raise TimeoutError(f"Briefkasten {datei} bleibt gesperrt.")
        yield


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


def _ist_klasse_2(f: dict[str, Any]) -> bool:
    """Klasse 2, wenn gespeichert ODER der Text anschlägt — eine von Hand gesenkte Klasse zählt nicht."""
    return f.get("klasse") != 3 or befugnis.ist_klasse_2(
        f.get("frage"), f.get("vorschlag"), f.get("ziel"), f.get("art")
    )


def _hat_antwort(f: dict[str, Any]) -> bool:
    antwort = f.get("antwort")
    return isinstance(antwort, dict) and bool(str(antwort.get("wahl") or "").strip())


def _klasse_2_offen(fragen: list[dict[str, Any]]) -> bool:
    """Irgendeine Klasse-2-Frage ohne Antwort des Nutzers? Dann ist das Ticket gesperrt."""
    return any(_ist_klasse_2(f) and not (_hat_antwort(f) and f["antwort"].get("von") == NUTZER_VON) for f in fragen)


def einwerfen(
    n: str | int,
    frage: str,
    vorschlag: str,
    klasse: int | None,
    belege: Iterable[str],
    *,
    ziel: str = "",
    art: str = "",
    hauptbaum: Path | None = None,
) -> str:
    """Frage der Session einwerfen; liefert ihre ``id`` (für :func:`lesen`).

    ``klasse`` ``None``/3/2 macht nur strenger: Klasse 2, sobald sie genannt ist oder Frage,
    Vorschlag, ``ziel`` oder ``art`` an :func:`befugnis.ist_klasse_2` anschlagen. ``Verweigert``
    bei fremdem Ticket (≠ ``TO_SPAWN_TICKET``), ``ValueError`` bei Klasse außer 2/3.
    """
    if klasse is not None and klasse not in _KLASSEN:
        raise ValueError(f"Klasse {klasse!r}: in den Briefkasten gehören nur Klasse 2 und 3.")
    nummer = _nummer(n)
    eigen = _eigenes_ticket()
    if eigen is not None and eigen != nummer:
        raise Verweigert(f"Ticket {nummer}: diese Session gehört zu Ticket {eigen}.")
    streng = klasse == 2 or befugnis.ist_klasse_2(frage, vorschlag, ziel, art)
    frage_id = secrets.token_hex(4)
    datei = _datei(nummer, hauptbaum)
    with _gesperrt(datei):
        daten = _lade(datei)
        if daten is None and datei.exists():
            # Nie überschreiben, was nicht lesbar ist (Datenverlust): beiseitelegen.
            stempel = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            datei.replace(datei.with_name(f"{datei.name}.kaputt-{stempel}"))
            log.warning("Kaputter Briefkasten %s nach .kaputt-%s verschoben.", datei, stempel)
        daten = daten or {"ticket": str(nummer), "fragen": []}
        daten["fragen"].append(
            {
                "id": frage_id,
                "ts": _jetzt(),
                "frage": frage,
                "vorschlag": vorschlag,
                "ziel": ziel,
                "art": art,
                "klasse": 2 if streng else 3,
                "belege": list(belege),
                "antwort": None,
            }
        )
        manifest.schreibe_atomar(datei, daten)
    return frage_id


def lesen(n: str | int, frage_id: str | None = None, *, hauptbaum: Path | None = None) -> Antwort | str:
    """Stand der Frage ``frage_id``: :class:`Antwort`, :data:`WARTET` oder :data:`NUTZER`.

    :data:`NUTZER`, solange irgendeine Klasse-2-Frage des Tickets offen ist oder ``frage_id``
    Klasse 2 ist (auch eine eingetragene Antwort gibt Klasse 2 nie frei). Ohne passende ``id``
    nie eine :class:`Antwort`.
    """
    daten = _lade(_datei(n, hauptbaum))
    if not daten or not daten["fragen"]:
        return WARTET
    if _klasse_2_offen(daten["fragen"]):
        return NUTZER
    treffer = [f for f in daten["fragen"] if frage_id and f.get("id") == frage_id]
    if not treffer:
        return WARTET
    f = treffer[-1]
    if _ist_klasse_2(f):
        return NUTZER
    if not _hat_antwort(f):
        return WARTET
    antwort = f["antwort"]
    belege = antwort.get("belege")
    return Antwort(
        wahl=str(antwort["wahl"]),
        grund=str(antwort.get("grund", "")),
        belege=[str(b) for b in belege] if isinstance(belege, list) else [],
        von=str(antwort.get("von", "")),
        ts=str(antwort.get("ts", "")),
    )


def antworten(
    n: str | int,
    frage_id: str,
    wahl: str,
    belege: Iterable[str],
    grund: str,
    von: str = "aufseher",
    *,
    hauptbaum: Path | None = None,
) -> Path:
    """Antwort auf die jüngste offene Frage schreiben, nur wenn sie ``frage_id`` trägt.

    ``ValueError`` bei leerer ``wahl``, ohne offene Frage oder fremder ``id``; ``Verweigert`` in
    einer Bau-Session (``TO_SPAWN_TICKET`` gesetzt) und solange eine Klasse-2-Frage offen ist.
    """
    if _eigenes_ticket() is not None:
        raise Verweigert("Eine Bau-Session beantwortet keine Briefkasten-Fragen (TO_SPAWN_TICKET gesetzt).")
    if not wahl.strip():
        raise ValueError("Leere Wahl: die Antwort braucht eine Entscheidung.")
    datei = _datei(n, hauptbaum)
    with _gesperrt(datei):
        daten = _lade(datei)
        letzte = daten["fragen"][-1] if daten and daten["fragen"] else None
        if letzte is None or letzte.get("antwort"):
            raise ValueError(f"Ticket {n}: keine offene Frage im Briefkasten.")
        if _klasse_2_offen(daten["fragen"]):
            raise Verweigert(f"Ticket {n}: eine Klasse-2-Frage ist offen, das entscheidet nur der Nutzer.")
        if letzte.get("id") != frage_id:
            raise ValueError(f"Ticket {n}: Frage {frage_id!r} ist nicht die jüngste offene Frage.")
        letzte["antwort"] = {
            "ts": _jetzt(),
            "wahl": wahl,
            "grund": grund,
            "belege": list(belege),
            "von": von,
        }
        manifest.schreibe_atomar(datei, daten)
    return datei


def _sortier_nummer(t: str) -> int:
    try:
        return _nummer(t)
    except ValueError:
        return -1


def offene(tickets: Iterable[str | int] | None = None, *, hauptbaum: Path | None = None) -> list[str]:
    """Tickets, deren jüngste Frage noch unbeantwortet ist (``tickets`` None = alle)."""
    ordner = _hauptbaum(hauptbaum) / _ORDNER
    if tickets is None:
        tickets = sorted(
            (d.stem.removeprefix(_PRAEFIX) for d in ordner.glob(f"{_PRAEFIX}*.json")),
            key=_sortier_nummer,
        )
    ergebnis = []
    for t in tickets:
        try:
            daten = _lade(_datei(t, hauptbaum))
        except ValueError:
            continue
        if daten and daten["fragen"] and not daten["fragen"][-1].get("antwort"):
            ergebnis.append(str(_nummer(t)))
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
            if _ist_klasse_2(f) or not _hat_antwort(f):
                continue  # Klasse 2 hat keine gültige Aufseher-Antwort
            antwort = f["antwort"]
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
