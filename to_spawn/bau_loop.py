"""Staffel-Schleife: eine Bau-Session starten und bei Handoff fortsetzen.

Die Schleife gehört dem Skill — ``scripts/bau.py`` im Projekt-Repo bleibt
unangetastet. Ablauf je Ticket:

1. Session starten (Log-Zeile ``session_start``).
2. Bei ``staffel.modus == "eltern"`` die Marker-Datei ``.to-spawn/stop-<N>``
   überwachen; taucht sie auf, beendet der Elternprozess das Kind. (Ein Stop-Hook
   kann den Claude-Prozess laut Doku nicht selbst beenden.)
3. Nach dem Ende: Ticket noch offen **und** frische Handoff-Datei
   ``docs/handoffs/HANDOFF_*_<N>.md`` → Staffel + 1, Neustart mit dem Handoff als
   Startkontext im Prompt. Maximal ``staffel.max_staffeln`` Läufe.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Sequence

from . import bau_log, config, gh, hooks

log = logging.getLogger("to_spawn.bau_loop")

#: Zeichen-Obergrenze für den Handoff-Text im Folge-Prompt.
HANDOFF_MAX = 20000


def frischer_handoff(repo: Path, ticket: str | int, seit: float) -> Path | None:
    """Jüngste Handoff-Datei des Tickets, die nach ``seit`` (Epoch) entstand."""
    ordner = repo / "docs" / "handoffs"
    if not ordner.is_dir():
        return None
    kandidaten = [
        pfad for pfad in ordner.glob(f"HANDOFF_*_{ticket}.md") if pfad.stat().st_mtime >= seit
    ]
    if not kandidaten:
        return None
    return max(kandidaten, key=lambda p: p.stat().st_mtime)


def _marker_waechter(
    marker: Path, prozess: subprocess.Popen, stopp: threading.Event, takt: float
) -> None:
    """Beendet das Kind, sobald die Marker-Datei auftaucht (Weg ``eltern``)."""
    while not stopp.wait(takt):
        if not marker.exists():
            continue
        if prozess.poll() is None:
            log.info("Marker %s gesehen — Session wird beendet (Staffel).", marker.name)
            prozess.terminate()
        return


def _prompt_mit_handoff(cmd: Sequence[str], handoff: Path) -> list[str]:
    """Handoff-Inhalt als Startkontext an den Prompt (letztes Argument) hängen."""
    text = handoff.read_text(encoding="utf-8", errors="replace")[:HANDOFF_MAX]
    neu = list(cmd)
    neu[-1] = (
        f"{neu[-1]}\n\n--- Startkontext aus dem Handoff der Vorgänger-Session "
        f"({handoff.name}) ---\n{text}\n--- Ende Startkontext ---"
    )
    return neu


def _hat_handoff_zeile(repo: Path, ticket: str | int, staffel: int) -> bool:
    return any(
        zeile.get("typ") == "handoff" and int(zeile.get("staffel") or 1) == staffel
        for zeile in bau_log.lese(repo, ticket)
    )


def run_ticket(
    ticket: str | int,
    cmd: Sequence[str],
    repo: Path | None = None,
    konfig: dict[str, Any] | None = None,
    *,
    max_staffeln: int | None = None,
    takt: float = 1.0,
    umgebung: dict[str, str] | None = None,
) -> int:
    """Ticket-Session mit Staffel-Fortsetzung fahren; gibt den letzten Exit-Code zurück.

    ``cmd`` ist der vollständige Aufruf; das **letzte** Element gilt als Prompt und
    wird bei einer Fortsetzung um den Handoff-Text ergänzt.
    """
    ticket = str(ticket)
    wurzel = config.repo_wurzel(repo)
    einstellungen = konfig if konfig is not None else config.lade(wurzel)
    modus = config.staffel_modus(einstellungen)
    grenze = int(
        max_staffeln
        if max_staffeln is not None
        else einstellungen.get("staffel", {}).get("max_staffeln", 3)
    )
    marker = hooks.marker_pfad(wurzel, ticket)
    aktuell = list(cmd)
    code = 0

    for staffel in range(1, grenze + 1):
        marker.unlink(missing_ok=True)
        start = time.time()
        basis = dict(os.environ)
        basis.update(
            {
                "TO_SPAWN_TICKET": ticket,
                "TO_SPAWN_STAFFEL": str(staffel),
                "TO_SPAWN_START": str(start),
                "TO_SPAWN_STAFFEL_MODUS": modus,
            }
        )
        if umgebung:
            basis.update(umgebung)

        bau_log.schreibe(
            wurzel,
            ticket,
            "session_start",
            staffel=staffel,
            modell=einstellungen.get("modelle", {}).get("ticket"),
            effort=einstellungen.get("effort", {}).get("ticket"),
            runner=einstellungen.get("runner"),
            text=f"Session {staffel}/{grenze} gestartet.",
        )

        prozess = subprocess.Popen(aktuell, cwd=str(wurzel), env=basis)
        stopp = threading.Event()
        waechter: threading.Thread | None = None
        if modus == "eltern":
            waechter = threading.Thread(
                target=_marker_waechter,
                args=(marker, prozess, stopp, takt),
                daemon=True,
            )
            waechter.start()
        try:
            code = prozess.wait()
        finally:
            stopp.set()
            if waechter is not None:
                waechter.join(timeout=takt * 3)

        handoff = frischer_handoff(wurzel, ticket, start)
        offen = gh.ticket_offen(ticket, cwd=wurzel)
        if handoff is None or offen is False:
            marker.unlink(missing_ok=True)
            return code

        if not _hat_handoff_zeile(wurzel, ticket, staffel):
            bau_log.schreibe(
                wurzel,
                ticket,
                "handoff",
                staffel=staffel,
                text=f"Handoff geschrieben: {handoff.name} — Folge-Session übernimmt.",
            )
        if staffel == grenze:
            bau_log.schreibe(
                wurzel,
                ticket,
                "staffel_limit",
                staffel=staffel,
                text=f"Staffel-Grenze {grenze} erreicht — Ticket bleibt offen, Mensch nötig.",
            )
            marker.unlink(missing_ok=True)
            return code
        aktuell = _prompt_mit_handoff(cmd, handoff)
        marker.unlink(missing_ok=True)
    return code
