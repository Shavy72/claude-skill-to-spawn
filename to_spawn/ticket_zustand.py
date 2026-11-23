"""Ticket-Zustand: die eine Stelle, die weiß, wann ein Ticket fertig ist (#279).

Für Entscheidungen gibt es genau zwei Fragen — jede Stelle wählt die passende:

- :func:`fertig_gebaut` — „Muss hier noch jemand bauen?“ Nein, sobald das Issue zu
  ist ODER grün auf Staging liegt (Label der Deploy-Schlange). Live kommt gesammelt
  am Spec-Ende, darauf wartet kein Bau.
- :func:`endgueltig_zu` — „Ist das Ticket wirklich abgeschlossen?“ Nur ein geschlossenes
  Issue. Für alles, was zum echten Schluss gehört (Aufseher, Spec-Schluss, Aufräumen).

Für Anzeigen liefert :func:`stufe` einen der Werte aus :data:`STUFEN`; Aufrufer
vergleichen nur auf Gleichheit, nie auf Reihenfolge.

Ein Ticket ist ein ``dict`` in jeder Form, die bei uns vorkommt: REST (``state: open``,
``state_reason``, Labels als Objekte), ``gh``-CLI (``state: OPEN``, ``stateReason``)
oder schon aufbereitet (Labels als Namen, auch als Menge). Welches Label und welcher
GitHub-Zustand was bedeutet, steht nur hier.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

#: Label der Deploy-Schlange (#1527): Ticket liegt grün auf Staging, wartet nur noch auf Live.
STAGING_FERTIG_LABEL = "staging:fertig"

Stufe = Literal["offen", "fertig_gebaut", "zu", "verworfen"]
#: Alle Anzeige-Stufen; ``verworfen`` = geschlossen als „nicht geplant“ oder Duplikat (nie gebaut).
STUFEN: tuple[Stufe, ...] = ("offen", "fertig_gebaut", "zu", "verworfen")

#: GitHub-Schließgründe (klein geschrieben), die „verworfen“ bedeuten.
_VERWORFEN_GRUENDE = frozenset({"not_planned", "duplicate"})

#: Wortlaut je Stufe für Meldungen, z. B. „„bau 12“ geschlossen (Ticket zu).“
_WORTLAUT: dict[Stufe, str] = {
    "offen": "Ticket offen",
    "fertig_gebaut": "Ticket fertig gebaut, wartet auf Live",
    "zu": "Ticket zu",
    "verworfen": "Ticket verworfen",
}

#: Felder, die :func:`lesen` von ``gh issue view`` braucht.
_FELDER = "state,labels,stateReason"


def fertig_gebaut(ticket: Mapping[str, Any]) -> bool:
    """Zu ODER grün auf Staging: hier baut niemand mehr."""
    return stufe(ticket) != "offen"


def endgueltig_zu(ticket: Mapping[str, Any]) -> bool:
    """Issue geschlossen (auch „verworfen“): echter Schluss."""
    return stufe(ticket) in ("zu", "verworfen")


def stufe(ticket: Mapping[str, Any]) -> Stufe:
    """Anzeige-Stufe; fehlende Felder gelten als offen."""
    if str(ticket.get("state", "")).lower() == "closed":
        grund = ticket.get("state_reason") or ticket.get("stateReason") or ""
        return "verworfen" if str(grund).lower() in _VERWORFEN_GRUENDE else "zu"
    return "fertig_gebaut" if _staging_fertig(ticket) else "offen"


def wortlaut(ticket: Mapping[str, Any]) -> str:
    """Kurzer Text zur Stufe für Meldungen — „fertig gebaut“ ist nicht „zu“."""
    return _WORTLAUT[stufe(ticket)]


def wartet_auf_live(ticket: Mapping[str, Any], *, live_freigegeben: bool) -> bool:
    """Grün auf Staging, Live steht bewusst noch aus (#275).

    Für Prüfer, die „Commit liegt live“ verlangen: so ein Ticket ist kein Verstoß.
    Mit Live-Freigabe gilt wieder „muss live sein“.
    """
    return _staging_fertig(ticket) and not live_freigegeben


def lesen(ticket: int | str, cwd: Path | None = None) -> dict[str, Any] | None:
    """Ein Ticket mit allen Feldern holen, die die Fragen brauchen.

    ``None`` bei Fehler oder Zeitüberschreitung — der Aufrufer entscheidet dann
    vorsichtig (nichts starten, nichts wegwerfen).
    """
    from to_spawn import gh  # spät: gh fragt seinerseits diesen Baustein

    daten = gh.json_lauf(["issue", "view", str(ticket), "--json", _FELDER], cwd=cwd, timeout=gh.ticket_stand_timeout())
    return daten if isinstance(daten, dict) and "state" in daten else None


def _staging_fertig(ticket: Mapping[str, Any]) -> bool:
    namen = {str(x.get("name") if isinstance(x, Mapping) else x) for x in ticket.get("labels") or ()}
    return STAGING_FERTIG_LABEL in namen
