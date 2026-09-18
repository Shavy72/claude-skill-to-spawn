"""Stop- und SubagentStop-Hook: Zahlen aus dem Transkript ins Bau-Log.

Claude Code reicht dem Hook ein JSON auf stdin (``session_id``,
``transcript_path``, ``hook_event_name``, ``stop_hook_active`` …). Das Transkript
ist JSONL; ``assistant``-Zeilen tragen ``message.usage``. Zeilen mit
``isSidechain: true`` gehören Subagenten.

Wichtig (Doku ``code.claude.com/docs/en/hooks``, Abschnitt „Stop decision
control"): ein Stop-Hook kann den Prozess **nicht** beenden — ``decision:
"block"`` hält Claude nur am Laufen, ``continue: false`` beendet lediglich die
Verarbeitung der Runde. Deshalb legt der Hook zusätzlich eine Marker-Datei
``.to-spawn/stop-<N>`` an, die der Elternprozess (Staffel-Schleife) sieht.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from . import bau_log, config

log = logging.getLogger("to_spawn.hooks")

LEERE_TOKENS = {
    "input": 0,
    "cache_read": 0,
    "cache_creation": 0,
    "output": 0,
    "gesamt": 0,
}


def marker_pfad(repo: Path, ticket: str | int) -> Path:
    return repo / ".to-spawn" / f"stop-{ticket}"


def _zeilen(pfad: Path) -> list[dict[str, Any]]:
    if not pfad or not Path(pfad).is_file():
        return []
    ergebnis: list[dict[str, Any]] = []
    for roh in Path(pfad).read_text(encoding="utf-8", errors="replace").splitlines():
        roh = roh.strip()
        if not roh:
            continue
        try:
            eintrag = json.loads(roh)
        except ValueError:
            continue
        if isinstance(eintrag, dict):
            ergebnis.append(eintrag)
    return ergebnis


def _usage(eintrag: dict[str, Any]) -> dict[str, Any] | None:
    nachricht = eintrag.get("message")
    if eintrag.get("type") != "assistant" or not isinstance(nachricht, dict):
        return None
    verbrauch = nachricht.get("usage")
    return verbrauch if isinstance(verbrauch, dict) else None


def _zahl(wert: Any) -> int:
    """Token-Wert als Ganzzahl; Unlesbares zählt 0 statt den Hook abzubrechen."""
    if isinstance(wert, bool):
        return 0
    try:
        return int(wert or 0)
    except (TypeError, ValueError):
        log.warning("Unlesbarer Token-Wert %r im Transkript — zählt 0.", wert)
        return 0


def summiere(eintraege: list[dict[str, Any]]) -> dict[str, int]:
    """Token-Summe über ``message.usage`` der übergebenen assistant-Zeilen."""
    summe = dict(LEERE_TOKENS)
    for eintrag in eintraege:
        verbrauch = _usage(eintrag)
        if verbrauch is None:
            continue
        summe["input"] += _zahl(verbrauch.get("input_tokens"))
        summe["cache_read"] += _zahl(verbrauch.get("cache_read_input_tokens"))
        summe["cache_creation"] += _zahl(verbrauch.get("cache_creation_input_tokens"))
        summe["output"] += _zahl(verbrauch.get("output_tokens"))
    summe["gesamt"] = (
        summe["input"] + summe["cache_read"] + summe["cache_creation"] + summe["output"]
    )
    return summe


def modell_aus(eintraege: list[dict[str, Any]]) -> str | None:
    for eintrag in reversed(eintraege):
        nachricht = eintrag.get("message")
        if isinstance(nachricht, dict) and nachricht.get("model"):
            return str(nachricht["model"])
    return None


def _zeitstempel(eintrag: dict[str, Any]) -> datetime | None:
    roh = eintrag.get("timestamp")
    if not isinstance(roh, str):
        return None
    try:
        return datetime.fromisoformat(roh.replace("Z", "+00:00"))
    except ValueError:
        return None


def dauer_sekunden(eintraege: list[dict[str, Any]]) -> int:
    """Dauer aus ``TO_SPAWN_START`` (Epoch) oder aus den Transkript-Zeitstempeln."""
    start_env = os.environ.get("TO_SPAWN_START")
    if start_env:
        try:
            return max(0, int(datetime.now().timestamp() - float(start_env)))
        except ValueError:
            pass
    zeiten = [z for z in (_zeitstempel(e) for e in eintraege) if z is not None]
    if len(zeiten) < 2:
        return 0
    return max(0, int((max(zeiten) - min(zeiten)).total_seconds()))


def haupt_zeilen(eintraege: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """assistant-Zeilen der Hauptsitzung (ohne Subagenten)."""
    return [e for e in eintraege if not e.get("isSidechain") and _usage(e) is not None]


def letzte_subagent_kette(eintraege: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """assistant-Zeilen der zuletzt gelaufenen Subagenten-Kette (Sidechain)."""
    kette: list[dict[str, Any]] = []
    letzte: list[dict[str, Any]] = []
    for eintrag in eintraege:
        if eintrag.get("isSidechain"):
            if _usage(eintrag) is not None:
                kette.append(eintrag)
        else:
            if kette:
                letzte = kette
            kette = []
    return kette or letzte


def _frischer_handoff(repo: Path, ticket: str) -> Path | None:
    """Handoff-Datei zum Ticket, die nach dem Session-Start entstanden ist."""
    start_env = os.environ.get("TO_SPAWN_START")
    ordner = repo / "docs" / "handoffs"
    if not ordner.is_dir():
        return None
    grenze = float(start_env) if start_env else 0.0
    kandidaten = [p for p in ordner.glob(f"HANDOFF_*_{ticket}.md") if p.stat().st_mtime >= grenze]
    if not kandidaten:
        return None
    return max(kandidaten, key=lambda p: p.stat().st_mtime)


def _eingabe(strom: TextIO) -> dict[str, Any]:
    roh = strom.read().strip()
    if not roh:
        return {}
    try:
        daten = json.loads(roh)
    except ValueError:
        log.warning("Hook-Eingabe ist kein JSON — leere Eingabe angenommen.")
        return {}
    return daten if isinstance(daten, dict) else {}


def _text(wert: Any, vorgabe: str) -> str:
    """Kurztext für die Log-Zeile (``null`` oder Nicht-Text im Hook-JSON → Vorgabe)."""
    return (wert if isinstance(wert, str) else "")[:300] or vorgabe


def _erster_zeitpunkt(eintraege: list[dict[str, Any]]) -> str | None:
    """Zeitstempel des ersten Transkript-Eintrags (wie im Transkript geschrieben)."""
    for eintrag in eintraege:
        roh = eintrag.get("timestamp")
        if isinstance(roh, str) and roh:
            return roh
    return None


def _hat_zeile(repo: Path, ticket: str, typ: str, session_id: str) -> bool:
    return any(
        z.get("typ") == typ and z.get("session_id") == session_id
        for z in bau_log.lese(repo, ticket)
    )


def hook_stop(strom: TextIO | None = None, ausgabe: TextIO | None = None) -> int:
    """Stop-Hook: schreibt ``session_ende`` und — bei frischem Handoff — den Marker.

    Endet immer mit 0: ein Fehler im Hook darf die Bau-Session nie stören (#204).
    """
    try:
        _hook_stop(strom or sys.stdin, ausgabe or sys.stdout)
    except Exception:
        log.exception("Stop-Hook fehlgeschlagen — keine Log-Zeile, Session läuft weiter.")
    return 0


def _hook_stop(strom: TextIO, ausgabe: TextIO) -> None:
    daten = _eingabe(strom)
    repo = bau_log.log_repo()
    if repo is None:
        return
    konfig = config.lade(repo)
    ticket = bau_log.ticket_aus_umgebung(repo)
    if ticket is None:
        log.info("Kein Ticket erkennbar (TO_SPAWN_TICKET/wt-<N>) — keine Log-Zeile.")
        return

    eintraege = _zeilen(Path(daten.get("transcript_path") or ""))
    haupt = haupt_zeilen(eintraege)
    session_id = daten.get("session_id")
    modell = modell_aus(haupt) or konfig.get("modelle", {}).get("ticket")
    effort = os.environ.get("TO_SPAWN_EFFORT") or konfig.get("effort", {}).get("ticket")

    # Stop feuert am Ende jeder Runde — session_start nur beim ersten Mal je Session.
    if session_id and not _hat_zeile(repo, ticket, "session_start", str(session_id)):
        bau_log.schreibe(
            repo,
            ticket,
            "session_start",
            session_id=session_id,
            staffel=_staffel(),
            modell=modell,
            effort=effort,
            runner=konfig.get("runner"),
            beginn=_erster_zeitpunkt(eintraege),
            text="Session gestartet.",
        )
    bau_log.schreibe(
        repo,
        ticket,
        "session_ende",
        session_id=session_id,
        staffel=_staffel(),
        modell=modell,
        effort=effort,
        runner=konfig.get("runner"),
        tokens=summiere(haupt),
        dauer_s=dauer_sekunden(eintraege),
        text=_text(daten.get("last_assistant_message"), "Session beendet."),
    )

    handoff = _frischer_handoff(repo, ticket)
    if handoff is None:
        return

    marker = marker_pfad(repo, ticket)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(handoff), encoding="utf-8")
    if not (session_id and _hat_zeile(repo, ticket, "handoff", str(session_id))):
        bau_log.schreibe(
            repo,
            ticket,
            "handoff",
            session_id=session_id,
            staffel=_staffel(),
            text=f"Handoff geschrieben: {handoff.name} — Folge-Session übernimmt.",
        )
    if config.staffel_modus(konfig) == "hook":
        # Weg "hook": Claude soll die Verarbeitung beenden. Laut Doku beendet das
        # nur die Runde, nicht den Prozess — der Marker bleibt der sichere Weg.
        ausgabe.write(
            json.dumps(
                {"continue": False, "stopReason": "Smart Zone erreicht — Handoff geschrieben."},
                ensure_ascii=False,
            )
        )


def hook_subagent_stop(strom: TextIO | None = None) -> int:
    """SubagentStop-Hook: schreibt ``subagent_ende`` inklusive Eltern-Session.

    Endet immer mit 0: ein Fehler im Hook darf die Bau-Session nie stören (#204).
    """
    try:
        _hook_subagent_stop(strom or sys.stdin)
    except Exception:
        log.exception("SubagentStop-Hook fehlgeschlagen — keine Log-Zeile, Session läuft weiter.")
    return 0


def _hook_subagent_stop(strom: TextIO) -> None:
    daten = _eingabe(strom)
    repo = bau_log.log_repo()
    if repo is None:
        return
    konfig = config.lade(repo)
    ticket = bau_log.ticket_aus_umgebung(repo)
    if ticket is None:
        log.info("Kein Ticket erkennbar — keine Subagenten-Zeile.")
        return

    eigenes = daten.get("agent_transcript_path")
    if eigenes and Path(eigenes).is_file():
        eintraege = _zeilen(Path(eigenes))
        kette = [e for e in eintraege if _usage(e) is not None]
    else:
        eintraege = _zeilen(Path(daten.get("transcript_path") or ""))
        kette = letzte_subagent_kette(eintraege)

    eltern = daten.get("session_id")
    bau_log.schreibe(
        repo,
        ticket,
        "subagent_ende",
        session_id=daten.get("agent_id") or eltern,
        eltern_session=eltern,
        vermerk=f"Subagent von Session {eltern}" if eltern else None,
        staffel=_staffel(),
        modell=modell_aus(kette) or daten.get("agent_type"),
        effort=os.environ.get("TO_SPAWN_EFFORT"),
        runner=konfig.get("runner"),
        tokens=summiere(kette),
        dauer_s=dauer_sekunden(kette),
        text=_text(daten.get("last_assistant_message"), "Subagent beendet."),
    )


def _staffel() -> int:
    roh = os.environ.get("TO_SPAWN_STAFFEL", "1")
    return int(roh) if roh.isdigit() else 1
