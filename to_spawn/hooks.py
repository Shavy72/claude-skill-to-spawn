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
from collections.abc import Iterator
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


def _antwort_schluessel(eintrag: dict[str, Any]) -> str | None:
    """Kennung einer Modellantwort: ``message.id``, sonst ``requestId``."""
    nachricht = eintrag.get("message")
    kennung = nachricht.get("id") if isinstance(nachricht, dict) else None
    if isinstance(kennung, str) and kennung:
        return f"msg:{kennung}"
    anfrage = eintrag.get("requestId")
    if isinstance(anfrage, str) and anfrage:
        return f"req:{anfrage}"
    return None


def _je_antwort(eintraege: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """``message.usage`` je Modellantwort genau einmal.

    Claude Code schreibt eine Modellantwort als mehrere Zeilen (je Inhaltsblock) mit
    derselben ``message.id``/``requestId`` und derselben ``usage`` — je Kennung zählt
    nur die erste Zeile. Zeilen ohne Kennung zählen einzeln (Fixrunde #204).
    """
    gesehen: set[str] = set()
    for eintrag in eintraege:
        verbrauch = _usage(eintrag)
        if verbrauch is None:
            continue
        schluessel = _antwort_schluessel(eintrag)
        if schluessel is not None:
            if schluessel in gesehen:
                continue
            gesehen.add(schluessel)
        yield verbrauch


def summiere(eintraege: list[dict[str, Any]]) -> dict[str, int]:
    """Token-Summe über ``message.usage`` der übergebenen assistant-Zeilen."""
    summe = dict(LEERE_TOKENS)
    for verbrauch in _je_antwort(eintraege):
        summe["input"] += _zahl(verbrauch.get("input_tokens"))
        summe["cache_read"] += _zahl(verbrauch.get("cache_read_input_tokens"))
        summe["cache_creation"] += _zahl(verbrauch.get("cache_creation_input_tokens"))
        summe["output"] += _zahl(verbrauch.get("output_tokens"))
    summe["gesamt"] = (
        summe["input"] + summe["cache_read"] + summe["cache_creation"] + summe["output"]
    )
    return summe


def kontext(eintraege: list[dict[str, Any]]) -> dict[str, int] | None:
    """Spitzen-Kontext und Anzahl der Modellaufrufe (#238); ``None`` ohne Aufruf.

    Jeder Aufruf schickt den ganzen bisherigen Kontext; ``input`` + ``cache_read`` +
    ``cache_creation`` eines Aufrufs ist also die Kontextgröße in diesem Moment. Die
    Summe über alle Aufrufe wächst quadratisch und ist keine Kontextgröße — die
    Smart-Zone-Zahl ist das Maximum. Ohne lesbaren Aufruf (leeres oder fehlendes
    Transkript) gibt es keinen Wert statt einer falschen 0.
    """
    spitze = 0
    aufrufe = 0
    for verbrauch in _je_antwort(eintraege):
        aufrufe += 1
        groesse = (
            _zahl(verbrauch.get("input_tokens"))
            + _zahl(verbrauch.get("cache_read_input_tokens"))
            + _zahl(verbrauch.get("cache_creation_input_tokens"))
        )
        spitze = max(spitze, groesse)
    return {"spitze": spitze, "aufrufe": aufrufe} if aufrufe else None


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
    return dauer_aus_zeitstempeln(eintraege)


def dauer_aus_zeitstempeln(eintraege: list[dict[str, Any]]) -> int:
    """Spanne erster bis letzter Transkript-Zeitstempel (für Subagenten, Fixrunde #204)."""
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


PROTOKOLL = Path(".claude") / "to-spawn" / "hooks.log"


def _datei_protokoll() -> None:
    """Hook-Meldungen zusätzlich nach ``~/.claude/to-spawn/hooks.log`` (Fixrunde #204).

    stderr eines Hooks mit Exit 0 sieht niemand. Scheitert das Anlegen, läuft der
    Hook still weiter.
    """
    try:
        ziel = (Path.home() / PROTOKOLL).resolve()
        ziel.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(ziel, encoding="utf-8")
    except (OSError, RuntimeError):
        return
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [pid %(process)d] %(name)s: %(message)s")
    )
    handler.set_name("to-spawn-hooks")
    wurzel = logging.getLogger("to_spawn")
    for alt in [h for h in wurzel.handlers if h.get_name() == "to-spawn-hooks"]:
        wurzel.removeHandler(alt)
        alt.close()
    wurzel.addHandler(handler)
    if wurzel.level == logging.NOTSET or wurzel.level > logging.INFO:
        wurzel.setLevel(logging.INFO)


