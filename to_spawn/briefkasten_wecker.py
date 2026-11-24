"""Briefkasten-Wecker (#414): Frage und Antwort im Briefkasten wecken den Empfänger.

Bisher sah der Aufseher eine neue Frage erst in seinem Takt (``wache.py``, 30 min) und die
Bau-Session die Antwort erst beim nächsten Briefkasten-Blick (20 min). Dieser Faden läuft im
Aufseher-Prozess (``wache.py``, neben dem Handoff-Blick) und schaut alle :data:`TAKT_S` Sekunden
in den Briefkasten der Spec (nur Dateien, kein GitHub):

* neue offene Frage (mit id) → ein Weckruf ins eigene Pane des Aufsehers (``TMUX_PANE``);
* Antwort auf eine gesehene Frage liegt → ein Weckruf ins Fenster ``bau <N>`` der Spec-Sitzung.

Jede Frage weckt je Richtung höchstens einmal. Getippt wird nur, wenn :meth:`Wecker._darf_tippen`
es erlaubt (die eine Stelle der Regel): im Pane läuft Claude, das Bild zeigt ein leeres
Eingabefeld, nichts arbeitet oder fragt zurück, und das Fenster ist seit :data:`RUHE_S` ruhig.
Sonst versucht es der nächste Durchgang wieder. Was beim Start schon offen ist, weckt den
Aufseher nicht (er liest es im ersten Tick), seine Antwort weckt die Bau-Session aber; scheitert
dieser erste Blick, steht das im Log, und der nächste Durchgang weckt mit allen offenen Fragen.

Grenzen (bewusst): Das Gedächtnis „schon geweckt“ liegt nur im Speicher des Aufseher-Prozesses.
Nach einem Neustart des Aufsehers können Weckrufe verloren gehen — Netz ist der feste Takt.
Ohne ``TMUX_PANE`` (PC, kein tmux) läuft kein Faden, und der Cron-Modus (``waechter_takt``) hat
keinen Wecker; dort gilt allein der Takt. Der Weckruf ist nur ein Anstoß: was gilt, steht im
Briefkasten.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from to_spawn import aufpasser, aufseher_stand, befugnis, briefkasten, manifest, respawn
from to_spawn.tmux_aufruf import FensterWeg, TmuxFehler

log = logging.getLogger(__name__)

#: Abstand zweier Durchgänge (s): Frage bzw. Antwort weckt spätestens nach ~1 min.
TAKT_S = 60.0
#: So lange muss ein Fenster ohne Aktivität sein, bevor getippt wird (s).
RUHE_S = 60.0
#: So lange wartet :func:`laeuft` beim Verlassen auf das Ende des Fadens (s).
STOPP_S = 5.0
AUFSEHER_TEXT = (
    "Briefkasten: neue Frage {fragen} — jetzt `to_spawn.py offene` ansehen und entscheiden bzw. an den "
    "Nutzer weitergeben (Befugnis-Abschnitt). Deinen Takt (ScheduleWakeup) nicht ändern."
)
BAU_TEXT = "Briefkasten: Antwort auf Frage {id} liegt — jetzt `{befehl}` nachsehen und weiterbauen."

Schluessel = tuple[str, str]  # (Ticket, Frage-id)


class Wecker:
    """Ein Durchgang je :meth:`einmal`; der Konstruktor legt nur den Ausgangsstand fest.

    Nicht thread-sicher: :meth:`einmal` läuft nur in einem Faden (siehe :func:`laeuft`).
    """

    def __init__(self, spec: int, repo: Path, pane: str, werkzeug: respawn.TmuxWerkzeug | None = None) -> None:
        self.spec, self.repo, self.pane = spec, repo, pane
        self.werkzeug = werkzeug or respawn.TmuxWerkzeug()
        self._bekannt: set[Schluessel] = set()  # je gesehene offene Frage
        self._fuer_aufseher: set[Schluessel] = set()
        self._fuer_bau: set[Schluessel] = set()  # gesehen, Bau-Session noch nicht geweckt
        self._gemeldet: set[str] = set()
        self._ruhe: dict[str, tuple[str, float]] = {}  # Ziel → (Bild-Hash, gleich seit)
        try:
            self._bekannt = self._offen()
        except Exception as fehler:  # noqa: BLE001 — ohne Ausgangsstand weckt der nächste Durchgang alles
            log.warning(
                "Briefkasten-Wecker Spec #%s: erster Blick gescheitert (%r) — nächster Durchgang weckt mit allen offenen Fragen",
                spec,
                fehler,
            )
        self._fuer_bau = set(self._bekannt)

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
        neu = offen - self._bekannt
        self._bekannt |= neu
        self._fuer_bau |= neu
        self._fuer_aufseher = (self._fuer_aufseher | neu) & offen  # schon Beantwortetes weckt nicht mehr
        if self._fuer_aufseher:
            fragen = ", ".join(f"#{t} id {fid}" for t, fid in sorted(self._fuer_aufseher))
            if self._tippe(self.pane, AUFSEHER_TEXT.format(fragen=fragen)):
                self._fuer_aufseher.clear()
        for t, fid in sorted(self._fuer_bau - offen):
            try:
                stand = briefkasten.lesen(t, fid, hauptbaum=self.repo)
                erledigt = stand == briefkasten.UNBEKANNT or (
                    isinstance(stand, briefkasten.Antwort) and self._wecke_bau(t, fid)
                )
            except Exception as fehler:  # noqa: BLE001 — ein kaputtes Ticket hält die anderen nicht auf
                self._melde(f"Briefkasten-Wecker #{t} Frage {fid}: {fehler!r}")
                continue
            if erledigt:
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
        return self._tippe(ziel, BAU_TEXT.format(id=fid, befehl=befugnis.antwort_befehl(t, fid)))

    def _darf_tippen(self, ziel: str) -> bool:
        """Die eine Regel „darf ich in dieses Fenster tippen“ (#414); alle Teile müssen gelten:

        * im Pane läuft Claude, nicht nur die Shell (sonst führt Bash den Weckruf aus);
        * das Bild zeigt das Claude-Eingabefeld, und hinter ``❯`` steht nichts
          (kein halber Text eines Menschen, kein Prompt-Vorschlag);
        * nichts arbeitet und keine Rückfrage steht offen (:func:`aufpasser.arbeitet`);
        * seit :data:`RUHE_S` keine Aktivität: ``window_activity`` alt genug, oder — weil tmux die
          tickende Statuszeile als Aktivität zählt — das Bild ohne Statuszeile so lange gleich.

        :class:`FensterWeg` und :class:`TmuxFehler` fliegen weiter.
        """
        pid, aktiv = self.werkzeug.pane_lage(ziel)
        if pid is None or not self.werkzeug.claude_laeuft(pid):
            return False
        farbig = self.werkzeug.bildschirm(ziel, farben=True)
        if farbig is None:
            return False
        bild = aufpasser.ohne_farben(farbig)
        if aufpasser.arbeitet(bild) or not aufpasser.eingabe_leer(farbig):
            return False
        jetzt = self.werkzeug.jetzt()
        bild_hash = aufpasser.pane_hash(bild)
        vorher = self._ruhe.get(ziel)
        if vorher is None or vorher[0] != bild_hash:
            vorher = self._ruhe[ziel] = (bild_hash, jetzt)
        ruhig_seit = vorher[1] if aktiv is None else min(aktiv, vorher[1])
        return jetzt - ruhig_seit >= RUHE_S

    def _tippe(self, ziel: str, text: str) -> bool:
        """Nur tippen, wenn :meth:`_darf_tippen`; ``False`` = nächster Durchgang versucht es wieder."""
        try:
            if not self._darf_tippen(ziel):
                return False
            self.werkzeug.tippen(ziel, text)
            self._ruhe.pop(ziel, None)
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
