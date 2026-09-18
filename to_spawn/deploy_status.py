"""Deploy-Statusdatei lesen und ins Bau-Log übernehmen (duoplus-management#207).

Das Deploy-Skript des Repos schreibt je Phasenwechsel eine JSON-Zeile in
``.deploy_status.jsonl`` (Felder ``ts``, ``lauf``, ``phase``, ``sek``, ``marker``,
am Ende zusätzlich ``ergebnis``, ``exit``, ``grund``). Eine wartende Session ruft
alle paar Minuten ``to_spawn.py deploy-status`` — das liest NUR diese Datei, nie
die Gate-Ausgabe, und hängt jede neue Phase als ``deploy_phase`` ans Bau-Log.

Exit-Codes: 0 = Ende grün · 1 = Ende rot (auch: Gate-Prozess laut PID tot, ohne
Ende-Zeile) · 3 = läuft noch · 4 = läuft laut Datei, aber seit mehr als
``still_min`` Minuten keine neue Phase oder Zeitstempel unlesbar · 2 = keine,
leere oder unlesbare Statusdatei.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import bau_log, config

log = logging.getLogger("to_spawn.deploy_status")

DATEI_NAME = ".deploy_status.jsonl"
#: 60 statt 40: ``--serial`` braucht 23–52 min in einer Phase; tote Prozesse
#: erkennt die PID-Prüfung ohnehin sofort.
STILL_MIN_VORGABE = 60

EXIT_GRUEN = 0
EXIT_ROT = 1
EXIT_KEINE_DATEI = 2
EXIT_LAEUFT = 3
EXIT_STILL = 4

#: Felder einer Statuszeile, die ins Bau-Log wandern (``ts`` setzt das Bau-Log selbst).
_FELDER = ("lauf", "phase", "sek", "marker", "ergebnis", "grund", "exit", "sha")


def status_pfad(repo: Path, datei: str | None = None) -> Path:
    """``--datei`` gewinnt, dann ``DEPLOY_STATUS_DATEI``, sonst ``<repo>/.deploy_status.jsonl``."""
    wahl = datei or os.environ.get("DEPLOY_STATUS_DATEI")
    return Path(wahl) if wahl else repo / DATEI_NAME


def lese_status(pfad: Path) -> list[dict[str, Any]]:
    """Alle gültigen Zeilen des jüngsten Laufs (kaputte Zeilen: Warnung, übersprungen)."""
    try:
        text = pfad.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    except OSError as fehler:
        log.warning("Statusdatei unlesbar (%s): %s", pfad, fehler)
        return []
    zeilen: list[dict[str, Any]] = []
    for nummer, roh in enumerate(text.splitlines(), start=1):
        roh = roh.strip()
        if not roh:
            continue
        try:
            eintrag = json.loads(roh)
        except ValueError:
            log.warning("Kaputte Zeile %d in %s übersprungen.", nummer, pfad)
            continue
        if (
            not isinstance(eintrag, dict)
            or not eintrag.get("phase")
            or not eintrag.get("lauf")
        ):
            log.warning("Zeile %d in %s ohne phase/lauf übersprungen.", nummer, pfad)
            continue
        zeilen.append(eintrag)
    if not zeilen:
        return []
    # Das Skript legt die Datei je Lauf neu an; stehen trotzdem mehrere Läufe
    # drin, zählt nur der jüngste (der der letzten Zeile).
    lauf = zeilen[-1]["lauf"]
    return [z for z in zeilen if z.get("lauf") == lauf]


def _schluessel(eintrag: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(eintrag.get("lauf")),
        str(eintrag.get("phase")),
        str(eintrag.get("sek")),
    )


def uebernimm(repo: Path, ticket: str, zeilen: list[dict[str, Any]]) -> int:
    """Neue Phasen als ``deploy_phase`` ins Bau-Log; Rückgabe = Zahl neuer Zeilen."""
    schon_da = {
        _schluessel(z)
        for z in bau_log.lese(repo, ticket)
        if z.get("typ") == "deploy_phase"
    }
    neu = 0
    for zeile in zeilen:
        schluessel = _schluessel(zeile)
        if schluessel in schon_da:
            continue
        felder = {name: zeile.get(name) for name in _FELDER}
        bau_log.schreibe(repo, ticket, "deploy_phase", **felder)
        schon_da.add(schluessel)
        neu += 1
    return neu


def _zeitpunkt(text: Any) -> datetime | None:
    try:
        wert = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)


def _dauer(sekunden: float) -> str:
    sekunden = max(int(sekunden), 0)
    return f"{sekunden} s" if sekunden < 120 else f"{sekunden // 60} min"


def _einzeilig(text: Any) -> str:
    return " ".join(str(text or "").split())


def prozess_lebt(pid: Any) -> bool | None:
    """True = lebt, False = sicher tot, None = unbekannt (keine PID, Windows, Fehler).

    Unter Windows gibt es keine Prüfung: ``os.kill(pid, 0)`` würde dort den
    Prozess beenden, und die PID aus Git-Bash ist keine Windows-PID.
    """
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or sys.platform == "win32"
    ):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as fehler:
        log.warning("PID %s nicht prüfbar: %s", pid, fehler)
        return None
    return True


def bericht(
    zeilen: list[dict[str, Any]], still_min: float, jetzt: datetime
) -> tuple[str, int]:
    """Kurzer Text (≤ 3 Zeilen) und Exit-Code für die Zeilen eines Laufs."""
    letzte = zeilen[-1]
    lauf = letzte.get("lauf")
    if letzte.get("phase") == "ende":
        vorher = zeilen[-2]["phase"] if len(zeilen) > 1 else "start"
        gesamt = _dauer(float(letzte.get("sek") or 0))
        if letzte.get("ergebnis") == "gruen":
            text = f"Deploy-Lauf {lauf}: grün nach {gesamt} — {_einzeilig(letzte.get('grund'))}"
            return text, EXIT_GRUEN
        text = (
            f"Deploy-Lauf {lauf}: rot: {_einzeilig(letzte.get('grund')) or 'ohne Grund'}\n"
            f"Exit {letzte.get('exit', '?')} in Phase {vorher} nach {gesamt}"
        )
        return text, EXIT_ROT

    pid = next((z.get("pid") for z in zeilen if z.get("pid") is not None), None)
    if prozess_lebt(pid) is False:
        text = (
            f"Deploy-Lauf {lauf}: rot: Gate-Prozess {pid} ohne Ende-Zeile beendet "
            "(SIGKILL/Speicher?)\n"
            f"Letzte Phase {letzte.get('phase')}: {_einzeilig(letzte.get('marker'))}"
        )
        return text, EXIT_ROT

    seit = _zeitpunkt(letzte.get("ts"))
    if seit is None:
        text = (
            f"Deploy-Lauf {lauf}: Phase {letzte.get('phase')}, Zeitstempel unlesbar "
            f"({letzte.get('ts')!r})\n"
            "Zustand unklar — Gate hängt oder ist gestorben, Prozess prüfen"
        )
        return text, EXIT_STILL
    still_s = (jetzt - seit).total_seconds()
    zeilen_text = [
        f"Deploy-Lauf {lauf} läuft: Phase {letzte.get('phase')} seit {_dauer(still_s)}",
        f"Letzter Schritt: {_einzeilig(letzte.get('marker'))}",
    ]
    if still_s > still_min * 60:
        zeilen_text.append(
            f"Keine neue Phase seit {int(still_s // 60)} min — Gate hängt oder ist gestorben, "
            "Prozess prüfen"
        )
        return "\n".join(zeilen_text), EXIT_STILL
    return "\n".join(zeilen_text), EXIT_LAEUFT


def _ticket(ticket: str | None, repo: Path) -> str | None:
    """``--ticket`` → ``BAU_TICKET`` (setzt bau.py) → Umgebung/Pfad des Repos → Arbeitsordner."""
    if ticket:
        return ticket
    aus_bau = (os.environ.get("BAU_TICKET") or "").strip()
    if aus_bau.isdigit():
        return aus_bau
    return bau_log.ticket_aus_umgebung(repo) or bau_log.ticket_aus_umgebung()


def deploy_status(
    repo: Path,
    datei: str | None = None,
    ticket: str | None = None,
    still_min: float = STILL_MIN_VORGABE,
) -> int:
    """Befehl ``deploy-status``: lesen, ins Bau-Log übernehmen, kurz melden.

    Zeigt die Statusdatei ausdrücklich in ein Git-Repo (``--datei`` oder
    ``DEPLOY_STATUS_DATEI``), gehört das Bau-Log in dieses Repo — nicht in das
    Arbeitsverzeichnis, aus dem der Loop zufällig aufruft.
    """
    pfad = status_pfad(repo, datei)
    zeilen = lese_status(pfad)
    if not zeilen:
        print(f"Keine Deploy-Statusdatei mit Inhalt: {pfad}")
        return EXIT_KEINE_DATEI

    if pfad != repo / DATEI_NAME:
        kandidat = config.repo_wurzel(pfad.parent)
        if (kandidat / ".git").exists():
            repo = kandidat
    nummer = _ticket(ticket, repo)
    if nummer:
        neu = uebernimm(repo, str(nummer), zeilen)
        log.debug("%d neue deploy_phase-Zeilen für Ticket %s", neu, nummer)
    else:
        log.warning(
            "kein Ticket (--ticket, TO_SPAWN_TICKET oder wt-<N>) — nur Anzeige, Bau-Log bleibt unverändert."
        )

    text, code = bericht(zeilen, still_min, datetime.now(timezone.utc))
    print(text)
    return code
