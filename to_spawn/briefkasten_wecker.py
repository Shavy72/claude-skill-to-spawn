"""Briefkasten-Wecker (#414): Frage und Antwort im Briefkasten wecken den Empfänger.

Bisher sah der Aufseher eine neue Frage erst in seinem Takt (``wache.py``, 30 min) und die
Bau-Session die Antwort erst beim nächsten Briefkasten-Blick (20 min). Dieser Faden läuft im
Aufseher-Prozess (``wache.py``, neben dem Handoff-Blick) und schaut alle :data:`TAKT_S` Sekunden
in den Briefkasten der Spec (nur Dateien, kein GitHub):

* neue offene Frage (mit id) → ein Weckruf ins eigene Pane des Aufsehers (``TMUX_PANE``);
* Antwort auf eine gesehene Frage liegt → ein Weckruf ins Fenster ``bau <N>`` der Spec-Sitzung.

Jede Frage weckt je Richtung höchstens einmal. Getippt wird nur in ein stilles Pane; arbeitet es
oder steht eine Rückfrage offen, versucht es der nächste Durchgang wieder. Was beim Start schon
offen ist, weckt den Aufseher nicht (er liest es im ersten Tick), seine Antwort weckt die
Bau-Session aber. Ohne ``TMUX_PANE`` (PC, kein tmux) läuft kein Faden — dann gilt wie bisher der
Takt, der ohnehin als Netz bleibt. Der Weckruf ist nur ein Anstoß: was gilt, steht im Briefkasten.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from to_spawn import aufpasser, aufseher_stand, briefkasten, manifest, respawn
from to_spawn.tmux_aufruf import FensterWeg, TmuxFehler

log = logging.getLogger(__name__)

#: Abstand zweier Durchgänge (s): Frage bzw. Antwort weckt spätestens nach ~1 min.
TAKT_S = 60.0
#: So lange wartet :func:`laeuft` beim Verlassen auf das Ende des Fadens (s).
STOPP_S = 5.0
AUFSEHER_TEXT = (
    "Briefkasten: neue Frage {fragen} — jetzt `to_spawn.py offene` ansehen und entscheiden bzw. an den "
    "Nutzer weitergeben (Befugnis-Abschnitt). Deinen Takt (ScheduleWakeup) nicht ändern."
)
BAU_TEXT = (
    "Briefkasten: Antwort auf Frage {id} liegt — jetzt `to_spawn.py antwort {n} --id {id}` nachsehen und weiterbauen."
)

Schluessel = tuple[str, str]  # (Ticket, Frage-id)


class Wecker:
    """Ein Durchgang je :meth:`einmal`; der erste (im Konstruktor) legt nur den Ausgangsstand fest.

    Nicht thread-sicher: :meth:`einmal` läuft nur in einem Faden (siehe :func:`laeuft`).
    """

    def __init__(self, spec: int, repo: Path, pane: str, werkzeug: respawn.TmuxWerkzeug | None = None) -> None:
        self.spec, self.repo, self.pane = spec, repo, pane
        self.werkzeug = werkzeug or respawn.TmuxWerkzeug()
        self._bekannt: set[Schluessel] | None = None  # je gesehene offene Frage
        self._fuer_aufseher: set[Schluessel] = set()
        self._fuer_bau: set[Schluessel] = set()  # gesehen, Bau-Session noch nicht geweckt
        self._gemeldet: set[str] = set()
        self.einmal()

    def einmal(self) -> None:
        """Ein Durchgang; wirft nie (gleicher Fehler-Text nur einmal laut)."""
        try:
            self._durchgang()
        except Exception as fehler:  # noqa: BLE001 — der Faden darf nie sterben
            self._melde(f"Briefkasten-Wecker Spec #{self.spec}: {fehler!r}")

    def _offen(self) -> set[Schluessel]:
        tickets = manifest.lade_manifest(self.repo, self.spec)["tickets"]
        return {(f["ticket"], f["id"]) for f in briefkasten.offene(tickets, hauptbaum=self.repo) if f["id"]}

    def _durchgang(self) -> None:
        offen = self._offen()
        if self._bekannt is None:
            self._bekannt, self._fuer_bau = set(offen), set(offen)
            return
        neu = offen - self._bekannt
        self._bekannt |= neu
        self._fuer_bau |= neu
        self._fuer_aufseher = (self._fuer_aufseher | neu) & offen  # schon Beantwortetes weckt nicht mehr
        if self._fuer_aufseher:
            fragen = ", ".join(f"#{t} id {fid}" for t, fid in sorted(self._fuer_aufseher))
            if self._tippe(self.pane, AUFSEHER_TEXT.format(fragen=fragen)):
                self._fuer_aufseher.clear()
        for t, fid in sorted(self._fuer_bau - offen):
            stand = briefkasten.lesen(t, fid, hauptbaum=self.repo)
            if stand == briefkasten.UNBEKANNT or (isinstance(stand, briefkasten.Antwort) and self._wecke_bau(t, fid)):
                self._fuer_bau.discard((t, fid))

    def _wecke_bau(self, t: str, fid: str) -> bool:
        """``True`` = erledigt (geweckt oder kein Fenster mehr), ``False`` = nächster Durchgang."""
        ziele = aufseher_stand.bau_fenster(self.spec)
        if ziele is None:
            return False
        ziel = ziele.get(int(t))
        if ziel is None:
            log.debug("#%s: Antwort %s liegt, aber kein Fenster bau %s — Takt übernimmt", t, fid, t)
            return True
        return self._tippe(ziel, BAU_TEXT.format(id=fid, n=t))

    def _tippe(self, ziel: str, text: str) -> bool:
        """Nur in ein stilles Pane tippen; ``False`` = arbeitet, Rückfrage oder tmux-Fehler."""
        try:
            bild = self.werkzeug.bildschirm(ziel)
            if bild is None or aufpasser.arbeitet(bild):
                return False
            self.werkzeug.tippen(ziel, text)
        except FensterWeg:
            return True  # Fenster weg: niemand mehr zu wecken
        except TmuxFehler as fehler:
            self._melde(f"Briefkasten-Wecker: Tippen in {ziel} gescheitert: {fehler}")
            return False
        log.info("Briefkasten-Wecker: %s geweckt — %s", ziel, text[:80])
        return True

    def _melde(self, text: str) -> None:
        if text in self._gemeldet:
            log.debug("%s", text)
            return
        self._gemeldet.add(text)
        log.warning("%s", text)


@contextmanager
def laeuft(spec: int, repo: Path, *, takt_s: float | None = None) -> Iterator[Wecker | None]:
    """Wecker als Daemon-Faden ``briefkasten-wecker-<S>``, solange der ``with``-Block läuft.

    Ohne ``TMUX_PANE`` (kein tmux) ``None`` und kein Faden: Fragen und Antworten nur im Takt.
    """
    pane = (os.environ.get("TMUX_PANE") or "").strip()
    if not pane:
        log.info("Briefkasten-Wecker Spec #%s aus: kein tmux-Pane — Fragen und Antworten nur im Takt", spec)
        yield None
        return
    abstand = TAKT_S if takt_s is None else takt_s
    wecker = Wecker(spec, repo, pane)
    stopp = threading.Event()

    def schleife() -> None:
        while not stopp.wait(abstand):
            wecker.einmal()

    faden = threading.Thread(target=schleife, name=f"briefkasten-wecker-{spec}", daemon=True)
    faden.start()
    log.info("Briefkasten-Wecker Spec #%s läuft (Takt %.0f s, Pane %s)", spec, abstand, pane)
    try:
        yield wecker
    finally:
        stopp.set()
        faden.join(timeout=STOPP_S)
