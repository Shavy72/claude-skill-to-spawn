"""Eingriffs-Leiter je Bau-Ticket (#432, Spec #399: E15, E16, E11).

Eine Tür: :func:`eingreifen`. Der Aufseher (über einen Ablöse-Subagenten) ruft sie
je Ticket auf; sie liest die Lage, entscheidet die nächste Stufe, führt sie aus und
gibt GENAU EINE Zeile zurück. Die Stufe merkt sie im Leitstand, damit kein Eingriff
doppelt passiert.

Stufen (feste Schwellen :data:`STUPS_MIN`, :data:`NACH_STUPS_MIN`):

0. nichts gemerkt — still ≥ 20 min → Mindset-Stupser tippen (Stufe 1).
1. angestupst — weitere 15 min still → Handoff anfordern (Stufe 2).
   Handoff-Grenze (Spitzen-Kontext ≥ ``haupt.handoff_k`` der Smart-Zone-SSOT) →
   sofort Stufe 2, auch aus Stufe 0.
2. Handoff angefordert — sobald die Start-Prompt-Datei frisch da ist → Stufe 3 =
   Skill ``respawn`` (:func:`respawn.abloesen`), nie ein eigener Startweg; Erfolg →
   Stufe 0.
4. Ticket zu und Fenster still → ``/exit`` tippen, genau einmal.

In ein Fenster, das arbeitet oder eine Rückfrage zeigt, wird nie getippt. Arbeitet
die Session nach einem Stupser wieder, fällt die Leiter auf Stufe 0 zurück.

:func:`entscheide` ist reine Logik (keine Außenwelt). Alle Außenwelt-Zugriffe laufen
über das Protokoll :class:`Umwelt`; echt ist :func:`echte_umwelt`, Tests geben ein
Fake hinein. Exit-Codes: 0 ok (Eingriff oder nichts nötig) · 1 Fehler (tmux/gh/
Leitstand) · bei Stufe 3 der Exit-Code von ``respawn``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from to_spawn import aufseher_stand, bau_log, config, gh, leitstand, respawn

log = logging.getLogger(__name__)

#: Minuten still, bevor Stufe 0 anstupst.
STUPS_MIN = 20
#: Minuten nach dem Stupser (und still), bevor der Handoff angefordert wird.
NACH_STUPS_MIN = 15
#: Rückfall der Handoff-Grenze in k-Token (SSOT-Stand 2026-10-04: haupt.handoff_k).
HANDOFF_K_VORGABE = 250.0
SMART_ZONE_DATEI = Path("~/.claude/smart-zone.json")

EXIT_OK = 0
EXIT_FEHLER = 1

#: Stufe 1 (E11 Richtung 2): Mut machen, Session arbeitet selbst weiter.
MINDSET_TEXT = (
    "Aufseher: Du stehst seit {min} min still. Wir bauen nur in Staging — dort kann "
    "nichts live schaden. Entscheide offene Punkte selbst (Vorschlag nehmen, ins "
    "Bau-Log), bau das Ticket fertig, bleib in der Smart Zone. Setz deinen Loop genau "
    "dort fort, wo du warst."
)
EXIT_TEXT = "/exit"

Aktion = Literal["nichts", "anstupsen", "handoff", "respawn", "exit", "zuruecksetzen"]


@dataclass(frozen=True)
class Lage:
    """Was die Leiter über ein Ticket weiß (``None`` = unbekannt)."""

    offen: bool | None  # False = Ticket zu
    fenster: str  # aufseher_stand.ARBEITET | STILL | RUECKFRAGE | KEIN_FENSTER | ...
    still_min: int | None
    kontext_k: float | None  # Spitzen-Kontext aus dem Bau-Log
    prompt_datei: bool  # frische START_*_<N>.txt seit dem letzten Eingriff
    ziel: str | None = None  # tmux-Ziel des Fensters ``bau <N>``


@dataclass(frozen=True)
class Gemerkt:
    """Aus dem Leitstand: 0 nichts, 1 angestupst, 2 Handoff angefordert, 4 /exit."""

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
    def werkzeug(self) -> respawn.Werkzeug:
        """tmux-Werkzeug von respawn: ``tippen`` = Text einfügen, Enter getrennt."""
        ...


def entscheide(lage: Lage, gemerkt: Gemerkt, jetzt: float, handoff_k: float) -> Schritt:
    """Nächster Schritt der Leiter — reine Logik, Regeln im Modul-Kommentar."""
    stufe = gemerkt.stufe
    if lage.fenster == aufseher_stand.ARBEITET:
        if stufe == 1:
            return Schritt("zuruecksetzen", "arbeitet wieder nach Stupser")
        return Schritt("nichts", "arbeitet")
    if lage.fenster == aufseher_stand.RUECKFRAGE:
        return Schritt("nichts", "Rückfrage offen — Aufseher antwortet")
    if lage.fenster != aufseher_stand.STILL:
        return Schritt("nichts", f"Fenster: {lage.fenster}")
    still = lage.still_min or 0
    if lage.offen is False:
        if stufe == 4:
            return Schritt("nichts", "Ticket zu, /exit schon getippt")
        return Schritt("exit", f"Ticket zu, still {still} min")
    if stufe == 4:
        return Schritt("zuruecksetzen", "Ticket wieder offen")
    if stufe == 2:
        if lage.prompt_datei:
            return Schritt("respawn", "Start-Prompt-Datei da")
        warte = _minuten(jetzt, gemerkt.seit)
        return Schritt("nichts", f"wartet auf Start-Prompt-Datei seit {warte} min")
    if lage.kontext_k is not None and lage.kontext_k >= handoff_k:
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


def _minuten(jetzt: float, seit: float | None) -> int:
    return 0 if seit is None else max(0, int((jetzt - seit) // 60))


# --- Tür -------------------------------------------------------------------------------


_WORT: dict[str, str] = {
    "nichts": "nichts",
    "anstupsen": "angestupst",
    "handoff": "Handoff angefordert",
    "respawn": "abgelöst (respawn)",
    "exit": "/exit getippt",
    "zuruecksetzen": "zurückgesetzt",
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
    return gemerkt, lage, entscheide(lage, gemerkt, jetzt, u.handoff_k()), jetzt


def _plane(u: Umwelt, spec: int, ticket: int) -> Ergebnis:
    gemerkt, _, schritt, _ = _lies(u, spec, ticket)
    text = f"Stufe {gemerkt.stufe} {schritt.aktion} geplant (dry-run) — {schritt.grund}"
    return Ergebnis(EXIT_OK, _zeile(ticket, text))


def _schritt(u: Umwelt, repo: Path, spec: int, ticket: int) -> Ergebnis:
    gemerkt, lage, schritt, jetzt = _lies(u, spec, ticket)
    neu: tuple[int, float | None] | None = None
    if schritt.aktion == "respawn":
        erg = respawn.abloesen(repo, spec, ticket, werkzeug=u.werkzeug())
        if erg.exit != respawn.EXIT_OK:
            text = f"Stufe 2 respawn gescheitert — {erg.zeile}"
            return Ergebnis(erg.exit, _zeile(ticket, text))
        neu = (0, None)
    elif schritt.aktion in ("anstupsen", "handoff", "exit"):
        if not lage.ziel:
            raise RuntimeError(f"tmux-Ziel von bau {ticket} unbekannt")
        if schritt.aktion == "anstupsen":
            text = MINDSET_TEXT.format(min=lage.still_min or 0)
            neu = (1, jetzt)
        elif schritt.aktion == "handoff":
            text = respawn.handoff_auftrag_fuer(u.worktree(ticket), ticket, jetzt)
            neu = (2, jetzt)
        else:
            text = EXIT_TEXT
            neu = (4, jetzt)
        u.werkzeug().tippen(lage.ziel, text)
    elif schritt.aktion == "zuruecksetzen":
        neu = (0, None)
    stufe = gemerkt.stufe
    if neu is not None:
        leitstand.setze_leiter_stufe(ticket, neu[0], neu[1])
        stufe = neu[0]
    text = f"Stufe {stufe} {_WORT[schritt.aktion]} — {schritt.grund}"
    log.info("leiter #%s: %s → %s (%s)", ticket, gemerkt, schritt.aktion, schritt.grund)
    return Ergebnis(EXIT_OK, _zeile(ticket, text))


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
        self._werkzeug = respawn.TmuxWerkzeug()

    def jetzt(self) -> float:
        return self.q.jetzt()

    def handoff_k(self) -> float:
        return handoff_grenze()

    def worktree(self, ticket: int) -> Path:
        return Path(config.worktree_pfad(ticket, self.repo)).expanduser()

    def werkzeug(self) -> respawn.Werkzeug:
        return self._werkzeug

    def lage(self, spec: int, ticket: int, seit: float | None) -> Lage:
        # Stand-Datei wird nur gelesen — schreiben tut sie der Aufseher-Tick.
        vorher = aufseher_stand._vorher(spec, None)
        erg = aufseher_stand.sammeln(spec, self.q, vorher)
        tl = next((t for t in erg.lagen if t.nummer == ticket), None)
        if tl is None:
            raise RuntimeError(f"#{ticket} ist kein Sub-Issue von Spec #{spec}")
        fenster = aufseher_stand._fenster_liste(spec, self.q) or {}
        ziel = fenster[ticket][0] if ticket in fenster else None
        wt = self.worktree(ticket)
        prompt = seit is not None and respawn.start_prompt_da(wt, ticket, seit)
        return Lage(
            tl.offen,
            tl.fenster,
            tl.still_min,
            self._kontext_k(ticket, wt),
            prompt,
            ziel,
        )

    def _kontext_k(self, ticket: int, wt: Path) -> float | None:
        try:
            ort = bau_log.log_ort(ticket, wt, self.repo)
            if ort is None:
                return None
            wert = bau_log.zusammenfassung(ort, ticket, hauptbaum=self.repo)["spitze_k"]
        except (OSError, ValueError, KeyError) as fehler:
            log.warning("Bau-Log #%s unlesbar: %s", ticket, fehler)
            return None
        return None if wert is None else float(wert)


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
