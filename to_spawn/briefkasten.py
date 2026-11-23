"""Entscheidungs-Briefkasten (Tür B, #278; Lastenheft L8 A45–A48).

Einziges Modul, das Ort, Dateimuster, Format und Ablage der Fragen kennt (Hauptbaum, gitignored
``.to-spawn/``). Aufrufer fragen nur:

- :func:`einwerfen` — die Session wirft eine Frage mit eigenem Vorschlag ein und bekommt deren
  ``id``. Die Klasse ordnet der Briefkasten selbst ein (:func:`befugnis.ist_klasse_2`); eine
  genannte Klasse macht nur strenger. Nur das eigene Ticket (``TO_SPAWN_TICKET``).
- :func:`lesen` — :class:`Antwort` (nur zur passenden ``id``, auch die des Nutzers),
  :data:`WARTET` oder :data:`NUTZER`.
- :func:`antworten` — der Aufseher antwortet auf eine offene Frage über ihre ``id``; verweigert
  (:class:`Verweigert`) in einer Bau-Session und solange das Ticket auf den Nutzer wartet.
- :func:`nutzer_antworten` — Antwort des Nutzers (auch Klasse 2); verweigert in einer Bau-Session.
- :func:`offene` — alle offenen Fragen (je Ticket, ``id``, Klasse) samt Sperre durch kaputte Datei.
- :func:`liste` — die eine Liste für die Gesamtabnahme: Klasse-1-Einträge aus dem Bau-Log plus alle
  Briefkasten-Antworten (wer, was, warum, Beleg); keine eigene Datei.
- :func:`datei_relativ` — Ort der Datei für Texte (Start-Auftrag), relativ zum Hauptbaum.

Eine Datei je Ticket hält alle Fragen in Reihenfolge. Klasse 2 gibt nur der Nutzer frei; eine
offene Klasse-2-Frage (ohne Antwort ``von`` :data:`NUTZER_VON`) sperrt das ganze Ticket. Eine halbe
Datei zählt als :data:`WARTET` (sichere Richtung). Eine unlesbare Datei legt :func:`einwerfen` als
``entscheidung-<N>.json.kaputt-<stempel>`` beiseite; solange eine solche liegt, wartet das Ticket
auf den Nutzer (sie könnte eine offene Klasse-2-Frage enthalten). Geklärt ist es, wenn der Nutzer
sie geprüft und gelöscht hat. Jedes Lesen-Ändern-Schreiben läuft unter :func:`sperre.halte` auf
``entscheidung-<N>.lock``.

Schutz des Nutzer-Wegs, ehrlich: Bau-Session (``TO_SPAWN_TICKET`` gesetzt, auch leer) und
Aufseher (``--von`` kennt nur :data:`AUFSEHER_VON`) haben über diese Schnittstelle keinen Weg,
``von`` :data:`NUTZER_VON` zu schreiben; die CLI ``nutzer-antwort`` verlangt zusätzlich ein
echtes Terminal. Das verhindert versehentliche und über die angebotene Schnittstelle mögliche
Selbst-Freigabe — nicht eine Session, die mit Absicht die Datei selbst schreibt oder die
Umgebung umbaut (``env -u``); auf derselben Maschine kann sie das technisch immer.
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
#: Einziges ``von`` für :func:`antworten` (feste Auswahl, CLI ``--von``).
AUFSEHER_VON = ("aufseher",)
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
    """Nummer aus ``TO_SPAWN_TICKET`` (setzt ``bau.py`` = Bau-Session) oder ``None`` (fehlt ganz).

    Gesetzt, aber leer oder unlesbar (``TO_SPAWN_TICKET=``) gilt als Bau-Session und passt zu
    keinem Ticket (-1). Der Aufseher-Takt entfernt die Variable ganz (``waechter_takt._kind_env``).
    """
    wert = os.environ.get("TO_SPAWN_TICKET")
    if wert is None:
        return None
    try:
        return _nummer(wert)
    except ValueError:
        return -1


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


def _gilt(f: dict[str, Any]) -> bool:
    """Hat ``f`` eine gültige Antwort? Klasse 2 nur ``von`` :data:`NUTZER_VON`."""
    return _hat_antwort(f) and (f["antwort"].get("von") == NUTZER_VON or not _ist_klasse_2(f))


def _klasse_2_offen(fragen: list[dict[str, Any]]) -> bool:
    """Irgendeine Klasse-2-Frage ohne Antwort des Nutzers? Dann ist das Ticket gesperrt."""
    return any(_ist_klasse_2(f) and not _gilt(f) for f in fragen)


def _kaputte(datei: Path) -> list[Path]:
    """Beiseitegelegte unlesbare Fassungen (``.kaputt-*``) — jede sperrt das Ticket."""
    return sorted(datei.parent.glob(f"{datei.name}.kaputt-*"))


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

    :data:`NUTZER`, solange eine ``.kaputt-*``-Datei liegt oder irgendeine Klasse-2-Frage des
    Tickets ohne Antwort des Nutzers ist (eine Antwort anderer gibt Klasse 2 nie frei). Die
    Antwort des Nutzers kommt als :class:`Antwort` (``von`` :data:`NUTZER_VON`). Ohne passende
    ``id`` nie eine :class:`Antwort`.
    """
    datei = _datei(n, hauptbaum)
    if _kaputte(datei):
        return NUTZER
    daten = _lade(datei)
    if not daten or not daten["fragen"]:
        return WARTET
    if _klasse_2_offen(daten["fragen"]):
        return NUTZER
    treffer = [f for f in daten["fragen"] if frage_id and f.get("id") == frage_id]
    if not treffer:
        return WARTET
    f = treffer[-1]
    if not _gilt(f):
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
    von: str = AUFSEHER_VON[0],
    *,
    hauptbaum: Path | None = None,
) -> Path:
    """Antwort des Aufsehers auf die offene Frage ``frage_id`` schreiben.

    ``von`` nur aus :data:`AUFSEHER_VON` (``ValueError`` sonst; der Nutzer antwortet über
    :func:`nutzer_antworten`). ``ValueError`` bei leerer ``wahl`` oder ohne offene Frage mit dieser
    ``id``; ``Verweigert`` in einer Bau-Session und solange das Ticket auf den Nutzer wartet
    (offene Klasse-2-Frage oder ``.kaputt-*``-Datei).
    """
    if von not in AUFSEHER_VON:
        raise ValueError(f"von {von!r}: der Aufseher schreibt nur als {', '.join(AUFSEHER_VON)}.")
    return _schreibe_antwort(n, frage_id, wahl, belege, grund, von, hauptbaum)


