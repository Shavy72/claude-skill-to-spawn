"""Welche Prüfung ist jetzt dran? Einzige Stelle dieser Regel (#425).

Für Aufrufer (Bau-Session, Aufseher, Abschluss): übergib nur, *wo* die Spec gerade steht
(:class:`SpecStand`), und frag :func:`welche`. Den Befehl dazu liefert :func:`befehl`.

Regel (Davids Vorgabe, 24.11.2026, Beschlüsse E10–E13):

* Je Ticket immer nur die schnelle Prüfung (Stil, betroffene Tests, Rauchtest; 1–3 min).
* Die Komplett-Prüfung genau einmal je Spec:
  Repo mit Staging-App, sobald alle Tickets auf Staging sind;
  Repo ohne Staging-App, vor dem Live-Schritt.
* Sicherheitsnetz: Lief sie bis „vor Live“ noch nie (z. B. abgebrochen), läuft sie dort —
  auch mit Staging. Lief sie schon, ist danach nichts mehr dran („keine“).

Ob das Repo eine Staging-App hat, sagt allein :func:`to_spawn.config.hat_staging`. Wer sich
merkt, dass die Komplett-Prüfung einer Spec lief, entscheidet der Aufrufer (Merker-Datei,
Teil C); hier zählt nur das Ergebnis ``komplett_lief``.

Zwei Varianten erwogen: ein freies ``spec_stand``-Dict (Tippfehler im Schlüssel = still
falsche Prüfung) gegen eine kleine unveränderliche Datenklasse mit Aufzählung
:class:`Moment` — gebaut ist die zweite, weil ein falscher Moment schon beim Anlegen scheitert
und jede Kombination aus Moment × ``komplett_lief`` eine definierte Antwort hat.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from to_spawn import config

log = logging.getLogger(__name__)

#: Antwort von :func:`welche`; ``keine`` = jetzt nichts prüfen.
Art = Literal["schnell", "komplett", "keine"]


class Moment(str, Enum):
    """Wo die Spec gerade steht, aus Sicht der Prüfung."""

    #: Ein Ticket wird eingereicht.
    TICKET = "ticket"
    #: Alle Tickets der Spec liegen auf Staging (``staging:fertig``).
    ALLE_AUF_STAGING = "alle_auf_staging"
    #: Die Spec geht gleich live.
    VOR_LIVE = "vor_live"


@dataclass(frozen=True)
class SpecStand:
    """Stand der Spec: ``moment`` plus ob ihre Komplett-Prüfung schon lief.

    ``Moment("…")`` wandelt Text; ein unbekannter Wert scheitert sofort mit ``ValueError``.
    """

    moment: Moment
    komplett_lief: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "moment", Moment(self.moment))
        object.__setattr__(self, "komplett_lief", bool(self.komplett_lief))


def welche(konfig: dict[str, Any], spec_stand: SpecStand) -> Art:
    """Welche Prüfung jetzt dran ist: ``schnell``, ``komplett`` oder ``keine``."""
    if spec_stand.moment is Moment.TICKET:
        return "schnell"
    if spec_stand.komplett_lief:
        return "keine"
    if spec_stand.moment is Moment.ALLE_AUF_STAGING:
        # Ohne Staging-App gibt es diesen Moment nicht; die Prüfung kommt erst vor Live.
        return "komplett" if config.hat_staging(konfig) else "keine"
    return "komplett"


def befehl(konfig: dict[str, Any], art: Art) -> str | None:
    """Befehl für ``art`` aus ``pruefung.schnell``/``pruefung.komplett``; ``None`` = keiner.

    ``None`` heißt bei ``keine`` „nichts zu tun“, sonst „nicht eingerichtet“ (Einrichtungs-Ticket).
    """
    if art == "keine":
        return None
    text = config.pruef_befehl(konfig, art)
    if not text:
        log.info("Prüf-Befehl pruefung.%s fehlt in .to-spawn/config.json", art)
        return None
    return text
