"""Handoff-Blick (#542): startet die Leiter binnen ~2,5 min nach einem fälligen Handoff.

Eine Bau-Session schreibt an ihrer Grenze selbst einen Handoff, committet ihn und wartet
am Prompt. Die Folge-Session startet die Eingriffs-Leiter (Befehl C) — bisher nur im
Aufseher-Tick (``wache.py``, alle 30 min), also bis zu 30 min Stillstand. Dieser leichte
Faden schaut eng auf die Handoffs der Spec und ruft die Leiter, sobald einer fällig ist.

Regel je Durchgang (:meth:`Blick.einmal`), je Ticket der Spec (Manifest):

* kein gültiger Handoff (:func:`eigener_handoff.aktuell`) → nichts;
* Handoff da, noch nicht fällig → nächster Durchgang genau zur Fälligkeit;
* Handoff fällig → erst jetzt GitHub/tmux fragen (:func:`aufseher_stand.ticket_lage`);
  nur bei Ticket offen UND Fenster still startet ``to_spawn.py leiter <S> <N>`` als
  eigener Prozess — derselbe Weg und dieselbe Sperre wie Befehl C;
* scheitert die Leiter (Exit ≠ 0), wird derselbe Handoff nie wieder versucht und der
  Fehler nur einmal gemeldet.

Was die Leiter dann tut (Stufe, Neustart ab Handoff, Sperre), entscheidet allein sie.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple, Protocol

from to_spawn import aufseher_stand, eigener_handoff, gh, manifest, prozessbaum

log = logging.getLogger(__name__)

#: Wartezeit zwischen zwei Durchgängen ohne nahe Fälligkeit (s): die Leiter startet so
#: spätestens ~2,5 min nach der Fälligkeit eines Handoffs.
STANDARD_S = 150.0
#: Kleinste Wartezeit (s) — auch wenn eine Fälligkeit „jetzt“ ist, nie im Kreis drehen.
MIN_S = 1.0
#: Höchstdauer eines Leiter-Laufs (s); die Ablösung startet Sessions, das darf dauern.
LEITER_ZEIT_S = 600.0
#: So lange wartet :func:`laeuft` beim Verlassen auf das Ende des Fadens (s).
STOPP_S = 5.0
#: Das Skript hinter Befehl C (Skill-Wurzel).
SKRIPT = Path(__file__).resolve().parent.parent / "to_spawn.py"


class LeiterLauf(NamedTuple):
    """Ergebnis eines Leiter-Laufs: Exit-Code und die eine Ausgabe-Zeile der Leiter."""

    code: int
    zeile: str


class Naht(Protocol):
    """Was der Blick von außen braucht. Echt: :class:`EchteNaht`; Tests ersetzen nur
    GitHub/tmux (``quellen``) und den Prozess-Start (:meth:`leiter`)."""

    def tickets(self, spec: int) -> list[int]:
        """Ticket-Nummern der Spec (lokales Manifest)."""
        ...

    def aktuell(self, ticket: int) -> eigener_handoff.Treffer | None:
        """Gültiger eigener Handoff des Tickets, fällig oder nicht."""
        ...

    def lage(self, spec: int, ticket: int) -> aufseher_stand.TicketLage | None:
        """Lage des Tickets aus GitHub + tmux (``None`` = kein Sub-Issue der Spec)."""
        ...

    def leiter(self, spec: int, ticket: int) -> LeiterLauf:
        """Befehl C für das Ticket ausführen."""
        ...


class Blick:
    """Ein Durchgang über alle Handoffs einer Spec; merkt sich gescheiterte Leiter-Läufe.

    Nicht thread-sicher: :meth:`einmal` läuft nur in einem Faden (siehe :func:`laeuft`).
    """

    def __init__(self, spec: int, naht: Naht) -> None:
        self.spec = spec
        self.naht = naht
        #: Handoffs (Ticket, Datei, Handoff-Zeit), deren Leiter-Lauf gescheitert ist.
        self._gescheitert: set[tuple[int, str, float]] = set()
        #: Schon laut gemeldete Fehler-Texte — jeder nur einmal als Warnung.
        self._gemeldet: set[str] = set()

    def einmal(self, jetzt: float) -> float:
        """Ein Durchgang; Rückgabe = Sekunden bis zum nächsten (> 0, ≤ :data:`STANDARD_S`).

        Wirft nie: jede Ausnahme wird gemeldet (gleicher Text nur einmal laut) und der
        Durchgang endet mit :data:`STANDARD_S`.
        """
        try:
            return self._durchgang(jetzt)
        except Exception as fehler:  # noqa: BLE001 — der Faden darf nie sterben
            self._melde(f"Handoff-Blick Spec #{self.spec}: {fehler!r}")
            return STANDARD_S

    def _durchgang(self, jetzt: float) -> float:
        warte = STANDARD_S
        for ticket in self.naht.tickets(self.spec):
            treffer = self.naht.aktuell(ticket)
            if treffer is None:
                continue
            schluessel = (ticket, treffer.datei, treffer.commit_zeit)
            if schluessel in self._gescheitert:
                continue
            if not treffer.faellig(jetzt):
                warte = min(warte, max(MIN_S, treffer.faellig_ab - jetzt))
                continue
            if self._bereit(ticket):
                self._leiter(ticket, schluessel)
        log.debug("Handoff-Blick Spec #%s: nächster Blick in %.0f s", self.spec, warte)
        return warte

    def _bereit(self, ticket: int) -> bool:
        """Ticket offen und Fenster still? Sonst (arbeitet, Rückfrage, zu) nichts tun."""
        lage = self.naht.lage(self.spec, ticket)
        if (
            lage is None
            or lage.offen is not True
            or lage.fenster != aufseher_stand.STILL
        ):
            log.debug(
                "#%s: Handoff fällig, aber %s — keine Leiter",
                ticket,
                "kein Sub-Issue"
                if lage is None
                else f"offen={lage.offen}, Fenster {lage.fenster}",
            )
            return False
        return True

    def _leiter(self, ticket: int, schluessel: tuple[int, str, float]) -> None:
        lauf = self.naht.leiter(self.spec, ticket)
        if lauf.code == 0:
            log.info("#%s: Handoff fällig — Leiter: %s", ticket, lauf.zeile)
            return
        self._gescheitert.add(schluessel)
        log.warning(
            "#%s: Leiter ab Handoff %s gescheitert (Exit %s): %s — für diesen Handoff "
            "kein weiterer Versuch, Aufseher-Tick übernimmt",
            ticket,
            schluessel[1],
            lauf.code,
            lauf.zeile,
        )

    def _melde(self, text: str) -> None:
        if text in self._gemeldet:
            log.debug("%s", text)
            return
        self._gemeldet.add(text)
        log.warning("%s", text)


class EchteNaht:
    """Die echte Außenwelt des Blicks: Manifest, Handoff-Erkennung, GitHub/tmux, Leiter.

    ``repo`` = Hauptbaum (Manifest, Bau-Log, Worktrees); ``ordner`` = Arbeitsordner, in dem
    die Leiter läuft (wie Befehl C des Aufsehers); ``gh_repo`` = ``owner/name`` (leer →
    aus ``origin``). ``quellen`` ersetzt GitHub/tmux (Tests); ``None`` = echte Quellen,
    erst beim ersten fälligen Handoff gebaut.
    """

    def __init__(
        self,
        repo: Path,
        ordner: Path,
        gh_repo: str,
        quellen: aufseher_stand.Quellen | None = None,
    ) -> None:
        self.repo = repo
        self.ordner = ordner
        self.gh_repo = gh_repo
        self._quellen = quellen

    def tickets(self, spec: int) -> list[int]:
        """Ticket-Nummern aus ``docs/agents/manifests/spec-<S>.json``; Namen ohne Zahl
        fallen weg. ``FileNotFoundError``/``ValueError`` wie :func:`manifest.lade_manifest`."""
        nummern = manifest.lade_manifest(self.repo, spec)["tickets"]
        return [
            int(n)
            for n in sorted(nummern, key=manifest.ticket_schluessel)
            if str(n).isdigit()
        ]

    def aktuell(self, ticket: int) -> eigener_handoff.Treffer | None:
        """Gültiger eigener Handoff (:func:`eigener_handoff.aktuell`)."""
        return eigener_handoff.aktuell(self.repo, ticket)

    def lage(self, spec: int, ticket: int) -> aufseher_stand.TicketLage | None:
        """Lage wie der Aufseher-Stand sie sieht; :class:`aufseher_stand.GhFehlt` /
        :class:`RuntimeError`, wenn GitHub nichts liefert."""
        return aufseher_stand.ticket_lage(spec, ticket, self._echte_quellen()).lage

    def _echte_quellen(self) -> aufseher_stand.Quellen:
        if self._quellen is None:
            slug = self.gh_repo or gh.repo_aus_origin(self.repo, fallback="")
            if not slug:
                raise RuntimeError("GitHub-Repo unbekannt (kein gh_repo, kein origin)")
            self._quellen = aufseher_stand.echte_quellen(self.repo, slug)
        return self._quellen

    def leiter(self, spec: int, ticket: int) -> LeiterLauf:
        """``to_spawn.py leiter <S> <N>`` als eigener Prozess im Arbeitsordner.

        Eigener Prozess = derselbe Weg und dieselbe Leiter-Sperre wie Befehl C, und was
        die Leiter an der Umgebung ändert (``spawn.neustart`` setzt ``TO_SPAWN_REPO``),
        bleibt im Kind. ``TO_SPAWN_REPO`` fehlt schon in der Kind-Umgebung: die Leiter
        nimmt das Repo aus ``cwd`` wie beim Aufseher (#205). Ohne Fenster, ohne stdin;
        die Ausgabe läuft in eine Temp-Datei statt in eine Pipe — gestartete Sessions
        erben sie sonst und ließen das Lesen bis zu ihrem Ende hängen.
        """
        befehl = [sys.executable, str(SKRIPT), "leiter", str(spec), str(ticket)]
        if self.gh_repo:
            befehl += ["--gh-repo", self.gh_repo]
        env = {k: v for k, v in os.environ.items() if k.upper() != "TO_SPAWN_REPO"}
        with tempfile.TemporaryFile() as ausgabe:
            try:
                fertig = subprocess.run(
                    befehl,
                    cwd=str(self.ordner),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=ausgabe,
                    stderr=subprocess.STDOUT,
                    timeout=LEITER_ZEIT_S,
                    check=False,
                    **prozessbaum.ohne_fenster(),
                )
                code = fertig.returncode
            except subprocess.TimeoutExpired:
                code = -1
            except OSError as fehler:
                return LeiterLauf(-1, f"#{ticket}: Leiter nicht startbar ({fehler})")
            ausgabe.seek(0)
            text = ausgabe.read().decode("utf-8", errors="replace")
        zeilen = [z.strip() for z in text.splitlines() if z.strip()]
        zeile = zeilen[-1] if zeilen else f"#{ticket}: Leiter ohne Ausgabe"
        if code == -1 and not zeilen:
            zeile = f"#{ticket}: Leiter nach {LEITER_ZEIT_S:.0f} s abgebrochen"
        return LeiterLauf(code, zeile)


@contextmanager
def laeuft(
    spec: int, naht: Naht, *, uhr: Callable[[], float] = time.time
) -> Iterator[Blick]:
    """Blick als Daemon-Faden ``handoff-blick-<S>``, solange der ``with``-Block läuft.

    Zwischen zwei Durchgängen wartet der Faden auf ein Stopp-Signal (kein ``sleep``),
    beim Verlassen endet er sofort; läuft gerade eine Leiter, wartet das Verlassen
    höchstens :data:`STOPP_S` und lässt den Daemon-Faden dann allein auslaufen.
    """
    blick = Blick(spec, naht)
    stopp = threading.Event()

    def schleife() -> None:
        while not stopp.is_set():
            stopp.wait(blick.einmal(uhr()))

    faden = threading.Thread(target=schleife, name=f"handoff-blick-{spec}", daemon=True)
    faden.start()
    log.info("Handoff-Blick Spec #%s läuft (Takt ≤ %.0f s)", spec, STANDARD_S)
    try:
        yield blick
    finally:
        stopp.set()
        faden.join(timeout=STOPP_S)
        if faden.is_alive():
            log.warning(
                "Handoff-Blick Spec #%s: Faden endet nach laufender Leiter von selbst",
                spec,
            )
        else:
            log.info("Handoff-Blick Spec #%s gestoppt", spec)