def nutzer_antworten(
    n: str | int,
    frage_id: str,
    wahl: str,
    belege: Iterable[str],
    grund: str,
    *,
    hauptbaum: Path | None = None,
) -> Path:
    """Antwort des Nutzers (Mensch) auf die offene Frage ``frage_id``, auch Klasse 2.

    Einziger Weg zu ``von`` :data:`NUTZER_VON`. ``Verweigert`` in einer Bau-Session; eine
    ``.kaputt-*``-Datei sperrt weiter, bis der Nutzer sie gelöscht hat. Grenzen des Schutzes:
    Modul-Docstring.
    """
    return _schreibe_antwort(n, frage_id, wahl, belege, grund, NUTZER_VON, hauptbaum)


def _schreibe_antwort(
    n: str | int,
    frage_id: str,
    wahl: str,
    belege: Iterable[str],
    grund: str,
    von: str,
    hauptbaum: Path | None,
) -> Path:
    if _eigenes_ticket() is not None:
        raise Verweigert("Eine Bau-Session beantwortet keine Briefkasten-Fragen (TO_SPAWN_TICKET gesetzt).")
    if not wahl.strip():
        raise ValueError("Leere Wahl: die Antwort braucht eine Entscheidung.")
    datei = _datei(n, hauptbaum)
    with _gesperrt(datei):
        daten = _lade(datei)
        treffer = [f for f in (daten or {}).get("fragen", []) if f.get("id") == frage_id]
        if not daten or not treffer or treffer[-1].get("antwort"):
            raise ValueError(f"Ticket {n}: keine offene Frage {frage_id!r} im Briefkasten.")
        if von != NUTZER_VON and (_kaputte(datei) or _klasse_2_offen(daten["fragen"])):
            raise Verweigert(f"Ticket {n}: wartet auf den Nutzer (Klasse 2 oder kaputte Datei).")
        treffer[-1]["antwort"] = {
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


def offene(tickets: Iterable[str | int] | None = None, *, hauptbaum: Path | None = None) -> list[dict[str, str]]:
    """Alle offenen Fragen (``tickets`` None = alle), älteste zuerst je Ticket.

    Je Frage ``ticket``, ``id``, ``klasse`` (``"2"`` = nur der Nutzer), ``frage``, ``vorschlag``,
    ``ts``. Jede ``.kaputt-*``-Datei erscheint als Klasse-2-Eintrag mit leerer ``id``.
    """
    ordner = _hauptbaum(hauptbaum) / _ORDNER
    if tickets is None:
        namen = {d.name.split(".json", 1)[0] for d in ordner.glob(f"{_PRAEFIX}*.json*")}
        tickets = sorted((t.removeprefix(_PRAEFIX) for t in namen), key=_sortier_nummer)
    ergebnis: list[dict[str, str]] = []
    for t in tickets:
        try:
            datei = _datei(t, hauptbaum)
        except ValueError:
            continue
        nummer = str(_nummer(t))
        for kaputt in _kaputte(datei):
            ergebnis.append(
                {
                    "ticket": nummer,
                    "id": "",
                    "klasse": "2",
                    "frage": f"Briefkasten-Datei kaputt: {kaputt.name} — prüfen und löschen",
                    "vorschlag": "",
                    "ts": "",
                }
            )
        for f in (_lade(datei) or {}).get("fragen", []):
            if f.get("antwort"):
                continue
            ergebnis.append(
                {
                    "ticket": nummer,
                    "id": str(f.get("id", "")),
                    "klasse": "2" if _ist_klasse_2(f) else "3",
                    "frage": str(f.get("frage", "")),
                    "vorschlag": str(f.get("vorschlag", "")),
                    "ts": str(f.get("ts", "")),
                }
            )
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
            if not _gilt(f):
                continue  # Klasse 2 gilt nur mit Antwort des Nutzers
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