def _ohne_log_repo(hook: str) -> None:
    """Protokoll-Zeile, wenn der Ziel-Ordner (Ticket-Worktree) fehlt."""
    log.warning(
        "%s: Bau-Log-Ordner fehlt (TO_SPAWN_LOG_REPO=%s) — keine Zeile für Ticket #%s.",
        hook,
        os.environ.get("TO_SPAWN_LOG_REPO") or "(nicht gesetzt)",
        bau_log.ticket_aus_umgebung() or "?",
    )


def hook_stop(strom: TextIO | None = None, ausgabe: TextIO | None = None) -> int:
    """Stop-Hook: schreibt ``session_ende`` und — bei frischem Handoff — den Marker.

    Endet immer mit 0: ein Fehler im Hook darf die Bau-Session nie stören (#204).
    """
    _datei_protokoll()
    try:
        _hook_stop(strom or sys.stdin, ausgabe or sys.stdout)
    except Exception:
        log.exception("Stop-Hook fehlgeschlagen — keine Log-Zeile, Session läuft weiter.")
    return 0


def _hook_stop(strom: TextIO, ausgabe: TextIO) -> None:
    daten = _eingabe(strom)
    repo = bau_log.log_repo()
    if repo is None:
        _ohne_log_repo("Stop-Hook")
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
        kontext=kontext(haupt),
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
    _datei_protokoll()
    try:
        _hook_subagent_stop(strom or sys.stdin)
    except Exception:
        log.exception("SubagentStop-Hook fehlgeschlagen — keine Log-Zeile, Session läuft weiter.")
    return 0


def _hook_subagent_stop(strom: TextIO) -> None:
    daten = _eingabe(strom)
    repo = bau_log.log_repo()
    if repo is None:
        _ohne_log_repo("SubagentStop-Hook")
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
        zeit_kette = eintraege  # das ganze eigene Transkript gehört dem Subagenten
    else:
        eintraege = _zeilen(Path(daten.get("transcript_path") or ""))
        kette = letzte_subagent_kette(eintraege)
        zeit_kette = kette

    eltern = daten.get("session_id")
    bau_log.schreibe(
        repo,
        ticket,
        "subagent_ende",
        session_id=_subagent_kennung(daten, zeit_kette),
        eltern_session=eltern,
        vermerk=f"Subagent von Session {eltern}" if eltern else None,
        staffel=_staffel(),
        modell=modell_aus(kette) or daten.get("agent_type"),
        effort=os.environ.get("TO_SPAWN_EFFORT"),
        runner=konfig.get("runner"),
        tokens=summiere(kette),
        kontext=kontext(kette),
        # Nur die eigene Kette — TO_SPAWN_START ist der Start der ganzen Session.
        dauer_s=dauer_aus_zeitstempeln(zeit_kette),
        text=_text(daten.get("last_assistant_message"), "Subagent beendet."),
    )


