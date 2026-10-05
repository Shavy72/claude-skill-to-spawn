"""Eingriffs-Leiter je Bau-Ticket (#432, Spec #399: E15, E16, E11).

Eine Tür: :func:`eingreifen`. Der Aufseher (über einen Ablöse-Subagenten) ruft sie
je Ticket auf; sie liest die Lage, entscheidet die nächste Stufe, führt sie aus und
gibt GENAU EINE Zeile zurück. Die Stufe merkt sie im Leitstand, damit kein Eingriff
doppelt passiert.

Stufen (feste Schwellen :data:`STUPS_MIN`, :data:`NACH_STUPS_MIN`,
:data:`MIN_RUHE_MIN`, :data:`WARTE_MAX_MIN`):

0. nichts gemerkt — still ≥ 20 min → Mindset-Stupser tippen (Stufe 1).
1. angestupst — weitere 15 min still → Handoff anfordern (Stufe 2).
   Handoff-Grenze (Kontext der aktuellen Session ≥ ``haupt.handoff_k`` der
   Smart-Zone-SSOT) → sofort Stufe 2, auch aus Stufe 0 — aber erst ab Mindest-Ruhe.
2. Handoff angefordert — sobald die Start-Prompt-Datei frisch da ist → Stufe 3 =
   Skill ``respawn`` (:func:`respawn.abloesen`), nie ein eigener Startweg. Nach 30 min
   ohne Datei → Exit 1, Aufseher prüft.
3. respawn läuft — VOR dem Ablösen gemerkt (scheitert das Merken: Exit 1, nichts
   abgelöst). Erfolg → Stufe 0 + Respawn-Zeit in einem Schreibvorgang (älterer
   Kontext zählt nicht mehr). Bleibt Stufe 3 stehen (Abschluss nicht gemerkt), wird
   nie erneut abgelöst: Fenster arbeitet UND neue Session belegt (``session_start``
   im Bau-Log jünger als die Stufe-3-Zeit) → Abschluss nachholen, sonst Exit 1.
4. Ticket zu und Fenster still (ab Mindest-Ruhe) → ``/exit`` tippen, genau einmal.
5. respawn gescheitert — nichts mehr tippen/starten, jede Ausführung Exit 1, bis das
   Fenster wieder arbeitet oder das Ticket neu beginnt (``session_start`` im Bau-Log
   jünger als die Stufe-5-Zeit; die wird erst nach dem Ende von ``abloesen`` gesetzt).

Eigener Handoff (#542): Hat die Session in Stufe 0/1 selbst einen Handoff committet und
arbeitet seit :data:`eigener_handoff.STILL_MIN` Minuten nicht, startet die Leiter die
Folge-Session ab diesem Handoff neu (Aktion ``folge_handoff``) — Regel und Start in
:mod:`to_spawn.eigener_handoff`.

Am PC (Windows, #501) ist nichts tippbar: :func:`fuer_rechner` macht aus Stufe 1/2
direkt Stufe 3 (Zeile „PC: Stufe 1 übersprungen“), aus ``/exit`` eine Meldung.

In ein Fenster, das arbeitet oder eine Rückfrage zeigt, wird nie getippt. Arbeitet
die Session nach einem Stupser (oder nach Stufe 5) wieder, fällt die Leiter auf
Stufe 0 zurück. Erst wird die Stufe gemerkt, dann getippt — scheitert das Tippen,
wird nicht nochmal getippt.

:func:`entscheide` ist reine Logik (keine Außenwelt). Alle Außenwelt-Zugriffe laufen
über das Protokoll :class:`Umwelt`; echt ist :func:`echte_umwelt`, Tests geben ein
Fake hinein. Exit-Codes: 0 ok (Eingriff oder nichts nötig) · 1 Fehler (tmux/gh/
Leitstand) oder Aufseher muss prüfen (Stufe 5, Wartegrenze) · bei Stufe 3 der
Exit-Code von ``respawn``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from to_spawn import (
    anleitung,
    aufseher_stand,
    bau_log,
    config,
    eigener_handoff,
    gh,
    leitstand,
    pc_fenster,
    respawn,
)

log = logging.getLogger(__name__)

#: Minuten still, bevor Stufe 0 anstupst.
STUPS_MIN = 20
#: Minuten nach dem Stupser (und still), bevor der Handoff angefordert wird.
NACH_STUPS_MIN = 15
#: Mindest-Ruhe in Minuten, bevor Handoff-Grenze oder ``/exit`` greifen.
MIN_RUHE_MIN = 2
#: Höchste Wartezeit in Stufe 2 auf die Start-Prompt-Datei (wie respawn).
WARTE_MAX_MIN = int(respawn.WARTE_MAX_VORGABE // 60)
#: Rückfall der Handoff-Grenze in k-Token (SSOT-Stand 2026-10-04: haupt.handoff_k).
HANDOFF_K_VORGABE = 250.0
SMART_ZONE_DATEI = Path("~/.claude/smart-zone.json")

EXIT_OK = 0
EXIT_FEHLER = 1

#: Stufe 1 (E11 Richtung 2): Mindset-Stoß, Text aus der Aufseher-Anleitung (#500).
MINDSET_TEXT = anleitung.anstupser("Aufseher")
EXIT_TEXT = "/exit"

#: ``melden`` = nichts tun, Exit 1: der Aufseher muss prüfen.
Aktion = Literal[
    "nichts",
    "anstupsen",
    "handoff",
    "respawn",
    "abschliessen",
    "exit",
    "zuruecksetzen",
    "melden",
    "folge_handoff",
]


@dataclass(frozen=True)
class Lage:
    """Was die Leiter über ein Ticket weiß (``None`` = unbekannt)."""

    offen: bool | None  # False = Ticket zu
    fenster: str  # aufseher_stand.ARBEITET | STILL | RUECKFRAGE | KEIN_FENSTER | ...
    still_min: int | None
    kontext_k: float | None  # Spitzen-Kontext aus dem Bau-Log
    prompt_datei: bool  # frische START_*_<N>.txt seit dem letzten Eingriff
    ziel: str | None = None  # tmux-Ziel des Fensters ``bau <N>``
    letzter_start: float | None = None  # jüngster ``session_start`` im Bau-Log
    log_unlesbar: bool = False  # Bau-Log da, aber Lesen scheiterte (≠ „kein Start“)
    #: Committeter eigener Handoff der aktuellen Session (#542), ``None`` = keiner.
    eigener_handoff: eigener_handoff.Treffer | None = None


@dataclass(frozen=True)
class Gemerkt:
    """Leitstand: 0 nichts, 1 angestupst, 2 Handoff, 3 respawn läuft, 4 /exit, 5 gescheitert."""

    stufe: int
    seit: float | None


@dataclass(frozen=True)
class Schritt:
    aktion: Aktion
    grund: str


@dataclass(frozen=True)
class Ergebnis:
    """Ausgang: Exit-Code und genau eine Zeile Klartext."""

    exit: int
    zeile: str


class Umwelt(Protocol):
    """Alle Außenwelt-Zugriffe der Leiter (Naht für Tests)."""

    def jetzt(self) -> float: ...
    def lage(self, spec: int, ticket: int, seit: float | None) -> Lage: ...
    def handoff_k(self) -> float: ...
    def worktree(self, ticket: int) -> Path: ...
    def werkzeug(self) -> respawn.Werkzeug | pc_fenster.PcWerkzeug:
        """Werkzeug von respawn: tmux (``tippen`` = Text einfügen, Enter getrennt) oder PC."""
        ...
    def folge_ab_handoff(self, spec: int, ticket: int, treffer: eigener_handoff.Treffer) -> int:
        """Folge-Session ab eigenem Handoff starten (#542); Exit-Code des Neustarts."""
        ...


def entscheide(lage: Lage, gemerkt: Gemerkt, jetzt: float, handoff_k: float) -> Schritt:
    """Nächster Schritt der Leiter — reine Logik, Regeln im Modul-Kommentar."""
    stufe = gemerkt.stufe
    if stufe == 3:  # nie erneut ablösen — die frische Session liefe sonst doppelt
        if lage.fenster == aufseher_stand.ARBEITET:
            # Arbeitet allein beweist nichts — es kann noch die alte Session sein.
            if _neu_gestartet(lage, gemerkt):
                return Schritt(
                    "abschliessen", "neue Session arbeitet, Abschluss nachgeholt"
                )
            if lage.log_unlesbar:
                text = "hängt, Bau-Log unlesbar — neue Session nicht prüfbar"
                return Schritt("melden", f"{text}, Aufseher prüfen")
            return Schritt(
                "melden", "hängt, keine neue Session belegt — Aufseher prüfen"
            )
        vor = _minuten(jetzt, gemerkt.seit)
        text = f"respawn vor {vor} min nicht abgeschlossen — Aufseher prüfen"
        return Schritt("melden", text)
    if stufe == 5 and _neu_gestartet(lage, gemerkt):
        return Schritt(
            "zuruecksetzen", "Ticket neu gestartet nach gescheitertem respawn"
        )
    if lage.fenster == aufseher_stand.ARBEITET:
        if stufe == 1:
            return Schritt("zuruecksetzen", "arbeitet wieder nach Stupser")
        if stufe == 5:
            return Schritt(
                "zuruecksetzen", "arbeitet wieder nach gescheitertem respawn"
            )
        return Schritt("nichts", "arbeitet")
    if stufe == 5:
        vor = _minuten(jetzt, gemerkt.seit)
        return Schritt("melden", f"respawn gescheitert vor {vor} min — Aufseher prüfen")
    if lage.fenster == aufseher_stand.RUECKFRAGE:
        return Schritt("nichts", "Rückfrage offen — Aufseher antwortet")
    if lage.fenster != aufseher_stand.STILL:
        return Schritt("nichts", f"Fenster: {lage.fenster}")
    still = lage.still_min or 0
    ruhig = still >= MIN_RUHE_MIN
    if lage.offen is False:
        if stufe == 4:
            return Schritt("nichts", "Ticket zu, /exit schon getippt")
        if not ruhig:
            return Schritt("nichts", f"Ticket zu, still {still} min < {MIN_RUHE_MIN}")
        return Schritt("exit", f"Ticket zu, still {still} min")
    if stufe == 4:
        return Schritt("zuruecksetzen", "Ticket wieder offen")
    treffer = lage.eigener_handoff
    if stufe in eigener_handoff.STUFEN and treffer is not None:
        seit = treffer.still_min(jetzt)
        if treffer.faellig(jetzt):
            return Schritt("folge_handoff", f"eigener Handoff {treffer.datei}, seit {seit} min still")
        return Schritt("nichts", f"eigener Handoff vor {seit} min < {eigener_handoff.STILL_MIN}")
    if stufe == 2:
        if lage.prompt_datei:
            return Schritt("respawn", "Start-Prompt-Datei da")
        warte = _minuten(jetzt, gemerkt.seit)
        if warte >= WARTE_MAX_MIN:
            text = f"wartet {warte} min auf Prompt-Datei — Aufseher prüfen"
            return Schritt("melden", text)
        return Schritt("nichts", f"wartet auf Start-Prompt-Datei seit {warte} min")
    if ruhig and lage.kontext_k is not None and lage.kontext_k >= handoff_k:
        return Schritt(
            "handoff", f"Kontext {lage.kontext_k:g}k ≥ Handoff-Grenze {handoff_k:g}k"
        )
    if stufe == 1:
        seit_stups = _minuten(jetzt, gemerkt.seit)
        if still >= NACH_STUPS_MIN and seit_stups >= NACH_STUPS_MIN:
            return Schritt("handoff", f"{seit_stups} min nach Stupser still")
        return Schritt("nichts", f"angestupst vor {seit_stups} min < {NACH_STUPS_MIN}")
    if still >= STUPS_MIN:
        return Schritt("anstupsen", f"still {still} min")
    return Schritt("nichts", f"still {still} min < {STUPS_MIN}")


#: Vermerk in der Zeile, wenn der PC-Weg das Tippen überspringt (#501 E10).
PC_VERMERK = "PC: Stufe 1 übersprungen"
#: Am PC ersetzt die Ablösung (Session beenden, ohne eigenen Handoff) das Anstupsen — darum
#: erst nach langer echter Stille; Stille zählt dort Subagenten und Werkzeug-Prozesse mit.
PC_ABLOESE_MIN = 60


def fuer_rechner(schritt: Schritt, kann_tippen: bool, still_min: int | None = None) -> Schritt:
    """Am PC (nichts tippbar): Handoff-Anfordern (Kontext-Grenze) wird direkt die Ablösung,
    Anstupsen erst ab ``PC_ABLOESE_MIN`` still, ``/exit`` eine Meldung — reine Logik."""
    if kann_tippen:
        return schritt
    if schritt.aktion == "anstupsen" and (still_min or 0) < PC_ABLOESE_MIN:
        return Schritt("nichts", f"PC: still {still_min or 0} min < {PC_ABLOESE_MIN}, noch keine Ablösung")
    if schritt.aktion in ("anstupsen", "handoff"):
        return Schritt("respawn", f"{PC_VERMERK} — {schritt.grund}")
    if schritt.aktion == "exit":
        return Schritt("melden", f"{schritt.grund}, PC: /exit nicht tippbar — Aufseher prüfen")
    return schritt


def _neu_gestartet(lage: Lage, gemerkt: Gemerkt) -> bool:
    """``session_start`` jünger als der gemerkte Eingriff = das Ticket beginnt neu."""
    return (
        lage.letzter_start is not None
        and gemerkt.seit is not None
        and lage.letzter_start > gemerkt.seit
    )


def _minuten(jetzt: float, seit: float | None) -> int:
    return 0 if seit is None else max(0, int((jetzt - seit) // 60))


# --- Tür -------------------------------------------------------------------------------


_WORT: dict[str, str] = {
    "nichts": "nichts",
    "anstupsen": "angestupst",
    "handoff": "Handoff angefordert",
    "respawn": "abgelöst (respawn)",
    "abschliessen": "respawn abgeschlossen",
    "exit": "/exit getippt",
    "zuruecksetzen": "zurückgesetzt",
    "melden": "",
    "folge_handoff": "neu gestartet ab eigenem Handoff",
}


def _zeile(ticket: int, text: str) -> str:
    return " ".join(f"leiter #{ticket}: {text}".split())


def eingreifen(
    repo: Path,
    spec: int,
    ticket: int,
    *,
    umwelt: Umwelt | None = None,
    gh_repo: str = "",
    dry_run: bool = False,
) -> Ergebnis:
    """Ein Schritt der Leiter für ``ticket``; gibt nie eine Ausnahme weiter.

    ``dry_run``: nur entscheiden und die Zeile bauen — nichts tippen, nichts merken.
    """
    try:
        u = umwelt or echte_umwelt(repo, gh_repo)
        if dry_run:
            return _plane(u, spec, ticket)
        halter = leitstand.versuche(f"leiter-{ticket}", f"leiter-{os.getpid()}")
        if halter is None:
            return Ergebnis(EXIT_OK, _zeile(ticket, "läuft schon — nichts getan"))
        with halter:
            return _schritt(u, repo, spec, ticket)
    except Exception as fehler:  # Tür gibt nie eine Ausnahme weiter (geloggt)
        log.exception("leiter #%s abgebrochen.", ticket)
        return Ergebnis(
            EXIT_FEHLER, _zeile(ticket, f"Fehler — {type(fehler).__name__}: {fehler}")
        )


def _lies(u: Umwelt, spec: int, ticket: int) -> tuple[Gemerkt, Lage, Schritt, float]:
    stufe, seit = leitstand.leiter_eintrag(ticket)
    gemerkt = Gemerkt(stufe, seit)
    jetzt = u.jetzt()
    lage = u.lage(spec, ticket, seit if stufe == 2 else None)
    schritt = entscheide(lage, gemerkt, jetzt, u.handoff_k())
    return gemerkt, lage, fuer_rechner(schritt, pc_fenster.kann_tippen(u.werkzeug()), lage.still_min), jetzt


def _plane(u: Umwelt, spec: int, ticket: int) -> Ergebnis:
    gemerkt, _, schritt, _ = _lies(u, spec, ticket)
    text = f"Stufe {gemerkt.stufe} {schritt.aktion} geplant (dry-run) — {schritt.grund}"
    return Ergebnis(EXIT_OK, _zeile(ticket, text))


def _schritt(u: Umwelt, repo: Path, spec: int, ticket: int) -> Ergebnis:
    gemerkt, lage, schritt, jetzt = _lies(u, spec, ticket)
    log.info("leiter #%s: %s → %s (%s)", ticket, gemerkt, schritt.aktion, schritt.grund)
    if schritt.aktion == "melden":
        text = f"Stufe {gemerkt.stufe} {schritt.grund}"
        return Ergebnis(EXIT_FEHLER, _zeile(ticket, text))
    if schritt.aktion == "respawn":
        return _respawn(u, repo, spec, ticket, gemerkt, jetzt)
    if schritt.aktion == "folge_handoff" and lage.eigener_handoff is not None:
        code = u.folge_ab_handoff(spec, ticket, lage.eigener_handoff)
        if code != 0:
            return Ergebnis(EXIT_FEHLER, _zeile(ticket, f"Neustart ab Handoff gescheitert (Exit {code}) — {schritt.grund}"))
        return Ergebnis(EXIT_OK, _zeile(ticket, f"Stufe 0 {_WORT[schritt.aktion]} — {schritt.grund}"))
    stufe = gemerkt.stufe
    if schritt.aktion in ("anstupsen", "handoff", "exit"):
        if not lage.ziel:
            raise RuntimeError(f"tmux-Ziel von bau {ticket} unbekannt")
        if schritt.aktion == "anstupsen":
            stufe, text = 1, MINDSET_TEXT.format(min=lage.still_min or 0)
        elif schritt.aktion == "handoff":
            stufe = 2
            text = respawn.handoff_auftrag_fuer(u.worktree(ticket), ticket, jetzt)
        else:
            stufe, text = 4, EXIT_TEXT
        # Erst merken, dann tippen: scheitert das Tippen, tippt der nächste Lauf nicht
        # nochmal (lieber ein Eingriff zu wenig als doppelt).
        leitstand.setze_leiter_stufe(ticket, stufe, jetzt)
        try:
            u.werkzeug().tippen(lage.ziel, text)
        except (OSError, RuntimeError, ValueError) as fehler:
            log.warning("leiter #%s: Tippen gescheitert: %s", ticket, fehler)
            zeile = f"Stufe {stufe} gemerkt, Tippen gescheitert — {fehler}"
            return Ergebnis(EXIT_FEHLER, _zeile(ticket, zeile))
    elif schritt.aktion == "zuruecksetzen":
        stufe = 0
        leitstand.setze_leiter_stufe(ticket, 0, None)
    elif schritt.aktion == "abschliessen":
        stufe = 0
        leitstand.schliesse_leiter_respawn(ticket, gemerkt.seit or jetzt)
    text = f"Stufe {stufe} {_WORT[schritt.aktion]} — {schritt.grund}"
    return Ergebnis(EXIT_OK, _zeile(ticket, text))


def _respawn(
    u: Umwelt, repo: Path, spec: int, ticket: int, gemerkt: Gemerkt, jetzt: float
) -> Ergebnis:
    """Stufe 3: erst „respawn läuft“ merken, dann Skill respawn.

    Gescheitert → Stufe 5 mit der Zeit NACH ``abloesen`` (ein ``session_start`` des
    abgebrochenen neuen Fensters setzt sonst sofort zurück). Erfolg → Stufe 0 +
    Respawn-Zeit in einem Schreibvorgang; scheitert der (oder das Merken von Stufe
    5), bleibt Stufe 3 (kein zweites Ablösen), Exit 1 und die Zeile sagt es. Nach
    gescheitertem Stufe-5-Merken wird Stufe 3 mit der Zeit NACH ``abloesen`` neu
    gemerkt — sonst wäre der ``session_start`` des abgebrochenen Fensters jünger und
    ein Folgelauf mit „arbeitet“ schlösse still ab.
    """
    try:
        leitstand.setze_leiter_stufe(ticket, 3, jetzt)
    except (OSError, ValueError, leitstand.ZustandKaputt) as fehler:
        log.warning("leiter #%s: Stufe 3 nicht gemerkt: %s", ticket, fehler)
        text = f"Stufe 2 Leitstand nicht schreibbar, nichts abgelöst — {fehler}"
        return Ergebnis(EXIT_FEHLER, _zeile(ticket, text))
    erg = respawn.abloesen(
        repo, spec, ticket, werkzeug=u.werkzeug(), handoff_seit=gemerkt.seit
    )
    if erg.exit != respawn.EXIT_OK:
        nach = u.jetzt()
        try:
            leitstand.setze_leiter_stufe(ticket, 5, nach)
        except (OSError, ValueError, leitstand.ZustandKaputt) as fehler:
            log.warning("leiter #%s: Stufe 5 nicht gemerkt: %s", ticket, fehler)
            text = f"Stufe 3 respawn gescheitert, Stufe 5 nicht gemerkt — {fehler}"
            return Ergebnis(
                EXIT_FEHLER, _zeile(ticket, _stufe3_nachziehen(ticket, nach, text))
            )
        text = f"Stufe 5 respawn gescheitert — {erg.zeile}"
        return Ergebnis(erg.exit, _zeile(ticket, text))
    try:
        leitstand.schliesse_leiter_respawn(ticket, jetzt)
    except (OSError, ValueError, leitstand.ZustandKaputt) as fehler:
        log.warning("leiter #%s: respawn-Abschluss nicht gemerkt: %s", ticket, fehler)
        text = f"Stufe 3 abgelöst, Abschluss nicht gemerkt — {fehler}"
        return Ergebnis(EXIT_FEHLER, _zeile(ticket, text))
    grund = "Start-Prompt-Datei da" if pc_fenster.kann_tippen(u.werkzeug()) else PC_VERMERK
    text = f"Stufe 0 {_WORT['respawn']} — {grund}"
    return Ergebnis(EXIT_OK, _zeile(ticket, text))


def _stufe3_nachziehen(ticket: int, nach: float, text: str) -> str:
    """Stufe 3 mit Zeit nach ``abloesen`` neu merken; gibt die (ggf. ergänzte) Zeile."""
    try:
        leitstand.setze_leiter_stufe(ticket, 3, nach)
    except (OSError, ValueError, leitstand.ZustandKaputt) as fehler:
        log.error(
            "leiter #%s: Stufe 3 nicht neu gemerkt — Folgelauf kann fälschlich "
            "abschließen: %s",
            ticket,
            fehler,
        )
        return f"{text}; Stufe 3 nicht neu gemerkt — {fehler}"
    return text


# --- Echte Umwelt ------------------------------------------------------------------------


def handoff_grenze(datei: Path = SMART_ZONE_DATEI) -> float:
    """``haupt.handoff_k`` aus der Smart-Zone-SSOT; fehlt/kaputt → Warnung + Vorgabe."""
    try:
        daten = json.loads(datei.expanduser().read_text(encoding="utf-8"))
        return float(daten["haupt"]["handoff_k"])
    except (OSError, ValueError, KeyError, TypeError) as fehler:
        log.warning(
            "Handoff-Grenze aus %s nicht lesbar (%s) — nehme %sk.",
            datei,
            fehler,
            HANDOFF_K_VORGABE,
        )
        return HANDOFF_K_VORGABE


class _EchteUmwelt:
    """Lage aus Aufseher-Stand (nur lesen) + Bau-Log, tippen über respawn-tmux."""

    def __init__(self, repo: Path, quellen: aufseher_stand.Quellen) -> None:
        self.repo = repo
        self.q = quellen
        self._werkzeug = respawn.werkzeug_fuer_rechner()

    def jetzt(self) -> float:
        return self.q.jetzt()

    def handoff_k(self) -> float:
        return handoff_grenze()

    def worktree(self, ticket: int) -> Path:
        return Path(config.worktree_pfad(ticket, self.repo)).expanduser()

    def werkzeug(self) -> respawn.Werkzeug | pc_fenster.PcWerkzeug:
        return self._werkzeug

    def lage(self, spec: int, ticket: int, seit: float | None) -> Lage:
        # Stand-Datei wird nur gelesen — schreiben tut sie der Aufseher-Tick.
        blick = aufseher_stand.ticket_lage(spec, ticket, self.q)
        if blick.lage is None:
            raise RuntimeError(f"#{ticket} ist kein Sub-Issue von Spec #{spec}")
        wt = self.worktree(ticket)
        prompt = seit is not None and respawn.start_prompt_da(wt, ticket, seit)
        start, unlesbar = self._letzter_start(ticket, wt)
        treffer = eigener_handoff.finde(wt, ticket, eigener_handoff.grenze(start, ticket))
        return Lage(
            offen=blick.lage.offen,
            fenster=blick.lage.fenster,
            still_min=blick.lage.still_min,
            kontext_k=self._kontext_k(ticket, wt),
            prompt_datei=prompt,
            ziel=blick.ziel,
            letzter_start=start,
            log_unlesbar=unlesbar,
            eigener_handoff=treffer,
        )

    def folge_ab_handoff(self, spec: int, ticket: int, treffer: eigener_handoff.Treffer) -> int:
        # Die Leiter hält ihre Sperre ``leiter-<N>`` schon selbst.
        return eigener_handoff.folge_starten(self.repo, spec, ticket, treffer, sperren=False)

    def _letzter_start(self, ticket: int, wt: Path) -> tuple[float | None, bool]:
        """Jüngster ``session_start`` im Bau-Log und ob das Lesen scheiterte (Warnung)."""
        try:
            ort = bau_log.log_ort(ticket, wt, self.repo)
            if ort is None:
                return None, False
            return bau_log.letzter_session_start(
                ort, ticket, hauptbaum=self.repo
            ), False
        except (OSError, ValueError, KeyError, TypeError) as fehler:
            log.warning("Bau-Log #%s unlesbar — Start unbekannt: %s", ticket, fehler)
            return None, True

    def _kontext_k(self, ticket: int, wt: Path) -> float | None:
        """Kontext der aktuellen Session — Zeilen vor dem letzten Leiter-Respawn zählen nicht."""
        try:
            ort = bau_log.log_ort(ticket, wt, self.repo)
            if ort is None:
                return None
            return bau_log.kontext_aktuell_k(
                ort, ticket, hauptbaum=self.repo, nach=leitstand.leiter_respawn(ticket)
            )
        except (OSError, ValueError, KeyError, TypeError) as fehler:
            log.warning("Bau-Log #%s unlesbar — Kontext unbekannt: %s", ticket, fehler)
            return None


def echte_umwelt(repo: Path, gh_repo: str) -> Umwelt:
    """Umwelt des Rechners; :class:`aufseher_stand.GhFehlt`, wenn ``gh`` fehlt."""
    return _EchteUmwelt(repo, aufseher_stand.echte_quellen(repo, gh_repo))


def cli(repo: Path, spec: int, ticket: int, gh_repo: str, dry_run: bool) -> Ergebnis:
    """CLI-Weg: GitHub-Repo aus ``origin``, falls nicht angegeben."""
    if not gh_repo:
        gh_repo = gh.repo_aus_origin(repo, fallback="")
    if not gh_repo:
        return Ergebnis(
            EXIT_FEHLER,
            _zeile(
                ticket, "Fehler — GitHub-Repo unbekannt, --gh-repo owner/name angeben"
            ),
        )
    return eingreifen(repo, spec, ticket, gh_repo=gh_repo, dry_run=dry_run)
