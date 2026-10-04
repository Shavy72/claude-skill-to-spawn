"""Zweite Tür des Skills ``respawn``: den Bau-Aufseher ablösen (#436, Spec #399 E17).

Die Aufsicht (:func:`to_spawn.waechter_lauf.fahre`) ruft :func:`aufseher_abloesen`,
sobald der Kontext des Aufsehers die Handoff-Grenze erreicht — ohne dass der
Aufseher selbst daran denken muss. Dieselbe SOP wie :func:`to_spawn.respawn.abloesen`,
mit denselben Bausteinen (keine Kopien):

a) Handoff-Auftrag (``respawn._handoff_auftrag``) ins Pane des Aufsehers tippen; feste
   Pfade ``docs/HANDOFF_<tag>_waechter_<S>.md`` und
   ``docs/handoffs/START_<tag>_waechter_<S>.txt``.
d) warten, bis beide Dateien frisch, nicht leer und stabil sind
   (``respawn._warte_dateien`` mit Schlüssel ``waechter_<S>``).

Schritte b, c und e (neue Session, alte beenden) übernimmt die Aufsicht selbst: der
Aufseher läuft als Kind von ``wache.py`` (Limit-Aufsicht braucht ``Popen``), nicht in
einem eigenen tmux-Fenster. Abbruch (Exit 2): der Aufseher bekommt
:data:`WEITER_AUFTRAG_AUFSEHER` und bleibt im Amt.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from . import respawn

log = logging.getLogger("to_spawn.respawn_aufseher")

#: Wird dem Aufseher getippt, wenn die Ablösung nach Schritt a abbricht.
WEITER_AUFTRAG_AUFSEHER = (
    "Ablösung abgebrochen (respawn). Der Nachfolge-Aufseher startet nicht — arbeite als "
    "Bau-Aufseher normal weiter (nächster Tick wie gehabt). Handoff-Datei und Start-Prompt "
    "dürfen liegen bleiben."
)


@dataclass(frozen=True)
class AufseherErgebnis:
    """Ausgang der Aufseher-Ablösung: Exit-Code, eine Zeile, gefundene Dateien."""

    exit: int
    zeile: str
    handoff: Path | None = None
    start_prompt: str = ""


def aufseher_dateien(wurzel: Path, spec: int, seit: float) -> tuple[Path, Path]:
    """Feste Pfade für Handoff und Start-Prompt des Aufsehers (Datum aus ``seit``)."""
    tag = time.strftime("%Y-%m-%d", time.localtime(seit))
    return (
        wurzel / "docs" / f"HANDOFF_{tag}_waechter_{spec}.md",
        wurzel / respawn.HANDOFF_ORDNER / f"START_{tag}_waechter_{spec}.txt",
    )


def aufseher_abloesen(
    wurzel: Path,
    spec: int,
    ziel: str,
    *,
    werkzeug: respawn.Werkzeug | None = None,
    warte_max: float = respawn.WARTE_MAX_VORGABE,
) -> AufseherErgebnis:
    """Handoff + Start-Prompt beim Aufseher in Pane ``ziel`` anfordern und abwarten.

    ``wurzel`` = Arbeitsordner des Aufsehers (Pfade im Auftrag sind relativ dazu).
    Exit 0: beide Dateien da, ``handoff``/``start_prompt`` gefüllt — der Aufrufer
    beendet die Session und startet den Nachfolger. Exit 2: Dateien fehlen nach
    ``warte_max`` s, Weiter-Auftrag getippt. Exit 1: tmux-Fehler beim Tippen.
    Gibt nie eine Ausnahme weiter (eine Zeile statt Absturz der Aufsicht).
    """
    w = werkzeug or respawn.TmuxWerkzeug()
    kopf = f"respawn Aufseher #{spec}"
    seit = w.jetzt()
    handoff, start = aufseher_dateien(wurzel, spec, seit)
    schluessel = f"waechter_{spec}"
    try:
        w.tippen(
            ziel,
            respawn._handoff_auftrag(
                str(handoff.relative_to(wurzel)), str(start.relative_to(wurzel))
            ),
        )
    except respawn.TmuxFehler as fehler:
        log.warning("%s: Handoff-Auftrag an %s nicht getippt: %s", kopf, ziel, fehler)
        return AufseherErgebnis(respawn.EXIT_NICHT_BEWIESEN, f"{kopf}: tmux — {fehler}")
    log.info("%s: Handoff-Auftrag an %s getippt.", kopf, ziel)
    try:
        gefunden, _start, prompt = respawn._warte_dateien(
            w, handoff, start, seit, schluessel, warte_max
        )
    except respawn._Abbruch as abbruch:
        try:
            w.tippen(ziel, WEITER_AUFTRAG_AUFSEHER)
        except respawn.TmuxFehler as fehler:
            log.warning("%s: Weiter-Auftrag nicht getippt: %s", kopf, fehler)
        zeile = f"{kopf}: abgebrochen — {abbruch.grund}; Aufseher arbeitet weiter"
        return AufseherErgebnis(abbruch.exit_code, " ".join(zeile.split()))
    zeile = f"{kopf}: Handoff {gefunden.name} da — Nachfolger übernimmt"
    return AufseherErgebnis(respawn.EXIT_OK, zeile, gefunden, prompt)