def _subagent_kennung(daten: dict[str, Any], kette: list[dict[str, Any]]) -> str | None:
    """``agent_id``, sonst Dateiname des Subagenten-Transkripts, sonst
    ``<session_id>:<erster Zeitstempel der Kette>`` — nie die bloße Eltern-Session,
    sonst verschluckt die Zählung je Kennung alle Subagenten bis auf einen."""
    agent_id = daten.get("agent_id")
    if isinstance(agent_id, str) and agent_id:
        return agent_id
    eigenes = daten.get("agent_transcript_path")
    if isinstance(eigenes, str) and eigenes.strip():
        return Path(eigenes).stem
    eltern = daten.get("session_id")
    if not eltern:
        return None
    return f"{eltern}:{_erster_zeitpunkt(kette) or '?'}"


def _transkript_fuer(zeile: dict[str, Any], transkripte: Path) -> Path | None:
    """Transkript zu einer alten Log-Zeile im Claude-Projektordner (#238).

    ``session_ende``: ``<projekt>/<session_id>.jsonl``. ``subagent_ende``:
    ``<projekt>/<eltern_session>/subagents/agent-<session_id>.jsonl``.
    """
    kennung = str(zeile.get("session_id") or "")
    if not kennung or "/" in kennung or ":" in kennung:
        return None
    if zeile.get("typ") == "subagent_ende":
        eltern = str(zeile.get("eltern_session") or "")
        if not eltern or "/" in eltern:
            return None
        kennung = kennung.removeprefix("agent-")  # Kennung aus dem Dateinamen
        name = f"{eltern}/subagents/agent-{kennung}.jsonl"
    else:
        name = f"{kennung}.jsonl"
    for kandidat in (transkripte / name, *transkripte.glob(f"*/{name}")):
        if kandidat.is_file():
            return kandidat
    return None


def umrechnen(repo: Path, ticket: str, transkripte: Path) -> tuple[int, int]:
    """Alten Log-Zeilen ohne ``kontext`` den Spitzen-Kontext nachtragen (#238).

    Vor #238 stand nur ``tokens`` (Summe über alle Aufrufe) im Log. Für jede
    Session, deren jüngste Zeile keinen ``kontext`` trägt, hängt ``umrechnen`` eine
    Kopie dieser Zeile mit ``kontext`` an die Laufdatei an — nie umschreiben: ein
    gleichzeitig laufender Hook hängt ebenfalls nur an, keine Zeile geht verloren,
    und die jüngste Zeile je Session gewinnt beim Lesen. ``eintrag`` überträgt die
    Nachträge wie jede Laufzeile in die versionierte Datei.
    Gibt (umgerechnet, ohne Transkript) zurück.
    """
    zeilen = bau_log.lese(repo, ticket)
    juengste: dict[tuple[str, str], dict[str, Any]] = {}
    ohne = 0
    for zeile in zeilen:
        if zeile.get("typ") not in ("session_ende", "subagent_ende"):
            continue
        kennung = zeile.get("session_id")
        if not kennung:
            if not isinstance(zeile.get("kontext"), dict):
                ohne += 1
            continue
        juengste[(str(zeile["typ"]), str(kennung))] = zeile
    umgerechnet = 0
    for zeile in juengste.values():
        if isinstance(zeile.get("kontext"), dict):
            continue
        pfad = _transkript_fuer(zeile, transkripte)
        wert = None
        if pfad is not None:
            eintraege = _zeilen(pfad)
            kette = haupt_zeilen(eintraege) if zeile.get("typ") == "session_ende" else eintraege
            wert = kontext(kette)
        if wert is None:
            ohne += 1
            continue
        felder = {k: v for k, v in zeile.items() if k not in ("ts", "typ", "ticket")}
        felder.update(kontext=wert, umgerechnet_aus=pfad.name)
        bau_log.schreibe(repo, ticket, str(zeile["typ"]), **felder)
        umgerechnet += 1
    return umgerechnet, ohne


def _staffel() -> int:
    roh = os.environ.get("TO_SPAWN_STAFFEL", "1")
    return int(roh) if roh.isdigit() else 1
