"""Wiederanlauf: in welcher Lage ist ein Ticket, wenn es (erneut) gestartet wird? (#282 P1)

Eine Frage, eine Antwort für alle Startwege (Erststart, Ablösung, Folge-Runde)::

    lage = wiederanlauf.feststellen(7, repo)   # None = Stand nicht bestimmbar → nichts starten
    lage.art      # "neu" | "fortsetzen" | "abschluss" | "nichts"
    lage.fehlt    # z. B. ("Klickweg-Beleg",) — nur bei "abschluss" nicht leer
    lage.hinweis  # fertiger Textblock für den Start der Session

Regel (:func:`entscheide`, rein): Ticket zu/verworfen → ``nichts``; fertig gebaut und
nichts fehlt → ``nichts``; fertig gebaut und es fehlt etwas → ``abschluss``; nicht fertig
gebaut → ``fortsetzen``, wenn ein Zweig ``ticket/<N>-*`` (auf origin oder lokal) oder ein Commit mit
``(#<N>)`` existiert, sonst ``neu``. „Fertig gebaut?“ beantwortet nur
:func:`ticket_zustand.fertig_gebaut`, „zu?“ nur :func:`ticket_zustand.endgueltig_zu`.

Was fehlt, sagen die Belegseiten (:mod:`to_spawn.belege`): kein Klickweg → „Klickweg-Beleg“
(ausdrücklich „Klickweg: entfällt“ zählt als erfüllt), kein grüner Lauf → „grüner Testlauf“.
Gelesen wird der Beleg-Ordner bevorzugt im Ticket-Worktree ``wt-<N>`` (desselben Repos),
sonst im Arbeitsbaum ``repo``. Zweige und Commits liefert :mod:`to_spawn.gh`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from . import belege, capo, config, gh, ticket_zustand

log = logging.getLogger("wiederanlauf")

Art = Literal["neu", "fortsetzen", "abschluss", "nichts"]

KLICKWEG_BELEG = "Klickweg-Beleg"
GRUENER_LAUF = "grüner Testlauf"


@dataclass(frozen=True)
class Wiederanlauf:
    """Lage eines Tickets beim Start: Art, fehlende Abschluss-Schritte, Hinweis-Text."""

    art: Art
    fehlt: tuple[str, ...]
    hinweis: str

    def zeile(self) -> str:
        """Eine Zeile für die Lese-Tür: Art, bei ``abschluss`` dazu was fehlt."""
        return f"{self.art} — fehlt: {', '.join(self.fehlt)}" if self.fehlt else self.art


@dataclass(frozen=True)
class Signale:
    """Alles, was :func:`entscheide` braucht — von :func:`feststellen` gesammelt."""

    nummer: int
    ticket: Mapping[str, Any]
    zweige: tuple[str, ...]
    commits: int
    assignees: tuple[str, ...]
    fehlt: tuple[str, ...]


def entscheide(signale: Signale) -> Wiederanlauf:
    """Reine Regel (s. Modul-Doku): aus den Signalen die Lage samt Hinweis-Text."""
    n = signale.nummer
    if ticket_zustand.endgueltig_zu(signale.ticket):
        return Wiederanlauf("nichts", (), f"#{n}: nichts zu tun ({ticket_zustand.wortlaut(signale.ticket)}).")
    if ticket_zustand.fertig_gebaut(signale.ticket):
        if not signale.fehlt:
            return Wiederanlauf("nichts", (), f"#{n}: nichts zu tun (fertig gebaut, alle Belege da).")
        offen = ", ".join(signale.fehlt)
        return Wiederanlauf(
            "abschluss",
            signale.fehlt,
            f"Stand #{n}: fertig gebaut, nicht neu bauen. Es fehlen nur noch Abschluss-Schritte: {offen}. "
            "Nur diese nachholen und belegen.",
        )
    claim = f" Vergeben an: {', '.join(signale.assignees)}." if signale.assignees else ""
    if signale.zweige or signale.commits:
        zweige = ", ".join(signale.zweige) if signale.zweige else "keiner"
        return Wiederanlauf(
            "fortsetzen",
            (),
            f"Stand #{n}: es gibt schon Arbeit (Zweig: {zweige}; Commits mit (#{n}): {signale.commits}).{claim} "
            "Nicht von vorn beginnen: vorhandenen Stand lesen und fortsetzen.",
        )
    return Wiederanlauf("neu", (), f"Stand #{n}: noch keine Spur (kein Zweig, kein Commit).{claim} Von vorn beginnen.")


def feststellen(ticket: int, repo: Path, *, daten: Mapping[str, Any] | None = None) -> Wiederanlauf | None:
    """Lage von ``ticket`` aus Issue (gh), git (Zweige auf origin + lokal, Commits) und Belegseiten.

    ``daten``: schon gelesenes Issue-JSON (erspart eine doppelte ``gh``-Abfrage in der Startstelle).
    ``None``, wenn Issue oder git nicht lesbar sind — der Aufrufer startet dann nichts
    (fail-closed, wie :func:`ticket_zustand.lesen`).
    """
    if daten is None:
        daten = ticket_zustand.lesen(ticket, repo, mit_claim=True)
    if daten is None:
        log.warning("Ticket #%s: Issue-Stand nicht lesbar", ticket)
        return None
    arbeitsbaum = _ticket_arbeitsbaum(repo, ticket)
    zweige = _zweige(repo, ticket, arbeitsbaum)
    commits = _commits(repo, ticket)
    if zweige is None or commits is None:
        return None
    beleg = _belege_auswerten(repo, ticket, arbeitsbaum)
    fehlt = tuple(
        name
        for name, da in (
            (KLICKWEG_BELEG, beleg.klickweg or beleg.klickweg_entfaellt),
            (beleg.hinweis or GRUENER_LAUF, beleg.status == "gruen"),
        )
        if not da
    )
    assignees = tuple(str(a.get("login", "")) for a in daten.get("assignees") or () if isinstance(a, Mapping))
    return entscheide(Signale(ticket, daten, zweige, commits, tuple(a for a in assignees if a), fehlt))


def _ticket_arbeitsbaum(repo: Path, ticket: int) -> Path | None:
    """Ticket-Worktree ``wt-<N>`` desselben Repos, falls er existiert und nicht schon ``repo`` ist."""
    wt = Path(config.worktree_pfad(ticket, repo)).expanduser()
    if not wt.is_dir() or not (wt / ".git").exists():
        return None
    try:
        if wt.resolve() == repo.resolve():
            return None
    except OSError:
        return None
    kennung_repo = config.repo_kennung(repo)
    if kennung_repo is not None and config.repo_kennung(wt) != kennung_repo:
        return None
    return wt


def _belege_auswerten(repo: Path, ticket: int, arbeitsbaum: Path | None = None) -> belege.Belege:
    """Belege bevorzugt aus dem Ticket-Worktree (falls vorhanden und mit Belegen), sonst aus ``repo``."""
    ordner = capo.belege_ordner(config.lade(repo))
    if arbeitsbaum is not None:
        wt_beleg = belege.auswerten(arbeitsbaum / ordner, str(ticket))
        if wt_beleg.dateien:
            return wt_beleg
    return belege.auswerten(repo / ordner, str(ticket))


def _zweige(repo: Path, ticket: int, arbeitsbaum: Path | None = None) -> tuple[str, ...] | None:
    """Zweige ``ticket/<N>-*`` auf origin und lokal; ``None`` = origin/git fehlt oder unerreichbar."""
    muster = f"ticket/{ticket}-*"
    fern = gh.origin_zweige(repo, muster)
    if fern is None:
        return None
    lokal = gh.lokale_zweige(repo, muster)
    if lokal is None:
        return None
    alle = set(fern) | set(lokal)
    if arbeitsbaum is not None:
        wt_lokal = gh.lokale_zweige(arbeitsbaum, muster)
        if wt_lokal is not None:
            alle.update(wt_lokal)
    return tuple(sorted(n for n in alle if n.startswith(f"ticket/{ticket}-")))


def _commits(repo: Path, ticket: int) -> int | None:
    """Commits aller lokalen und Remote-Zweige, deren Betreff die Nummer trägt; ``None`` = git gescheitert."""
    betreffe = gh.commit_betreffe(repo, ticket, "--all")
    if betreffe is None:
        return None
    return sum(1 for betreff in betreffe if capo.betreff_hat_nummer(betreff, ticket))
