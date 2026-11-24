"""Briefkasten-Wecker (#414): Frage und Antwort im Briefkasten wecken den Empfänger.

Bisher sah der Aufseher eine neue Frage erst in seinem Takt (``wache.py``, 30 min) und die
Bau-Session die Antwort erst beim nächsten Briefkasten-Blick (20 min). Dieser Faden läuft im
Aufseher-Prozess (``wache.py``, neben dem Handoff-Blick) und schaut alle :data:`TAKT_S` Sekunden
in den Briefkasten der Spec (nur Dateien, kein GitHub):

* neue offene Frage (mit id) → ein Weckruf ins eigene Pane des Aufsehers (``TMUX_PANE``);
* Antwort auf eine gesehene Frage liegt → ein Weckruf ins Fenster ``bau <N>`` der Spec-Sitzung;
* Ticket der Spec geschlossen → Aufseher-Pane, Aufseher-Kommentar an offenem Ticket → ``bau <N>``
  (#554, :func:`ereignisse`; eine GraphQL-Abfrage je Durchgang für alle Tickets der Spec).

Jede Frage weckt je Richtung höchstens einmal. Getippt wird nur, wenn :meth:`Wecker._darf_tippen`
es erlaubt (die eine Stelle der Regel): im Pane läuft Claude, das Bild zeigt ein leeres
Eingabefeld, nichts arbeitet oder fragt zurück, und das Fenster ist seit :data:`RUHE_S` ruhig.
Sonst versucht es der nächste Durchgang wieder. Was beim Start schon offen ist, weckt den
Aufseher nicht (er liest es im ersten Tick), seine Antwort weckt die Bau-Session aber; scheitert
dieser erste Blick, steht das im Log, und der nächste Durchgang weckt mit allen offenen Fragen.

Grenzen (bewusst): Für Briefkasten und Abnahme-Kommentare liegt das Gedächtnis „schon geweckt“
nur im Speicher; nach einem Neustart des Aufsehers können diese Weckrufe verloren gehen — Netz
ist der feste Takt. Die GitHub-Ereignisse (#554) merkt :func:`gedaechtnis_datei` über Neustarts.
Ohne ``TMUX_PANE`` (PC, kein tmux) läuft kein Faden, und der Cron-Modus (``waechter_takt``) hat
keinen Wecker; dort gilt allein der Takt. Der Weckruf ist nur ein Anstoß: was gilt, steht im
Briefkasten.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

from to_spawn import (
    aufpasser,
    aufseher_stand,
    befugnis,
    briefkasten,
    capo,
    config,
    gh,
    manifest,
    respawn,
)
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

KOMMENTAR_TEXT = (
    "Abnahme-Issue #{ticket}: neuer Kommentar des Menschen (Kritik oder „passt“) — jetzt "
    "`gh issue view {ticket} --comments` lesen und darauf reagieren, nicht auf den eigenen Weckruf warten."
)

ZU_TEXT = (
    "Ticket #{ticket} ist geschlossen — jetzt den Stand der Spec ansehen und den nächsten Schritt "
    "(nächstes Ticket, Rundschau, Abschluss) anstoßen. Deinen Takt (ScheduleWakeup) nicht ändern."
)
AUFSEHER_KOMMENTAR_TEXT = (
    "Ticket #{ticket}: neuer Kommentar des Aufsehers — jetzt `gh issue view {ticket} --comments` lesen und umsetzen."
)
#: So viele jüngste Kommentare je Ticket liest die GitHub-Abfrage (ein Durchgang ≈ 1 min).
KOMMENTARE_JE_TICKET = 20

Schluessel = tuple[str, str]  # (Ticket, Frage-id)


class Ereignis(NamedTuple):
    """Ein Weckruf aus der GitHub-Lage (#554)."""

    schluessel: str  # über Neustarts stabil, z. B. ``zu:12`` oder ``kommentar:<id>``
    bau: str | None  # Ticket der zu weckenden Bau-Session; ``None`` = Aufseher-Pane
    text: str


def ereignisse(lage: dict[str, Any]) -> list[Ereignis]:
    """Die eine Ereignis-Quelle (#554): GitHub-Lage der Spec-Tickets → wen mit welchem Text wecken.

    ``lage`` = Ticket → ``{"state", "comments": {"nodes": [...]}}`` (Form von :meth:`Wecker._lage`).
    Ein weiteres Ereignis ist ein weiterer Zweig hier; Gedächtnis und Wecken bleiben unberührt.
    """
    aus: list[Ereignis] = []
    for t in sorted(lage, key=int):
        issue = lage[t]
        if not isinstance(issue, dict):
            continue
        if issue.get("state") == "CLOSED":
            aus.append(Ereignis(f"zu:{t}", None, ZU_TEXT.format(ticket=t)))
            continue
        for k in (issue.get("comments") or {}).get("nodes") or []:
            if (
                isinstance(k, dict)
                and k.get("authorAssociation") in capo.BETEILIGTE_ROLLEN
                and str(k.get("body") or "").lstrip().startswith(capo.AUFSEHER_KOEPFE)
            ):
                aus.append(Ereignis(f"kommentar:{k['databaseId']}", t, AUFSEHER_KOMMENTAR_TEXT.format(ticket=t)))
    return aus


def gedaechtnis_datei(repo: Path, spec: int) -> Path:
    """Gedächtnis „schon geweckt“ der GitHub-Ereignisse (#554), neben dem übrigen Zustand."""
    return repo / ".to-spawn" / f"wecker-{spec}.json"


class Wecker:
    """Ein Durchgang je :meth:`einmal`; der Konstruktor legt nur den Ausgangsstand fest.

    Nicht thread-sicher: :meth:`einmal` läuft nur in einem Faden (siehe :func:`laeuft`).
    """

    def __init__(
        self,
        spec: int,
        repo: Path,
        pane: str,
        werkzeug: respawn.TmuxWerkzeug | None = None,
        gh_repo: str = "",
    ) -> None:
        self.spec, self.repo, self.pane, self.gh_repo = spec, repo, pane, gh_repo
        # Abnahme-Issues (#528): Ticket → bekannte Mensch-Kommentar-ids; fehlt das Ticket, steht der Ausgangsstand aus.
        self._checkpoints: set[str] | None = None
        self._kommentare: dict[str, set[int]] = {}
        self._kommentar_aufseher: set[str] = set()
        self._kommentar_bau: set[str] = set()
        self.werkzeug = werkzeug or respawn.TmuxWerkzeug()
        self._bekannt: set[Schluessel] = set()  # je gesehene offene Frage
        self._fuer_aufseher: set[Schluessel] = set()
        self._fuer_bau: set[Schluessel] = set()  # gesehen, Bau-Session noch nicht geweckt
        self._gemeldet: set[str] = set()
        self._ruhe: dict[str, tuple[str, float]] = {}  # Ziel → (Bild-Hash, gleich seit)
        self._geweckt: set[str] | None = None  # #554: Schlüssel schon geweckter Ereignisse
        self._geschrieben: set[str] | None = None  # Stand der Gedächtnis-Datei
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
        try:
            self._kommentare_blick()
        except Exception as fehler:  # noqa: BLE001 — GitHub-Ausfall darf den Briefkasten nicht aufhalten
            self._melde(f"Briefkasten-Wecker Spec #{self.spec}: Kommentare: {fehler!r}")
        try:
            self._tickets_blick()
        except Exception as fehler:  # noqa: BLE001 — GitHub-Ausfall darf den Briefkasten nicht aufhalten
            self._melde(f"Briefkasten-Wecker Spec #{self.spec}: Tickets: {fehler!r}")
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

    def _lage(self, tickets: dict[str, Any]) -> dict[str, Any] | None:
        """Zustand + jüngste Kommentare aller Tickets in EINER GraphQL-Abfrage; ``None`` = Fehler."""
        besitzer, _, name = self.gh_repo.partition("/")
        felder = " ".join(
            f"t{int(t)}: issue(number: {int(t)}) {{ state comments(last: {KOMMENTARE_JE_TICKET}) "
            "{ nodes { databaseId body authorAssociation } } }"
            for t in tickets
        )
        daten = gh.json_lauf(
            [
                "api",
                "graphql",
                "-f",
                f"owner={besitzer}",
                "-f",
                f"name={name}",
                "-f",
                f"query=query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {felder} }} }}",
            ]
        )
        repo = ((daten or {}).get("data") or {}).get("repository") if isinstance(daten, dict) else None
        if not isinstance(repo, dict):
            return None
        return {str(t): repo.get(f"t{int(t)}") for t in tickets}

    def _lies_gedaechtnis(self) -> set[str] | None:
        """``None`` = keine (lesbare) Datei: dann wird der Bestand nur gemerkt, nicht geweckt."""
        datei = gedaechtnis_datei(self.repo, self.spec)
        if not datei.is_file():
            return None
        try:
            return {str(k) for k in json.loads(datei.read_text(encoding="utf-8"))["geweckt"]}
        except (OSError, ValueError, KeyError, TypeError) as fehler:
            log.warning("Wecker-Gedächtnis %s unlesbar (%r) — Bestand gilt als geweckt", datei, fehler)
            return None

    def _tickets_blick(self) -> None:
        """GitHub-Ereignisse (#554) wecken; erst nach erfolgreichem Wecken als „geweckt“ merken.

        Ohne Gedächtnis-Datei (erster Lauf) wird der Bestand nur gemerkt. Abfrage gescheitert:
        nichts wecken, nichts merken. Gemerkt bleibt nur, was GitHub noch zeigt — so weckt ein
        wieder geöffnetes und erneut geschlossenes Ticket noch einmal, und die Datei wächst nicht.
        """
        if not self.gh_repo:
            return
        lage = self._lage(manifest.lade_manifest(self.repo, self.spec)["tickets"])
        if lage is None:
            self._melde(
                f"Briefkasten-Wecker Spec #{self.spec}: GitHub-Abfrage der Tickets gescheitert — Takt übernimmt"
            )
            return
        jetzt = ereignisse(lage)
        if self._geweckt is None:
            self._geweckt = self._lies_gedaechtnis()
        if self._geweckt is None:
            self._geweckt = {e.schluessel for e in jetzt}
        offen = [e for e in jetzt if e.schluessel not in self._geweckt]
        for wen in dict.fromkeys(e.bau for e in offen):
            gruppe = [e for e in offen if e.bau == wen]
            text = " ".join(dict.fromkeys(e.text for e in gruppe))
            if self._tippe(self.pane, text) if wen is None else self._wecke_bau_text(wen, text):
                self._geweckt |= {e.schluessel for e in gruppe}
        self._geweckt &= {e.schluessel for e in jetzt}
        if self._geweckt != self._geschrieben:
            datei = gedaechtnis_datei(self.repo, self.spec)
            datei.parent.mkdir(parents=True, exist_ok=True)
            manifest.schreibe_atomar(datei, {"geweckt": sorted(self._geweckt)})
            self._geschrieben = set(self._geweckt)

    def _abnahme_tickets(self) -> set[str] | None:
        """Tickets der Spec mit Checkpoint-Label (einmal gelesen); ``None`` = GitHub nicht lesbar."""
        if self._checkpoints is not None:
            return self._checkpoints
        label = manifest.checkpoint_label(config.lade(self.repo))
        gefunden: set[str] = set()
        for t in manifest.lade_manifest(self.repo, self.spec)["tickets"]:
            daten = gh.json_lauf(["api", f"repos/{self.gh_repo}/issues/{t}"])
            if not isinstance(daten, dict):
                return None
            if any(isinstance(e, dict) and e.get("name") == label for e in daten.get("labels") or []):
                gefunden.add(str(t))
        self._checkpoints = gefunden
        return gefunden

    def _kommentare_blick(self) -> None:
        """Neuer Mensch-Kommentar am Abnahme-Issue → Aufseher-Pane und Bau-Fenster wecken (#528).

        Mensch = Kommentar ohne Marke des Aufsehers, Aufpassers oder der Bau-Session
        (``capo.EIGENE_KOEPFE``/``SESSION_KOPF``); Zitate beginnen mit ``>`` und zählen.
        Je Kommentar höchstens ein Weckruf je Richtung. Beim ersten Lesen (Neustart) weckt, was nach dem
        letzten eigenen Kommentar steht; alles davor ist Bestand.
        """
        if not self.gh_repo:
            return
        tickets = self._abnahme_tickets()
        for t in sorted(tickets or ()):
            kommentare = capo.issue_kommentare(self.gh_repo, int(t))
            if kommentare is None:
                continue
            koepfe = (*capo.EIGENE_KOEPFE, capo.SESSION_KOPF)
            eigen = [str(c.get("body") or "").startswith(koepfe) for c in kommentare]
            mensch = [(int(c["id"]), i) for i, (c, e) in enumerate(zip(kommentare, eigen, strict=True)) if not e]
            ids = {i for i, _ in mensch}
            bekannt = self._kommentare.get(t)
            if bekannt is None:
                # Ausgangsstand: was nach dem letzten eigenen Kommentar steht, ist unbeantwortet und weckt.
                letzter = max((i for i, e in enumerate(eigen) if e), default=-1)
                bekannt = {k for k, pos in mensch if pos < letzter}
            self._kommentare[t] = bekannt | ids
            if ids - bekannt:
                self._kommentar_aufseher.add(t)
                self._kommentar_bau.add(t)
        for t in sorted(self._kommentar_aufseher):
            if self._tippe(self.pane, KOMMENTAR_TEXT.format(ticket=t)):
                self._kommentar_aufseher.discard(t)
        for t in sorted(self._kommentar_bau):
            if self._wecke_bau_text(t, KOMMENTAR_TEXT.format(ticket=t)):
                self._kommentar_bau.discard(t)

    def _wecke_bau(self, t: str, fid: str) -> bool:
        """``True`` = erledigt (geweckt oder kein Fenster mehr), ``False`` = nächster Durchgang."""
        return self._wecke_bau_text(t, BAU_TEXT.format(id=fid, befehl=befugnis.antwort_befehl(t, fid)))

    def _wecke_bau_text(self, t: str, text: str) -> bool:
        ziele = aufseher_stand.bau_fenster(self.spec)
        if ziele is None:
            return False
        ziel = ziele.get(int(t))
        if ziel is None:
            log.debug("#%s: Weckruf fällig, aber kein Fenster bau %s — Takt übernimmt", t, t)
            return True
        return self._tippe(ziel, text)

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
def laeuft(spec: int, repo: Path, *, takt_s: float | None = None, gh_repo: str = "") -> Iterator[Wecker | None]:
    """Wecker als Daemon-Faden ``briefkasten-wecker-<S>``, solange der ``with``-Block läuft.

    Ohne ``TMUX_PANE`` (kein tmux) ``None`` und kein Faden: Fragen und Antworten nur im Takt.
    """
    pane = (os.environ.get("TMUX_PANE") or "").strip()
    if not pane:
        log.info("Briefkasten-Wecker Spec #%s aus: kein tmux-Pane — Fragen und Antworten nur im Takt", spec)
        yield None
        return
    abstand = TAKT_S if takt_s is None else takt_s
    wecker = Wecker(spec, repo, pane, gh_repo=gh_repo)
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
