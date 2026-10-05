"""Neustart ab eigenem Handoff (#542, Variante A).

Schreibt eine Bau-Session an ihrer Grenze selbst einen Handoff, committet ihn und
bleibt dann still stehen, startet die Folge-Session automatisch — ohne dass der
Aufseher Befehl D von Hand tippt. Dieses Modul ist die EINZIGE Stelle der Regel;
Leiter (:mod:`to_spawn.leiter`) und Aufpasser (:mod:`to_spawn.aufpasser`) fragen nur
:func:`pruefe` / :func:`finde` und rufen :func:`folge_starten`.

Regel: Ein eigener Handoff zählt, wenn

* ``docs/handoffs/HANDOFF_<datum>_<N>.md`` im Ticket-Worktree getrackt ist,
* die Datei keine uncommitteten Änderungen hat (``git status --porcelain`` leer),
* ihr letzter Commit jünger ist als der jüngste ``session_start`` im Bau-Log UND
  jünger als der letzte Leiter-Respawn/Neustart (sonst gehört er zur Vorsession oder
  wurde schon benutzt),
* die Leiter das Ticket nicht selbst gerade ablöst (Leiter-Stufe 0 oder 1).

Fällig ist er, sobald die Session seit dem Handoff-Commit :data:`STILL_MIN` Minuten
nicht arbeitet — die Zeit seit dem Commit zählt als Stille, der lange
Bildschirm-Vergleich wird nicht abgewartet.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from to_spawn import bau_log, config, leitstand

log = logging.getLogger(__name__)

#: Minuten seit dem Handoff-Commit, ab denen eine nicht arbeitende Session neu startet.
STILL_MIN = 2
#: Leiter-Stufen, in denen der Neustart ab eigenem Handoff greifen darf.
STUFEN = (0, 1)
#: Rückgabe von :func:`folge_starten`, wenn die Sperre belegt oder nichts mehr fällig ist:
#: es wurde NICHTS gestartet. Bewusst weder 0 (= gestartet) noch ein Fehlercode von
#: ``spawn.neustart`` (1-3 …) — Aufrufer müssen es getrennt behandeln.
EXIT_NICHTS = 10
ORDNER = "docs/handoffs"


@dataclass(frozen=True)
class Treffer:
    """Ein committeter eigener Handoff: Pfad relativ zum Worktree + Commit-Zeit."""

    datei: str
    commit_zeit: float

    def still_min(self, jetzt: float) -> int:
        return max(0, int((jetzt - self.commit_zeit) // 60))

    def faellig(self, jetzt: float) -> bool:
        return self.still_min(jetzt) >= STILL_MIN


def _git(wt: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(wt), *args], capture_output=True, text=True, check=True
    ).stdout


def finde(wt: Path, ticket: int, letzter_start: float | None) -> Treffer | None:
    """Jüngster committeter, sauberer Handoff von ``ticket``, jünger als ``letzter_start``.

    ``letzter_start`` = Grenze (siehe :func:`grenze`); ``None`` → nie ein Treffer
    (ohne belegten Start ist unklar, zu welcher Session der Handoff gehört).
    """
    if letzter_start is None or not (wt / ".git").exists():
        return None
    name = re.compile(rf"{re.escape(ORDNER)}/HANDOFF_[^/]+_{int(ticket)}\.md")
    try:
        dateien = _git(
            wt, "ls-files", "--", f"{ORDNER}/HANDOFF_*_{int(ticket)}.md"
        ).split("\n")
        beste: Treffer | None = None
        for datei in (d for d in dateien if name.fullmatch(d)):
            if _git(wt, "status", "--porcelain", "--", datei).strip():
                continue  # Änderungen nicht committet — Session schreibt noch
            roh = _git(wt, "log", "-1", "--format=%ct", "--", datei).strip()
            if not roh or float(roh) <= letzter_start:
                continue
            if beste is None or float(roh) > beste.commit_zeit:
                beste = Treffer(datei, float(roh))
        return beste
    except (OSError, ValueError, subprocess.CalledProcessError) as fehler:
        log.warning("#%s: eigener Handoff nicht prüfbar (%s)", ticket, fehler)
        return None


def grenze(letzter_start: float | None, ticket: int) -> float | None:
    """Ab wann ein Handoff neu ist: jüngster ``session_start``, mindestens der letzte
    Leiter-Respawn/Neustart. ``None`` ohne belegten Start."""
    if letzter_start is None:
        return None
    return max(letzter_start, leitstand.leiter_respawn(ticket) or 0.0)


def pruefe(repo: Path, ticket: int, jetzt: float) -> Treffer | None:
    """Fälliger eigener Handoff von ``ticket`` (Worktree + Bau-Log wie die Leiter), sonst ``None``."""
    if leitstand.leiter_stufe(ticket) not in STUFEN:
        return None
    wt = Path(config.worktree_pfad(ticket, repo)).expanduser()
    try:
        ort = bau_log.log_ort(ticket, wt, repo)
        start = (
            None
            if ort is None
            else bau_log.letzter_session_start(ort, ticket, hauptbaum=repo)
        )
    except (OSError, ValueError, KeyError, TypeError) as fehler:
        log.warning(
            "#%s: Bau-Log unlesbar — kein Neustart ab Handoff (%s)", ticket, fehler
        )
        return None
    treffer = finde(wt, ticket, grenze(start, ticket))
    return treffer if treffer is not None and treffer.faellig(jetzt) else None


def folge_starten(
    repo: Path, spec: int, ticket: int, treffer: Treffer, *, sperren: bool
) -> int:
    """Folge-Session ab ``treffer`` starten (alte Session beenden) und merken.

    ``sperren=True`` (Aufpasser): nimmt die Leiter-Sperre ``leiter-<N>`` und prüft darin
    erneut — so starten Leiter und Aufpasser nie gleichzeitig neu. Die Leiter hält die
    Sperre selbst und ruft mit ``sperren=False``. Rückgabe: Exit-Code von
    ``spawn.neustart`` bzw. :data:`EXIT_NICHTS`, wenn nichts (mehr) zu tun war.
    """
    if not sperren:
        return _folge_starten(repo, spec, ticket, treffer)
    halter = leitstand.versuche(f"leiter-{ticket}", f"handoff-{os.getpid()}")
    if halter is None:
        log.info(
            "#%s: Leiter-Sperre belegt — Neustart ab Handoff im nächsten Takt", ticket
        )
        return EXIT_NICHTS
    with halter:
        if pruefe(repo, ticket, time.time()) != treffer:
            log.info(
                "#%s: Handoff %s nicht mehr fällig — nichts getan",
                ticket,
                treffer.datei,
            )
            return EXIT_NICHTS
        return _folge_starten(repo, spec, ticket, treffer)


def _folge_starten(repo: Path, spec: int, ticket: int, treffer: Treffer) -> int:
    from to_spawn import spawn  # spät: spawn zieht capo/manifest nach

    vorher = os.environ.get("TO_SPAWN_REPO")
    try:
        code = spawn.neustart(
            repo, spec, ticket, config.lade(repo), handoff=treffer.datei, beenden=True
        )
    finally:  # neustart setzt TO_SPAWN_REPO — der Aufrufer betreut evtl. mehrere Repos
        if vorher is None:
            os.environ.pop("TO_SPAWN_REPO", None)
        else:
            os.environ["TO_SPAWN_REPO"] = vorher
    if code == 0:
        # Stufe 0 + Neustart-Zeit in einem Schreibvorgang: derselbe Handoff zählt nie zweimal.
        leitstand.schliesse_leiter_respawn(ticket, time.time())
    return code
