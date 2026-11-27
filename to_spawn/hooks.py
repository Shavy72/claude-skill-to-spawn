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

import base64
import fnmatch
import json
import logging
import os
import posixpath
import re
import shlex
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from . import bau_log, befund, config, schliess_pruefer

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


def lese_transkript(pfad: Path) -> list[dict[str, Any]]:
    """Liest ein Claude-Transkript (JSONL) fehlertolerant; fehlende Datei → leer."""
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


_zeilen = lese_transkript


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
    summe["gesamt"] = summe["input"] + summe["cache_read"] + summe["cache_creation"] + summe["output"]
    return summe


def _aufruf_kontext(verbrauch: dict[str, Any]) -> int:
    """Kontextgröße eines einzelnen Modellaufrufs (``input`` + ``cache_read`` + ``cache_creation``)."""
    return (
        _zahl(verbrauch.get("input_tokens"))
        + _zahl(verbrauch.get("cache_read_input_tokens"))
        + _zahl(verbrauch.get("cache_creation_input_tokens"))
    )


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
        spitze = max(spitze, _aufruf_kontext(verbrauch))
    return {"spitze": spitze, "aufrufe": aufrufe} if aufrufe else None


def start_kontext(eintraege: list[dict[str, Any]]) -> int | None:
    """Kontext nach Start (#412): Kontextgröße des ersten echten Aufrufs der Hauptsitzung.

    Subagenten-Zeilen (``isSidechain: true``) und leere Null-Aufrufe (z. B. synthetische
    Fehlerzeilen) zählen nicht mit. Ohne lesbaren Aufruf → ``None``.
    """
    for verbrauch in _je_antwort(haupt_zeilen(eintraege)):
        groesse = _aufruf_kontext(verbrauch)
        if groesse > 0:
            return groesse
    return None


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
    return any(z.get("typ") == typ and z.get("session_id") == session_id for z in bau_log.lese(repo, ticket))


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
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [pid %(process)d] %(name)s: %(message)s"))
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


def _starte_takt(repo: Path, ticket: str) -> None:
    """Nach ``session_ende`` den Aufseher-Takt der Spec abgelöst starten (#316).

    Spec aus ``TO_SPAWN_SPEC`` (setzt ``bau.py``); ohne Spec nichts. Bei ``--ohne-wache``
    oder fremdem Rechner laut gespeicherter Auswahl wird kein Takt ausgelöst (#271).
    Fehler nur loggen.
    """
    spec = os.environ.get("TO_SPAWN_SPEC", "").strip()
    if not spec.isdigit():
        return
    try:
        from . import auswahl, waechter_takt

        ziel = Path(os.environ["TO_SPAWN_REPO"]) if os.environ.get("TO_SPAWN_REPO") else repo
        stand_auswahl = auswahl.lesen(ziel, int(spec))
        if not auswahl.wache_erlaubt(ziel, int(spec), auswahl=stand_auswahl, gelesen=True):
            log.info(
                "Aufseher-Takt #%s unterdrückt: laut Auswahl hier kein Aufseher (%s).",
                spec,
                auswahl.beschreibung(stand_auswahl),
            )
            return
        if ticket.isdigit() and not auswahl.ticket_erlaubt(
            ziel, int(spec), int(ticket), auswahl=stand_auswahl, gelesen=True
        ):
            log.info(
                "Aufseher-Takt #%s unterdrückt: Ticket #%s außerhalb der Auswahl (%s).",
                spec,
                ticket,
                auswahl.beschreibung(stand_auswahl),
            )
            return
        waechter_takt.starte_abgeloest(int(spec), ziel, f"session_ende #{ticket}")
    except Exception:  # noqa: BLE001 — der Hook darf die Bau-Session nie stören
        log.exception("Aufseher-Takt #%s nicht gestartet — Session läuft weiter.", spec)


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

    commit_wert = bau_log.git_kopf_kurz(repo, ticket)
    session_wert = str(session_id) if session_id else f"hook-{ticket}"
    # Stop feuert am Ende jeder Runde — session_start nur beim ersten Mal je Session.
    if session_id and not _hat_zeile(repo, ticket, "session_start", str(session_id)):
        bau_log.schreibe(
            "session_start",
            repo=repo,
            ticket=ticket,
            wer="hook-stop",
            was="Session gestartet.",
            session=session_wert,
            commit=commit_wert,
            grund="Stop-Hook erster Lauf",
            kontextstand=0,
            session_id=session_id,
            staffel=_staffel(),
            modell=modell,
            effort=effort,
            runner=konfig.get("runner"),
            beginn=_erster_zeitpunkt(eintraege),
            text="Session gestartet.",
        )
    kontext_daten = kontext(haupt)
    spitze = kontext_daten.get("spitze") if isinstance(kontext_daten, dict) else None
    kontextstand = round(spitze / 1000, 1) if isinstance(spitze, (int, float)) and spitze > 0 else 0
    ende_text = _text(daten.get("last_assistant_message"), "Session beendet.")
    bau_log.schreibe(
        "session_ende",
        repo=repo,
        ticket=ticket,
        wer="hook-stop",
        was=ende_text[:200] or "Session beendet.",
        session=session_wert,
        commit=commit_wert,
        grund="Stop-Hook Runden-Ende",
        kontextstand=kontextstand,
        session_id=session_id,
        staffel=_staffel(),
        modell=modell,
        effort=effort,
        runner=konfig.get("runner"),
        tokens=summiere(haupt),
        kontext=kontext_daten,
        dauer_s=dauer_sekunden(eintraege),
        text=ende_text,
    )
    _starte_takt(repo, ticket)

    handoff = _frischer_handoff(repo, ticket)
    if handoff is None:
        return

    marker = marker_pfad(repo, ticket)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(handoff), encoding="utf-8")
    if not (session_id and _hat_zeile(repo, ticket, "handoff", str(session_id))):
        bau_log.schreibe(
            "handoff",
            repo=repo,
            ticket=ticket,
            wer="hook-stop",
            was=f"Handoff geschrieben: {handoff.name}",
            session=session_wert,
            commit=commit_wert,
            grund=f"Handoff geschrieben: {handoff.name} — Folge-Session übernimmt.",
            kontextstand=kontextstand,
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


#: Umgebungsvariable für die Fertig-Meldung einer Bau-Session an ``bau.py`` (nur Linux, #288).
FERTIG_DATEI_ENV = "BAU_FERTIG_DATEI"


def _warten_laeuft(repo: Path, ticket: str) -> bool:
    """Läuft für ``ticket`` noch ein aktiver Hintergrund-Wartelauf (``to_spawn.warten``)?"""
    from . import warten

    try:
        zustaende = warten.zustaende_fuer_ticket(repo, ticket)
    except OSError:
        return False
    for _pfad, stand in zustaende:
        if stand.get("status") != "laeuft":
            continue
        pid = stand.get("laeufer_pid")
        if pid is None or warten._prozess_aktiv(pid, stand.get("laeufer_start")):
            return True
    return False


def fertig_pruefen_und_melden(stdin_json: str = "") -> bool:
    """Stop-Hook-Abschluss (nur Linux, #288): meldet fertige Bau-Sessions an ``bau.py``.

    Eine interaktive Claude-Session kann sich nach Ticket-Schluss nicht selbst per
    ``/exit`` beenden. Ist das Ticket nachweislich erledigt und hat die Session nichts
    mehr zu tun, schreibt dieser Hook atomar ``BAU_FERTIG_DATEI``. ``bau.py`` wartet
    daraufhin eine kurze Ruhefrist ab (falls z. B. ``review/stop.py`` blockiert oder
    ein Hintergrund-Agent läuft) und beendet die stille Session samt Fenster mit Exit 0.

    Regel (fail-closed bei Unklarheit):
    - Ticket ``endgueltig_zu`` (geschlossen/verworfen) und kein laufender ``warten``-Job ⇒ fertig.
    - Ticket ``fertig_gebaut`` (``staging:fertig``, offen): nur wenn keine offene
      Briefkasten-Frage (#416), kein Checkpoint-Label, kein laufender ``warten``-Job
      und :func:`wiederanlauf.feststellen` ``art == "nichts"`` liefert (#282).
    - Frischer Handoff oder bestehende ``BAU_STAFFEL_DATEI`` ⇒ Staffel hat Vorrang.
    """
    if sys.platform == "win32":
        return False
    fertig_roh = os.environ.get(FERTIG_DATEI_ENV, "").strip()
    ticket = os.environ.get("BAU_TICKET", "").strip()
    if not fertig_roh or not ticket:
        return False
    staffel_roh = os.environ.get("BAU_STAFFEL_DATEI", "").strip()
    if staffel_roh and Path(staffel_roh).is_file():
        return False

    log_ordner = bau_log.log_repo()
    rueckfall_roh = os.environ.get("TO_SPAWN_LOG_RUECKFALL", "").strip()
    haupt = Path(rueckfall_roh) if rueckfall_roh and Path(rueckfall_roh).is_dir() else log_ordner
    if haupt is None:
        return False
    for pruef_ordner in dict.fromkeys(p for p in (log_ordner, haupt) if p is not None):
        if _frischer_handoff(pruef_ordner, ticket) is not None:
            return False

    from . import briefkasten, manifest, ticket_zustand, wiederanlauf

    stand = ticket_zustand.lesen(ticket, haupt)
    if stand is None:
        return False
    if ticket_zustand.endgueltig_zu(stand):
        if _warten_laeuft(haupt, ticket):
            return False
    elif ticket_zustand.fertig_gebaut(stand):
        try:
            if briefkasten.offene([ticket], hauptbaum=haupt):
                return False
        except (OSError, ValueError, RuntimeError) as fehler:
            log.warning("#%s: Briefkasten nicht lesbar (%s) — Session bleibt offen", ticket, fehler)
            return False
        try:
            konfig = config.lade(haupt)
        except (OSError, ValueError) as fehler:
            log.warning("#%s: Konfig nicht lesbar (%s) — Session bleibt offen", ticket, fehler)
            return False
        if manifest.checkpoint_label(konfig) in ticket_zustand.label_namen(stand):
            return False
        if _warten_laeuft(haupt, ticket):
            return False
        if ticket.isdigit():
            lage = wiederanlauf.feststellen(int(ticket), haupt, daten=stand)
            if lage is None or lage.art != "nichts":
                return False
    else:
        return False

    try:
        daten = json.loads(stdin_json or "{}")
    except ValueError:
        daten = {}
    if not isinstance(daten, dict):
        daten = {}
    transkript_pfad = str(daten.get("transcript_path") or "").strip()
    transkript_groesse: int | None = None
    if transkript_pfad:
        try:
            pfad_obj = Path(transkript_pfad)
            if pfad_obj.is_file():
                transkript_groesse = pfad_obj.stat().st_size
        except OSError:
            transkript_groesse = None

    ziel = Path(fertig_roh)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    tmp = ziel.with_name(f".{ziel.name}.{os.getpid()}.tmp")
    inhalt = {
        "ticket": ticket,
        "grund": ticket_zustand.wortlaut(stand),
        "zeit": time.time(),
        "session_id": str(daten.get("session_id") or ""),
        "transcript_path": transkript_pfad,
        "transcript_size": transkript_groesse,
    }
    tmp.write_text(json.dumps(inhalt, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ziel)
    log.info("Fertig-Meldung für Ticket #%s geschrieben (%s).", ticket, inhalt["grund"])
    return True


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
    sub_id = _subagent_kennung(daten, zeit_kette)
    sub_kontext = kontext(kette)
    sub_spitze = sub_kontext.get("spitze") if isinstance(sub_kontext, dict) else None
    sub_kontextstand = round(sub_spitze / 1000, 1) if isinstance(sub_spitze, (int, float)) and sub_spitze > 0 else 0
    sub_text = _text(daten.get("last_assistant_message"), "Subagent beendet.")
    bau_log.schreibe(
        "subagent_ende",
        repo=repo,
        ticket=ticket,
        wer="hook-subagent-stop",
        was=sub_text[:200] or "Subagent beendet.",
        session=str(sub_id or eltern or f"subagent-{ticket}"),
        commit=bau_log.git_kopf_kurz(repo, ticket),
        grund="SubagentStop-Hook",
        kontextstand=sub_kontextstand,
        session_id=sub_id,
        eltern_session=eltern,
        vermerk=f"Subagent von Session {eltern}" if eltern else None,
        staffel=_staffel(),
        modell=modell_aus(kette) or daten.get("agent_type"),
        effort=os.environ.get("TO_SPAWN_EFFORT"),
        runner=konfig.get("runner"),
        tokens=summiere(kette),
        kontext=sub_kontext,
        # Nur die eigene Kette — TO_SPAWN_START ist der Start der ganzen Session.
        dauer_s=dauer_aus_zeitstempeln(zeit_kette),
        text=sub_text,
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
        felder = {k: v for k, v in zeile.items() if k not in ("ts", "zeit", "typ", "ticket")}
        if not str(felder.get("wer") or "").strip():
            felder["wer"] = "umrechnen"
        if not str(felder.get("was") or "").strip():
            felder["was"] = f"Spitzen-Kontext nachgetragen ({pfad.name})"
        if not str(felder.get("session") or "").strip():
            felder["session"] = str(zeile.get("session_id") or f"umrechnen-{ticket}")
        if not str(felder.get("commit") or "").strip():
            felder["commit"] = bau_log.git_kopf_kurz(repo, ticket)
        if not str(felder.get("grund") or "").strip():
            felder["grund"] = f"Spitzen-Kontext aus {pfad.name} nachgetragen"
        spitze = wert.get("spitze") if isinstance(wert, dict) else None
        felder["kontextstand"] = round(spitze / 1000, 1) if isinstance(spitze, (int, float)) and spitze > 0 else 0
        felder.update(kontext=wert, umgerechnet_aus=pfad.name)
        bau_log.schreibe(str(zeile["typ"]), repo=repo, ticket=ticket, **felder)
        umgerechnet += 1
    return umgerechnet, ohne


def _staffel() -> int:
    roh = os.environ.get("TO_SPAWN_STAFFEL", "1")
    return int(roh) if roh.isdigit() else 1


# --- Spec-Sperre (E2-4, App #1388 E56) ---------------------------------------------

#: Shell-Trenner zwischen einfachen Befehlen (zählen nur außerhalb von Anführungszeichen).
_TRENNER = frozenset(";&|()\n`")
#: Befehle, die den eigentlichen Befehl als Argument starten (wie ``nest/skill_schutz._PRAEFIXE``);
#: ihre Schalter (``sudo -u bau``) überspringt :func:`_befehlskern`. Verglichen wird der
#: Name ohne Pfad (``/usr/bin/env`` zählt wie ``env``).
_PRAEFIXE = {
    *("sudo", "doas", "env", "nohup", "command", "exec", "time", "nice"),
    *("timeout", "stdbuf", "setsid", "builtin", "coproc"),
}
#: Dauer-Argument von ``timeout`` (``30``, ``5m``, ``1.5s``) — steht vor dem eigentlichen Befehl.
_DAUER = re.compile(r"^\d+(\.\d+)?[smhd]?$")
#: Shell-Schlüsselwörter, nach denen ein neuer Befehl beginnt (``then gh …``, ``{ gh …; }``).
_SCHLUESSELWOERTER = {"if", "then", "else", "elif", "while", "until", "do", "!", "{", "}", "fi", "done"}
_ZUWEISUNG = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
#: Shells, deren ``-c``-Text selbst ein Befehl ist (wie ``eval``).
_SHELLS = {"bash", "sh", "zsh", "dash"}
#: Shell-Schalter mit eigenem Wert (``bash -o pipefail -c …``) — der Wert ist nie der ``-c``-Text.
_SHELL_MIT_WERT = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
#: Namen, die :func:`geschlossene_nummern` und :func:`push_pruef_ziele` selbst auswerten.
_ZIELE = {"gh", "git", "eval", *_SHELLS}
#: Schalter von ``gh`` (global, ``issue close``/``edit``, ``api``), die einen Wert nehmen —
#: der Wert ist nie Unterbefehl oder Nummer. Als ein Text, weil der Prüf-Test
#: ``test_aufseher_kommentar_1522`` jedes einzelne Kommentar-Schalter-Literal als
#: Kommentar-Schreiber meldet; das hier ist nur eine Parse-Tabelle.
_MIT_WERT = frozenset(
    """-R --repo -c --comment -r --reason --duplicate-of
    -t --title -b --body -F --body-file -m --milestone --state
    --add-label --remove-label --add-assignee --remove-assignee --add-project --remove-project
    -X --method -f --field --raw-field -H --header --input -q --jq --template --hostname --cache
    """.split()
)
_NUMMER = re.compile(r"^#?(\d+)$|/issues/(\d+)/?$")
_API_ISSUE = re.compile(r"(?:^|/)issues/(\d+)/?$")
_API_CLOSED = re.compile(r"state=closed")
#: ``gh api``-Schalter, deren Wert ein Feld des Bodys ist.
_API_FELD = {"-f", "-F", "--field", "--raw-field"}


def _roh_befehle(befehl: str) -> Iterator[list[str]]:
    """Einfache Befehle in ``befehl`` als Wortlisten, Zuweisungen/Präfixe noch davor.

    Geteilt wird nur an Trennern außerhalb von Anführungszeichen (``-c "a; b"`` bleibt
    ein Wort); ``\\``+Zeilenumbruch fügt Zeilen wie die Shell zusammen. Ein ``gh`` in
    einer Commit-Nachricht oder einem ``echo``-Text ist so nie Befehlsanfang.
    Ausnahme wie bei der Shell: ``$(…)`` und Backticks in doppelten Anführungszeichen
    laufen als Befehl — ihr Inhalt wird wie Text ohne Quotes zerlegt, danach gilt der
    alte Quote-Zustand wieder (Stapel ``offen``: schließendes Zeichen + Quote davor).
    """
    befehl = befehl.replace("\\\n", "")
    teile: list[str] = []
    offen: list[tuple[str, str]] = []
    start, quote, i = 0, "", 0
    while i < len(befehl):
        zeichen = befehl[i]
        if zeichen == "\\" and quote != "'":
            i += 2
            continue
        if quote == '"' and (zeichen == "`" or befehl.startswith("$(", i)):
            offen.append((zeichen if zeichen == "`" else ")", quote))
            quote = ""
            teile.append(befehl[start:i])
            i += 1 if zeichen == "`" else 2
            start = i
            continue
        if quote:
            if zeichen == quote:
                quote = ""
        elif zeichen in "'\"":
            quote = zeichen
        elif zeichen in _TRENNER:
            if offen and zeichen == offen[-1][0]:
                quote = offen.pop()[1]
            elif zeichen in "(`":
                offen.append((")" if zeichen == "(" else "`", ""))
            teile.append(befehl[start:i])
            start = i + 1
        i += 1
    teile.append(befehl[start:])
    for teil in teile:
        try:
            yield shlex.split(teil)
        except ValueError:
            yield teil.split()


def _einfache_befehle(befehl: str) -> Iterator[list[str]]:
    """Einfache Befehle in ``befehl`` als Wortlisten, ohne Zuweisungen/Präfixe davor
    (Zerlegung siehe :func:`_roh_befehle`, Kern siehe :func:`_befehlskern`)."""
    for roh in _roh_befehle(befehl):
        woerter = _befehlskern(roh)
        if woerter:
            yield woerter


def _befehlskern(woerter: list[str]) -> list[str]:
    """``woerter`` ab dem eigentlichen Befehl: Zuweisungen, Schlüsselwörter und Präfixe weg.

    Hinter einem Präfix (``sudo``, ``env`` …) fallen auch seine Schalter weg; ein Wort direkt
    nach einem Schalter gilt als dessen Wert (``sudo -u bau gh …``), außer es ist selbst ein
    Ziel wie ``gh``. Im Zweifel bleibt so eher ``gh`` übrig als ein harmloser Befehl —
    ``sudo -E echo gh issue close 5`` wird gesperrt (fail-closed, kommt praktisch nicht vor).
    """
    i, nach_praefix = 0, False
    while i < len(woerter):
        wort = woerter[i]
        if wort in _SCHLUESSELWOERTER or _ZUWEISUNG.match(wort):
            i += 1
        elif wort == "function":
            i += 2  # ``function f { gh …; }``: Name überspringen, ``{`` ist Schlüsselwort
        elif _name(wort) in _PRAEFIXE:
            i, nach_praefix = i + 1, True
        elif nach_praefix and _DAUER.match(wort):
            i += 1
        elif nach_praefix and wort.startswith("-"):
            i += 1
            folgend = woerter[i] if i < len(woerter) else ""
            if "=" not in wort and not folgend.startswith("-") and _name(folgend) not in _ZIELE:
                i += 1
        else:
            break
    return woerter[i:]


def _name(wort: str) -> str:
    """Befehlsname ohne Pfad, Windows-Endpunkt und ``.exe`` (``/usr/bin/gh`` → ``gh``, ``C:\\…\\cmd.exe`` → ``cmd``)."""
    basis = wort.replace("\\", "/").rsplit("/", 1)[-1]
    if basis not in (".", ".."):
        basis = basis.rstrip(".") or basis
    return basis[:-4] if basis.lower().endswith(".exe") else basis


def _shell_text(args: list[str]) -> str | None:
    """Der ``-c``-Text von ``bash <args>``, sonst ``None``.

    ``-c`` darf in einer Schalter-Gruppe stecken (``-lc``, ``-ec``); der Text ist dann das
    erste Positions-Argument, wie bei der Shell selbst.
    """
    mit_c, ueberspringen = False, False
    for arg in args:
        if ueberspringen:
            ueberspringen = False
        elif arg in _SHELL_MIT_WERT:
            ueberspringen = True
        elif arg.startswith(("--", "+")):
            continue
        elif arg.startswith("-") and len(arg) > 1:
            mit_c = mit_c or "c" in arg[1:]
        else:
            return arg if mit_c else None
    return None


def _zerlegen(args: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """``gh``-Argumente in Positionen und ``(schalter, wert)``-Paare.

    Alle Schreibweisen eines Schalters landen in derselben Form: ``--input datei``,
    ``--input=datei``, ``-f state=closed``, ``-fstate=closed`` und ``-f=state=closed``.
    Schalter ohne Wert (``--web``) stehen mit leerem Wert da.
    """
    positionen: list[str] = []
    schalter: list[tuple[str, str]] = []
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if not arg.startswith("-") or arg == "-":
            positionen.append(arg)
            continue
        if arg.startswith("--") and "=" in arg:
            name, wert = arg.split("=", 1)
        elif not arg.startswith("--") and len(arg) > 2 and arg[:2] in _MIT_WERT:
            name, wert = arg[:2], arg[2:].removeprefix("=")
        elif arg in _MIT_WERT:
            name, wert = arg, args[i] if i < len(args) else ""
            i += 1
        else:
            name, wert = arg, ""
        schalter.append((name, wert))
    return positionen, schalter


def _schliesst(args: list[str]) -> int | None:
    """Issue-Nummer, die ``gh <args>`` schließen würde, sonst ``None``."""
    pos, schalter = _zerlegen(args)
    if pos[:2] == ["issue", "close"] and len(pos) > 2:
        treffer = _NUMMER.search(pos[2])
        return int(treffer.group(1) or treffer.group(2)) if treffer else None
    if pos[:2] == ["issue", "edit"] and len(pos) > 2:
        treffer = _NUMMER.search(pos[2])
        zu = ("--state", "closed") in schalter
        return int(treffer.group(1) or treffer.group(2)) if zu and treffer else None
    if pos[:1] == ["api"] and len(pos) > 1:
        treffer = _API_ISSUE.search(pos[1])
        # ``--input`` schreibt einen beliebigen Body (auch ``state: closed``) — sperren.
        geschlossen = any(
            name == "--input" or (name in _API_FELD and _API_CLOSED.fullmatch(wert)) for name, wert in schalter
        )
        return int(treffer.group(1)) if treffer and geschlossen else None
    return None


def geschlossene_nummern(befehl: str) -> list[int]:
    """Issue-Nummern, die ``befehl`` schließen würde.

    Erkennt je einfachem Befehl (siehe :func:`_einfache_befehle`) ``gh issue close <N>``,
    ``gh issue edit <N> --state closed`` und ``gh api …/issues/<N> … state=closed``;
    Werte von Schaltern wie ``--comment``/``--repo`` zählen nicht als Nummer.
    ``eval …`` und ``bash -c "…"`` werden ausgepackt; ``gh api …/issues/<N> --input``
    zählt als Schließen. ``gh issue edit --state`` kennt gh heute nicht — die Zeile
    bleibt als Netz, falls es kommt.
    ponytail: erkennt nur wörtliche Aufrufe — ``curl``, ``gh api graphql`` (``closeIssue``
    mit Knoten-ID statt Nummer), ``Closes #<N>`` in
    Commit/PR-Text (GitHub schließt beim Merge), Variablen oder ein Skript umgehen die
    Sperre (gleiche Grenze wie ``skill_schutz``). Die eigentliche Tür ist ``spec-schliessen``: nur dort steht die
    Prüfung; der Hook fängt nur das versehentliche rohe Schließen ab.
    """
    nummern: list[int] = []
    for woerter in _einfache_befehle(befehl):
        name = _name(woerter[0])
        if name == "eval":
            nummern += geschlossene_nummern(" ".join(woerter[1:]))
        elif name in _SHELLS and (text := _shell_text(woerter[1:])) is not None:
            nummern += geschlossene_nummern(text)
        elif name == "gh" and (n := _schliesst(woerter[1:])) is not None:
            nummern.append(n)
    return nummern


def hook_spec_sperre(strom: TextIO | None = None, fehler: TextIO | None = None) -> int:
    """PreToolUse-Hook (Bash) der Bau-Session: rohes ``gh issue close <Spec>`` ablehnen.

    Exit 2 + Hinweis auf ``to_spawn.py spec-schliessen <S>`` (stderr geht an Claude),
    sobald der Befehl eine Nummer schließt, für die :func:`befund.ist_spec` gilt — dieselbe
    Definition, die ``spec-schliessen`` nutzt. Alles andere: Exit 0. Ein Fehler im Hook
    lässt den Befehl durch (die Session darf nie hängen bleiben, #204).
    """
    try:
        daten = _eingabe(strom or sys.stdin)
        roh = daten.get("tool_input")
        befehl = roh.get("command") if isinstance(roh, dict) else None
        if not isinstance(befehl, str):
            return 0
        start = daten.get("cwd")
        repo = config.repo_wurzel(Path(start) if isinstance(start, str) and start else None)
        specs = [n for n in geschlossene_nummern(befehl) if befund.ist_spec(repo, n)]
    except Exception:  # der Hook darf die Bau-Session nie stören
        log.exception("Spec-Sperre fehlgeschlagen — Befehl wird nicht geprüft.")
        return 0
    if not specs:
        return 0
    s = specs[0]
    (fehler or sys.stderr).write(
        f"Gesperrt: #{s} ist eine Spec — nie roh mit `gh issue close` schließen. "
        f"Stattdessen `python {SKILL_CLI} spec-schliessen {s}`: prüft die Folge-Tickets "
        "und schließt nur, wenn alle gebaut, geschlossen oder gebündelt sind.\n"
    )
    return 2


# --- Push-Prüfer-Hook (#865 / App #1396): Schließ-Prüfer vor git push auf ticket/* ---------------

#: Globale ``git``-Schalter vor dem Unterbefehl, die einen eigenen Wert erwarten.
_GIT_GLOBAL_MIT_WERT = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}
)
#: ``git push``-Schalter mit eigenem Wert (der Wert ist kein Remote und keine Refspec).
_GIT_PUSH_MIT_WERT = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})
#: Ticket-Zweig ``ticket/<N>…``: erste Zahlengruppe ist die Ticketnummer.
_TICKET_ZWEIG = re.compile(r"^ticket/(\d+)(?:\D|$)")


def _aktueller_zweig(repo: Path) -> str | None:
    """Aktueller Zweigname von ``repo`` (``git symbolic-ref --short HEAD``) oder ``None``."""
    code, name = schliess_pruefer._git(repo, "symbolic-ref", "--short", "HEAD")
    return name.strip() if code == 0 and name.strip() else None


def _git_push_ziele(woerter: list[str], cwd: Path) -> list[tuple[Path, str, str]]:
    """Liefert ``(arbeits_cwd, ziel_zweig, quelle_ref)`` für ``git push`` auf ``ticket/*``."""
    ziel_cwd, i = cwd, 1
    while i < len(woerter) and woerter[i].startswith("-"):
        arg, i = woerter[i], i + 1
        if arg == "-C" and i < len(woerter):
            ziel_cwd, i = (ziel_cwd / woerter[i]).resolve(), i + 1
        elif arg.startswith("-C") and len(arg) > 2:
            ziel_cwd = (ziel_cwd / arg[2:]).resolve()
        elif arg in _GIT_GLOBAL_MIT_WERT and i < len(woerter):
            i += 1
    if i >= len(woerter) or woerter[i] != "push":
        return []
    pos: list[str] = []
    ende_schalter = sammel = ueberspringen = False
    for arg in woerter[i + 1 :]:
        if ueberspringen:
            ueberspringen = False
        elif ende_schalter:
            pos.append(arg)
        elif arg == "--":
            ende_schalter = True
        elif arg in ("--delete", "--dry-run") or (
            arg.startswith("-") and not arg.startswith("--") and ("d" in arg[1:] or "n" in arg[1:])
        ):
            return []
        elif arg in ("--all", "--mirror", "--tags"):
            sammel = True
        elif arg in _GIT_PUSH_MIT_WERT:
            ueberspringen = True
        elif not arg.startswith("-"):
            pos.append(arg)
    treffer: list[tuple[Path, str, str]] = []
    if len(pos) >= 2:
        for refspec in pos[1:]:
            roh = refspec.lstrip("+")
            if not roh or roh.startswith(":"):
                continue
            quelle, ziel = roh.split(":", 1) if ":" in roh else (roh, roh)
            ziel_kurz = ziel.removeprefix("refs/heads/")
            if ziel_kurz == "HEAD":
                ziel_kurz = _aktueller_zweig(ziel_cwd) or ""
            if ziel_kurz.startswith("ticket/"):
                quelle_ref = (
                    quelle.removeprefix("refs/heads/") if quelle.startswith("refs/heads/") else (quelle or "HEAD")
                )
                treffer.append((ziel_cwd, ziel_kurz, quelle_ref))
    elif not sammel:
        aktuell = _aktueller_zweig(ziel_cwd)
        if aktuell and aktuell.startswith("ticket/"):
            treffer.append((ziel_cwd, aktuell, "HEAD"))
    return treffer


def push_pruef_ziele(befehl: str, cwd: Path) -> list[tuple[Path, str, str]]:
    """Alle ``git push``-Ziele auf ``ticket/*`` in ``befehl`` (inkl. ``cd``, ``eval``, ``bash -c``)."""
    ziele: list[tuple[Path, str, str]] = []
    aktuell_cwd = cwd
    for woerter in _einfache_befehle(befehl):
        name = _name(woerter[0])
        if name == "cd":
            ziel = woerter[2] if len(woerter) >= 3 and woerter[1] == "--" else (woerter[1] if len(woerter) >= 2 else "")
            if not ziel:
                aktuell_cwd = Path.home()
            elif not ziel.startswith("-"):
                aktuell_cwd = (aktuell_cwd / ziel).resolve()
        elif name == "eval":
            ziele += push_pruef_ziele(" ".join(woerter[1:]), aktuell_cwd)
        elif name in _SHELLS and (text := _shell_text(woerter[1:])) is not None:
            ziele += push_pruef_ziele(text, aktuell_cwd)
        elif name == "git":
            ziele += _git_push_ziele(woerter, aktuell_cwd)
    return ziele


def hook_push_pruefer(strom: TextIO | None = None, fehler: TextIO | None = None) -> int:
    """PreToolUse-Hook (Bash) der Bau-Session: Schließ-Prüfer vor ``git push`` auf ``ticket/*`` (#865).

    Delegiert ausschließlich an :func:`schliess_pruefer.pruefe` (#864) mit ``gruen_pruefen=False``
    (vor dem Push kann der Commit noch nicht grün auf dem Hauptzweig sein). Bei Ablehnung: Exit 2
    und konkrete Gründe auf stderr; bei anderen Befehlen, anderen Zweigen oder Hook-Fehlern: Exit 0.
    """
    try:
        daten = _eingabe(strom or sys.stdin)
        werkzeug = daten.get("tool_name")
        if isinstance(werkzeug, str) and werkzeug != "Bash":
            return 0
        roh = daten.get("tool_input")
        befehl = roh.get("command") if isinstance(roh, dict) else None
        if not isinstance(befehl, str):
            return 0
        start = daten.get("cwd")
        basis = Path(start) if isinstance(start, str) and start else Path.cwd()
        ziele = push_pruef_ziele(befehl, basis)
        if not ziele:
            return 0
        maengel: list[tuple[str, int, list[schliess_pruefer.Grund]]] = []
        for arbeits_cwd, zweig, quelle_ref in ziele:
            treffer = _TICKET_ZWEIG.match(zweig)
            env_ticket = os.environ.get("TO_SPAWN_TICKET", "")
            ticket = int(treffer.group(1)) if treffer else (int(env_ticket) if env_ticket.isdigit() else None)
            if ticket is None:
                continue
            repo = config.repo_wurzel(arbeits_cwd)
            ergebnis = schliess_pruefer.pruefe(
                schliess_pruefer.Stand(repo=repo, ticket=ticket, ref=quelle_ref, gruen_pruefen=False)
            )
            if not ergebnis.ok:
                maengel.append((zweig, ticket, ergebnis.gruende))
    except Exception:  # der Hook darf die Bau-Session nie hängen lassen (#204)
        log.exception("Push-Prüfer-Hook fehlgeschlagen — Befehl wird nicht blockiert.")
        return 0
    if not maengel:
        return 0
    aus = fehler or sys.stderr
    for zweig, ticket, gruende in maengel:
        aus.write(f"Push auf {zweig} (#{ticket}) vom Schließ-Prüfer abgelehnt:\n")
        for grund in gruende:
            aus.write(f"- {grund.regel}: {grund.text}\n")
    return 2


# --- Ja-Sperre (#531): das Live-Ja kommt nur vom Menschen, nie aus einem Werkzeug-Aufruf ---------

#: Unterbefehle von ``to_spawn.py``, die das Ja des Nutzers schreiben (``ja`` = Kurzform, #531).
JA_UNTERBEFEHLE = {"ja", "nutzer-antwort"}


def ja_aufrufe(befehl: str) -> bool:
    """True, wenn ``befehl`` das Ja des Nutzers schreiben würde — die eine Stelle des Musters (#531).

    Erkannt: ``ja``/``ja.cmd`` als Befehl, ``nutzer-antwort`` als Wort, ``to_spawn.py ja`` (auch mit
    ``python`` davor); ``eval …``, ``bash -c "…"``, ``powershell``/``pwsh``/``cmd`` (auch
    ``-EncodedCommand``) werden ausgepackt.
    Positivliste (#591, #538): Nur Befehle aus :data:`_JA_NUR_TEXT` (``echo``, ``grep`` …) dürfen
    ``ja`` als Wort tragen; jeder andere Befehl mit dem Wort (auch in Zuweisung, Schalter-Wert oder
    Text wie ``watch 'ja 5'``) sperrt — so braucht kein Starter (``ionice``, ``flock``, ``ksh`` …) eine
    eigene Liste. Interpreter (:data:`_JA_INTERPRETER`) und dynamische Befehlsnamen (``$(…)``,
    ``$X``) lesen ihr Programm aus Text, den die Prüfung nicht sieht: dort sperrt das Wort irgendwo im
    ganzen Befehl. Groß/klein zählt nicht (Windows), ``$'…'`` wird wie von bash aufgelöst, Klammern
    und Glob im Befehlsnamen werden gegen ``ja`` geprüft.
    Fluss-Regel (PR 610): was ein Teil mit dem Wort ausgibt, gilt beim Empfänger selbst als das Wort —
    jede folgende Pipe-Stufe, ``xargs``-Ziel, ``$(…)``/Backtick/``<(…)``-Ersetzung und ein gequoteter
    Here-Doc-Rumpf geben es an genau den Befehl weiter, der sie liest; der muss dann ein Text-Befehl
    sein. Here-Doc-Rümpfe und ``#``-Kommentare sind nie Befehle. Zerlegung unsicher (offene Quote,
    offener Here-Doc, zu tief verschachtelt) → das Wort irgendwo im Text sperrt.
    Bewusst breit und ohne Ausnahmen; die zwei Positivlisten für das Bash-Werkzeug (#567) prüft
    :func:`hook_ja_sperre` vorher (:func:`_bash_text_ohne_aufruf`).
    ponytail: nur wörtliche Aufrufe — Variablen, ein Skript, kodierter Text (``printf '\\x6a…' | sh``)
    oder ein direkter Schreibzugriff auf die Briefkasten-Datei umgehen die Sperre (gleiche Grenze wie
    ``hook_spec_sperre``); Upgrade-Pfad: Prüfung im Briefkasten selbst über einen Nachweis, den nur der
    Nutzer-Weg trägt.
    """
    befehl = _JA_ANSI_C.sub(_ansi_c_aufloesen, befehl).replace('$"', '"')
    if _JA_WIN_PFAD.search(befehl):  # Windows-Pfad: shlex verschluckt die Backslashes
        return True
    # Textsuche (Interpreter, unsichere Zerlegung) sieht beide Formen: ``\``+Zeilenumbruch ist in
    # Python-/Shell-Text eine Fortsetzung, ``\\``+Zeilenumbruch nicht (PR 610, Runde 4).
    suchtext = befehl + "\n" + befehl.replace("\\\n", "")
    try:
        alle: list[_JaTeil] = []
        teile, _ = _ja_zerlegen(befehl, alle=alle)
        return (
            not _ja_rumpf_ok(teile, ersetzung=False)
            or _ja_teile_sperren(teile, suchtext)
            or _ja_traeger_sperren(alle, teile)
        )
    except _JaTreffer:
        return True
    except _JaUnsicher as grund:
        log.info("Ja-Sperre: Zerlegung unsicher (%s) — Wort im Text entscheidet.", grund)
        return _ja_traegt(suchtext)


class _JaUnsicher(Exception):
    """Die Zerlegung ist nicht sicher (offene Quote, offener Here-Doc, zu tief verschachtelt)."""


class _JaTreffer(Exception):
    """Ein innerer Teil (Befehls-Ersetzung, Here-Doc ohne gequoteten Begrenzer) schreibt das Ja."""


class _JaTeil:
    """Ein einfacher Befehl: Trenner davor, Text (Ersetzungen schon durch Platzhalter ersetzt) und ob
    seine Eingabe (Here-Doc-Rumpf) das Wort trägt."""

    __slots__ = ("eingabe", "text", "trenner")

    def __init__(self, trenner: str, text: str) -> None:
        self.trenner, self.text, self.eingabe = trenner, text, False


#: Grenze für verschachtelte Ersetzungen (``$($($(…)))``) — darüber gilt die Zerlegung als unsicher.
_JA_MAX_TIEFE = 20
#: Zeichen, nach denen ein ``#`` einen Kommentar beginnt (Wortanfang).
_JA_WORTANFANG = frozenset(" \t\n;&|()<>")


def _ja_traegt(text: str) -> bool:
    """Text trägt das Wort (``ja``, ``nutzer-antwort`` oder ein Windows-Pfad auf ``ja``)."""
    return _ja_im_text(text) or "nutzer-antwort" in text.lower() or bool(_JA_WIN_PFAD.search(text))


def _ja_zerlegen(
    text: str, i: int = 0, ende: str = "", tiefe: int = 0, alle: list[_JaTeil] | None = None
) -> tuple[list[_JaTeil], int]:
    """``text`` ab ``i`` in einfache Befehle, bis ``ende`` (``)``/Backtick) oder Textende.

    Wie die Shell: Trenner nur außerhalb von Quotes; ``$(…)``, Backticks und ``<(…)``/``>(…)``
    werden ausgewertet (:func:`_ja_teile_sperren` auf den Inhalt, Treffer → :class:`_JaTreffer`) und durch
    den Platzhalter ``$ja`` (Inhalt trägt das Wort) bzw. ``$x`` ersetzt — so sieht der Empfänger ihre
    Ausgabe an genau der Stelle, an der sie landet. ``#`` am Wortanfang bis Zeilenende ist Kommentar.
    Here-Doc-Rümpfe sind nie Befehle: gequoteter Begrenzer → Eingabe des Befehls mit ``<<``,
    ungequotet (die Shell ersetzt im Rumpf) und das Wort darin → :class:`_JaTreffer`.
    Liefert die Teile und den Index hinter ``ende``.
    """
    if tiefe > _JA_MAX_TIEFE:
        raise _JaUnsicher("zu tief verschachtelt")
    teile: list[_JaTeil] = []
    offen: list[tuple[int, str, bool, bool]] = []  # Here-Docs: (Teil, Begrenzer, gequotet, <<-)
    puffer: list[str] = []
    trenner, quote, klammern = "", "", 0

    def abschliessen(neu: str) -> None:
        nonlocal trenner
        teile.append(_JaTeil(trenner, "".join(puffer)))
        puffer.clear()
        trenner = neu

    while i < len(text):
        zeichen = text[i]
        if quote == "'":
            quote = "" if zeichen == "'" else quote
            puffer.append(zeichen)
            i += 1
            continue
        if zeichen == "\\":
            if text.startswith("\\\n", i):  # Zeilenfortsetzung; ``\\`` ist schon als Paar verbraucht
                i += 2
                continue
            puffer.append(text[i : i + 2])
            i += 2
            continue
        if not quote and ende and zeichen == ende and not (ende == ")" and klammern):
            if offen:
                raise _JaUnsicher("Here-Doc ohne Endzeile vor Ersetzungsende")
            abschliessen("")
            return teile, i + 1
        if zeichen == "`" or text.startswith("$(", i) or (not quote and text.startswith(("<(", ">("), i)):
            innen = i + (1 if zeichen == "`" else 2)
            innere, weiter = _ja_zerlegen(text, innen, "`" if zeichen == "`" else ")", tiefe + 1, alle)
            inhalt = text[innen : weiter - 1]
            if not _ja_rumpf_ok(innere, ersetzung=True) or _ja_teile_sperren(innere, inhalt):
                raise _JaTreffer
            if alle is not None and not any(teil.eingabe for teil in innere):
                alle.extend(innere)  # reines ``$(cat <<'WORT' …)``: Wert, der Empfänger entscheidet
            platzhalter = "$ja" if _ja_traegt(inhalt) else "$x"
            puffer.append(f" {platzhalter} " if zeichen in "<>" else platzhalter)
            i = weiter
            continue
        if quote == '"':
            quote = "" if zeichen == '"' else quote
        elif zeichen in "'\"":
            quote = zeichen
        elif zeichen == "#" and (i == 0 or text[i - 1] in _JA_WORTANFANG):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("<<<", i):
            puffer.append(" <<< ")
            i += 3
            continue
        elif text.startswith("<<", i):
            i = _ja_heredoc_kopf(text, i + 2, offen, len(teile))
            puffer.append(" ")
            continue
        elif zeichen == "&" and puffer and puffer[-1] in ("<", ">"):
            puffer.append(zeichen)  # ``2>&1``, ``>&2``, ``<&3``: Deskriptor-Kopie, kein Trenner (Runde 5)
            i += 1
            continue
        elif zeichen == ">":
            vor = "".join(puffer).rsplit(None, 1)[-1] if puffer and puffer[-1] not in " \t" else ""
            if vor and vor[-1] not in "<>&" and not vor.isdigit():
                puffer.append(" ")
            puffer.append(zeichen)
            i += 1
            continue
        elif zeichen in _TRENNER:
            if zeichen == "(" or (zeichen == ")" and klammern):
                klammern += 1 if zeichen == "(" else -1
            schritt = 2 if zeichen in "|&" and text.startswith(zeichen * 2, i) else 1
            abschliessen(text[i : i + schritt])
            i += schritt
            if zeichen == "\n" and offen:
                i = _ja_heredoc_rumpf(text, i, offen, teile, ersetzung=bool(ende))
            continue
        puffer.append(zeichen)
        i += 1
    if quote or ende or offen:
        raise _JaUnsicher("offene Quote, Ersetzung oder Here-Doc")
    abschliessen("")
    return teile, i


def _ja_heredoc_kopf(text: str, i: int, offen: list[tuple[int, str, bool, bool]], teil: int) -> int:
    """Begrenzer hinter ``<<`` lesen (``-``, Quotes, Backslash = gequotet) und vormerken."""
    tabs = text.startswith("-", i)
    i += tabs
    while i < len(text) and text[i] in " \t":
        i += 1
    wort, gequotet = [], False
    while i < len(text) and text[i] not in _JA_WORTANFANG:
        if text[i] in "'\"":
            schluss = text.find(text[i], i + 1)
            if schluss < 0:
                raise _JaUnsicher("Here-Doc-Begrenzer mit offener Quote")
            wort.append(text[i + 1 : schluss])
            gequotet, i = True, schluss + 1
        elif text[i] == "\\":
            wort.append(text[i + 1 : i + 2])
            gequotet, i = True, i + 2
        else:
            wort.append(text[i])
            i += 1
    if not "".join(wort):
        raise _JaUnsicher("Here-Doc ohne Begrenzer")
    offen.append((teil, "".join(wort), gequotet, tabs))
    return i


def _ja_heredoc_rumpf(
    text: str, i: int, offen: list[tuple[int, str, bool, bool]], teile: list[_JaTeil], *, ersetzung: bool = False
) -> int:
    """Rümpfe der vorgemerkten Here-Docs ab ``i`` überspringen; Wort im Rumpf → Eingabe trägt es.

    In einer Ersetzung beendet bash den Rumpf auch an ``WORT)`` (PR 610, Runde 3) — beginnt dort eine
    Rumpf-Zeile mit dem Begrenzer, ohne ihm genau zu gleichen, ist das Ende nicht eindeutig → unsicher.
    Backslash+Zeilenumbruch (Runde 4): nur bei ungequotetem Begrenzer zieht bash die Zeilen zusammen; in
    einer Ersetzung gilt eine Rumpf-Zeile mit Backslash am Ende als unsicher."""

    def naechste(i: int) -> tuple[str, int]:
        umbruch = text.find("\n", i)
        return (text[i:], len(text)) if umbruch < 0 else (text[i:umbruch], umbruch + 1)

    for teil, wort, gequotet, tabs in offen:
        zeilen: list[str] = []
        while True:
            if i >= len(text):
                raise _JaUnsicher("Here-Doc ohne Endzeile")
            zeile, i = naechste(i)
            while not gequotet and (len(zeile) - len(zeile.rstrip("\\"))) % 2 and i < len(text):
                folge, i = naechste(i)
                zeile = zeile[:-1] + folge
            if ersetzung and zeile.endswith("\\"):
                raise _JaUnsicher("Here-Doc-Zeile mit Backslash am Ende in Ersetzung")
            vergleich = zeile.lstrip("\t") if tabs else zeile
            if vergleich == wort:
                break
            if ersetzung and vergleich.startswith(wort):
                raise _JaUnsicher("Here-Doc-Ende in Ersetzung nicht eindeutig")
            zeilen.append(zeile)
        if _ja_traegt("\n".join(zeilen)):
            if not gequotet:
                raise _JaTreffer  # die Shell ersetzt im Rumpf — nie nur Text
            teile[teil].eingabe = True
    offen.clear()
    return i


def _ja_rumpf_ok(teile: list[_JaTeil], *, ersetzung: bool) -> bool:
    """Darf ein Here-Doc-Rumpf mit dem Wort hier stehen? Ohne solchen Rumpf immer ja.

    Direkt im Befehl (``ersetzung=False``) nie: die einzige freie Form ``cat > datei <<'WORT'`` als
    ganzer Befehl prüft schon die Positivliste (:func:`_nur_datei_heredoc`), alles andere drumherum
    (``cd d && …``, ``tee``, ``<<-``, ``> $HOME/…``) bleibt gesperrt (#567). In einer Ersetzung
    (``$(cat <<'EOF' …)``) nur, wenn sie genau aus dem einen Befehl ``cat`` besteht — dann ist ihre
    Ausgabe der Rumpf, und die Fluss-Regel prüft den Empfänger (Text-Wert von ``git commit -m`` …).
    """
    if not any(teil.eingabe for teil in teile):
        return True
    if not ersetzung:
        return False
    try:
        befehle = [woerter for teil in teile if (woerter := shlex.split(teil.text))]
    except ValueError as fehler:
        raise _JaUnsicher("shlex") from fehler
    return befehle == [["cat"]]


def _ja_teile_sperren(teile: list[_JaTeil], befehl: str) -> bool:
    """Fluss-Regel über die Teile: trägt ein Teil das Wort (in seinen Wörtern oder seiner Eingabe),
    bekommt die nächste Stufe hinter ``|``/``|&`` das Wort als zusätzliches Argument und wird damit
    wie jeder Befehl mit dem Wort geprüft. Teile ohne Befehl (``}``, ``)``) unterbrechen den Fluss nicht."""
    fluss, trenner = False, []
    for teil in teile:
        trenner.append(teil.trenner)
        try:
            roh = shlex.split(teil.text)
        except ValueError as fehler:
            raise _JaUnsicher("shlex") from fehler
        woerter = _befehlskern(roh)
        if any(_ja_wort_in(w) for w in roh[: len(roh) - len(woerter)]):
            return True  # ``command -p ja``, ``env X=ja …``: Wort im Präfix-Teil
        if not woerter:  # ``|&``, ``}``, ``)``: kein Befehl, der Fluss läuft weiter
            fluss = fluss or teil.eingabe
            continue
        zufluss = teil.eingabe or (fluss and "|" in trenner)
        kern_roh = _befehlskern(teil.text.split())
        roh_befehl = kern_roh[0].strip("'\"") if kern_roh and "\\" in kern_roh[0] else ""
        if _ja_im_einfachen(woerter + (["ja"] if zufluss else []), befehl, roh_befehl=roh_befehl):
            return True
        fluss, trenner = zufluss or any(_ja_wort_in(w) for w in roh), []
    return False


def _ja_ist_relatives_ziel(ziel: str) -> bool:
    """Datei-Schreibziel relativ zum aktuellen Ordner (kein ``/dev/null``, kein ``>&2``, kein absoluter Pfad)."""
    if _JA_KEINE_DATEI.fullmatch(ziel):
        return False
    return not (ziel.startswith(("/", "~/")) or bool(re.match(r"^[A-Za-z]:[/\\]", ziel)))


def _ja_ist_text_git(roh: list[str]) -> bool:
    """``git``/``gh``-Text-Unterbefehl (:data:`_JA_TEXT_UNTERBEFEHLE`) ohne Präfix — nur der darf
    seine eigene Ausgabe in eine ungefährliche Datei umleiten (#621)."""
    woerter, _, _ = _ja_umleitungen(roh)
    if not woerter or woerter[0] not in ("git", "gh"):
        return False
    return _ja_unterbefehl(woerter, woerter[0])[0] in _JA_TEXT_UNTERBEFEHLE


def _ja_traeger_sperren(innere: list[_JaTeil], teile: list[_JaTeil]) -> bool:
    """Träger-Regel (PR 610, Runde 2): Gibt ein Teil das Wort aus oder schreibt es (:func:`_ja_traeger`),
    muss JEDER Teil des ganzen Befehls frei sein (:func:`_ja_teil_frei`) — gleich, wie er verbunden ist
    (Pipe, ``;``, ``&&``, ``&``, Gruppe, Schleife, Subshell, Prozess-Ersetzung). So braucht kein Weg der
    Ausgabe zu einem ausführenden Programm (Umleitung in eine Datei, Deskriptor, FIFO, Gruppe) eine eigene
    Erkennung. ``innere`` = Teile aus Ersetzungen (``$(…)``, Backticks, ``<(…)``); ein reines
    ``$(cat <<'WORT' …)`` fehlt darin — seine Ausgabe prüft der Empfänger über den Platzhalter.

    Wert-Teil (Runde 3): steht das Wort nur in Text-Werten (``git commit -m``), ist der Teil selbst frei,
    aber jeder andere Teil muss es auch sein — sonst liest ihn ``git log … | awk`` zurück und führt ihn aus."""
    alle = innere + teile
    zerlegt: list[tuple[_JaTeil, list[str]]] = []
    for teil in alle:
        try:
            roh = shlex.split(teil.text)
        except ValueError as fehler:
            raise _JaUnsicher("shlex") from fehler
        if roh or teil.eingabe:
            zerlegt.append((teil, roh))
    strikt = any(_ja_traeger(roh, teil.eingabe) for teil, roh in zerlegt)
    if not strikt and not any(_ja_wort_in(w) for _, roh in zerlegt for w in roh):
        return False
    # Nach ``cd`` ist jedes relative Schreib-Ziel mehrdeutig (z. B. ``cd .git && … >> config``).
    hat_cd = any((woerter := _ja_umleitungen(roh)[0]) and woerter[0] == "cd" for _, roh in zerlegt)
    if hat_cd and any(_ja_ist_relatives_ziel(z) for _, roh in zerlegt for z in _ja_umleitungen(roh)[2]):
        return True
    # Runde 4/5: schreibt ein Teil in eine Datei, kann sie Konfiguration für git/gh sein (``b/config``).
    # #621: nur eine einzelne eigene Ausgabe-Umleitung eines git/gh-Text-Unterbefehls
    # (``git log … > ~/tmp-bau/x.txt``) ohne Ersetzungen (``innere``), ohne Schleife und ohne ``&``/``|``
    # bleibt frei; jeder andere Fall bleibt fail-closed wie main.
    schreib_indices = [idx for idx, (_, roh) in enumerate(zerlegt) if _ja_schreibt(roh)]
    unsichere_folge = (
        bool(innere)
        or any(teil.trenner in _JA_NEBENLAEUFIG for teil in alle)
        or any(w in _JA_SCHLEIFE for _, roh in zerlegt for w in roh)
    )
    for idx, (_, roh) in enumerate(zerlegt):
        eigene_umleitung = not unsichere_folge and schreib_indices == [idx] and _ja_ist_text_git(roh)
        schreibt_hier = bool(schreib_indices) and not eigene_umleitung
        if not (_ja_teil_frei(roh, schreibt=schreibt_hier) or (not strikt and _ja_wert_teil(roh))):
            return True
    return False


def _ja_wert_teil(roh: list[str]) -> bool:
    """``git``/``gh``-Unterbefehl aus :data:`_JA_TEXT_WERTE`, wörtlich ohne Präfix davor."""
    name = roh[0] if roh else ""
    return name in ("git", "gh") and _befehlskern(roh) == roh and _ja_unterbefehl(roh, name)[0] in _JA_TEXT_WERTE


def _ja_traeger(roh: list[str], eingabe: bool) -> bool:
    """Der Teil kann das Wort ausgeben oder schreiben: es steht in seinen Wörtern oder seiner Eingabe.
    Ausnahme Wert-Teil: bei ``git commit -m``, ``gh pr create --body`` … (:data:`_JA_TEXT_WERTE`)
    zählt das Wort nur außerhalb der Text-Werte."""
    if eingabe:
        return True
    kern = _befehlskern(roh)
    name = _name(kern[0]).lower() if kern else ""
    if name in ("git", "gh") and _ja_unterbefehl(kern, name)[0] in _JA_TEXT_WERTE:
        roh = roh[: len(roh) - len(kern)] + _ja_ohne_text(kern, name)
    return any(_ja_wort_in(w) for w in roh)


#: Befehle, die in einem Befehl mit Träger frei sind, obwohl sie kein Text-Befehl sind: sie führen nichts aus.
_JA_HARMLOS = {"cd", "pwd", "true", "false", ":", "mkdir", "test", "["}
#: Shell-Schlüsselwörter, die nur Gruppen/Bedingungen öffnen oder schließen — der Befehl dahinter zählt.
#: ``for``, ``case``, ``select``, ``function``, ``exec``, ``coproc`` fehlen bewusst: dort sperrt der Teil.
_JA_KLAMMERWORTE = {"{", "}", "!", "do", "done", "then", "else", "elif", "fi", "if", "while", "until", "time"}
#: Schlüsselwörter für Schleifen — dort kann ein späterer Schreib-Teil vor einem früheren git/gh laufen (#621).
_JA_SCHLEIFE = {"while", "until", "do", "done"}
#: Trenner für nebenläufige Ausführung — ein späterer Schreib-Teil kann mit git/gh rennen (#621).
_JA_NEBENLAEUFIG = {"&", "|"}
#: Umleitungs-Wort (``>``, ``>>f``, ``2>``, ``&>``, ``<<<`` …); Gruppe 1 = angeklebtes Ziel.
_JA_UMLEITUNG = re.compile(r"(?:\d+|&)?(?:>>|>\||>&|<&|<<<|<>|>|<)(.*)", re.S)
#: Umleitung auf einen eigenen Deskriptor (``3>``, ``>&4``) — Weg zu ``exec``/``coproc``-Empfängern.
_JA_DESKRIPTOR = re.compile(r"(?:^|\W)(?:[3-9]|[1-9]\d+)[<>]|[<>]&-?(?:[3-9]|[1-9]\d+)")
#: Dateien, die später von selbst laufen (Shell-Start, Git-Hooks, PowerShell-Profil).
_JA_STARTDATEIEN = {
    *(".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".profile"),
    *(".zshrc", ".zshenv", ".zprofile", ".zlogin"),
}
#: xargs-Schalter ohne Wert, mit Wert als nächstes Wort oder angeklebt, lang mit ``=Wert``.
_JA_XARGS_OHNE_WERT = {"-0", "-r", "-t", "-x", "--null", "--no-run-if-empty", "--verbose", "--exit"}
_JA_XARGS_MIT_WERT = {"-I", "-n", "-L", "-P", "-s", "-d", "-E", "-a"}
_JA_XARGS_LANG = {"--max-args", "--max-lines", "--max-procs", "--max-chars", "--delimiter", "--arg-file", "--replace"}
_JA_FIND_SCHREIBT = ("-fprint", "-fls")
#: Git-/gh-Konfiguration: ``textconv``, ``core.pager``, Attribute starten Programme beim nächsten git/gh-Befehl.
#: Auch nackte Dateinamen (``config``, ``config.yml``, ``attributes``, ``HEAD``) für Arbeitsordner ``.git``/``gh``.
_JA_GIT_DATEIEN = {".gitconfig", ".gitattributes", "config", "config.yml", "hosts.yml", "attributes", "head"}
#: Git-Hook-Namen — verhindern Schreiben bei Arbeitsordner ``.git/hooks`` (#621).
_JA_GIT_HOOKS = {
    *("applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit"),
    *("prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge"),
    *("pre-push", "pre-receive", "update", "proc-receive", "post-receive", "post-update"),
    *("reference-transaction", "push-to-checkout", "pre-auto-gc", "post-rewrite"),
    *("sendemail-validate", "fsmonitor-watchman", "post-index-change"),
}
#: Schreib-Ziel, das erst die Shell festlegt (Glob, Klammer, Variable, Ersetzung, ``~user``/``~+``).
_JA_ZIEL_ERWEITERUNG = re.compile(r"[*?\[{$`]|^~(?!/|$)")
#: git-Unterbefehle ohne Programmstart, frei neben einem Wert-Teil (Runde 3).
_JA_GIT_FREI = {("git", "add"), ("git", "push"), ("git", "status"), ("git", "fetch")}
#: Lange Schalter, die ein Programm starten — auch abgekürzt (``--up=sh``), git nimmt eindeutige Präfixe.
_JA_GIT_STARTER = ("--upload-pack", "--receive-pack", "--exec")
#: Shell-Erweiterungen oder Remote-Helper-Trenner ``::`` in git-Argumenten (fail-closed, #621).
_JA_GIT_UNSICHER = re.compile(r"[$`{]|::")
#: Für ``git fetch``/``push`` sperren auch Globs (können zu ``ext::`` oder Schaltern erweitern, #621).
_JA_GIT_GLOB = re.compile(r"[*?\[]")
#: Schalter mit Shell-Erweiterung (``--ou${X}tput=…``, ``{-w,5}``) — fail-closed (#621).
_JA_SCHALTER_ERWEITERUNG = re.compile(r"[$`{*?\[]")


def _ja_startdatei(pfad: str) -> bool:
    """``pfad`` (schon ohne Quotes) ist eine Datei, die später von selbst läuft oder git/gh steuert;
    normalisiert (``.git//hooks``, ``a/../.bashrc``)."""
    pfad = posixpath.normpath(pfad.replace("\\", "/")).lower()
    basis = _ja_basis(pfad)
    return (
        basis in _JA_STARTDATEIEN
        or basis in _JA_GIT_DATEIEN
        or basis in _JA_GIT_HOOKS
        or pfad.endswith("profile.ps1")
        or "/.git/" in f"/{pfad}/"
        or "/.config/git/" in f"/{pfad}/"
        or "/.config/gh/" in f"/{pfad}/"
    )


def _ja_git_frei(woerter: list[str], unter: tuple[str, ...]) -> bool:
    """``git add|push|status|fetch`` ohne das Wort, ohne Programm-Schalter (:data:`_JA_GIT_STARTER`),
    ohne ``::``-Remote-Helper (``ext::`` …) und ohne Shell-Erweiterungen (fail-closed, #621)."""
    netz = unter in (("git", "fetch"), ("git", "push"))
    for wort in woerter:
        lang, _, wert = wort.partition("=")
        lang_klein = lang.lower()
        if (
            _ja_wort_in(wort)
            or (len(lang_klein) > 2 and any(s.startswith(lang_klein) for s in _JA_GIT_STARTER))
            or _JA_GIT_UNSICHER.search(wort)
            or (netz and _JA_GIT_GLOB.search(wort))
            or lang_klein.startswith("ext::")
            or wert.lower().startswith("ext::")
        ):
            return False
    return True


def _positions_args(args: list[str], *, minus_zaehlt: bool = False) -> list[str]:
    """Datei-/Positions-Argumente: hinter ``--`` zählt jedes Wort, auch mit führendem ``-`` (#621)."""
    ergebnis: list[str] = []
    nach_trenner = False
    for wort in args:
        if not nach_trenner and wort == "--":
            nach_trenner = True
        elif nach_trenner or not wort.startswith("-") or (minus_zaehlt and wort == "-"):
            ergebnis.append(wort)
    return ergebnis


def _ja_umleitungen(roh: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Wörter ohne Umleitungen und Klammerworte, alle Umleitungs-Ziele, Schreib-Ziele (auch ``tee``)."""
    woerter, ziele, schreib_ziele, k = [], [], [], 0
    while k < len(roh):
        wort = roh[k]
        if ">" in wort:
            rest = wort.rsplit(">", 1)[1].lstrip("|")
            ziele.append(rest or (roh[k + 1] if k + 1 < len(roh) else ""))
        if umleitung := _JA_UMLEITUNG.fullmatch(wort):
            if ">" in wort:
                schreib_ziele.append(ziele[-1])
            k += 1 if umleitung.group(1) else 2
            continue
        woerter.append(wort)
        k += 1
    while woerter and woerter[0] in _JA_KLAMMERWORTE:
        woerter = woerter[1:]
    if woerter and woerter[0] == "tee":
        schreib_ziele += _positions_args(woerter[1:])
    return woerter, ziele, schreib_ziele


#: Schreib-Ziel ohne Datei: Deskriptor-Kopie (``2>&1``, ``>&-``) oder ``/dev/null``.
_JA_KEINE_DATEI = re.compile(r"&(?:\d+|-)|/dev/null")


def _ja_schreibt(roh: list[str]) -> bool:
    """Der Teil schreibt in eine Datei (Umleitung oder ``tee``) — ohne Deskriptor-Kopien und ``/dev/null``."""
    return any(not _JA_KEINE_DATEI.fullmatch(z) for z in _ja_umleitungen(roh)[2])


def _ja_gh_web(woerter: list[str]) -> bool:
    """``gh … --web``/``-w`` (auch gebündelt oder mit Shell-Erweiterung): startet den Browser."""
    return any(
        w == "--web"
        or w.startswith("--web=")
        or re.fullmatch(r"-[A-Za-z]*w[A-Za-z]*", w)
        or (w[:1] in ("-", "{") and _JA_SCHALTER_ERWEITERUNG.search(w))
        for w in woerter
    )


def _ja_teil_frei(roh: list[str], *, schreibt: bool = False) -> bool:
    """Positivliste für jeden Teil eines Befehls mit Träger: Text-Befehl (:data:`_JA_NUR_TEXT`, ``find``
    ohne Start/Schreib-Schalter, ``git log``/``git grep``/``gh … view|list`` ohne Ausgabe-Weg, ``xargs``
    mit lauter bekannten Schaltern und freiem Ziel) oder :data:`_JA_HARMLOS`. Befehlsname wörtlich
    (kein Pfad, keine Zuweisung davor). Nie frei: Deskriptor-Umleitung, Schreib-Ziel Startdatei oder
    mit Shell-Erweiterung (:data:`_JA_ZIEL_ERWEITERUNG`), ``printf -v``, ``uniq`` mit Ausgabe-Datei,
    ``gh … --web``. ``schreibt`` (ein Teil des Befehls schreibt in eine Datei): ``git``/``gh`` lesen
    Konfiguration aus Dateien und sind dann nie frei (Runde 4)."""
    if any(_JA_DESKRIPTOR.search(w) for w in roh):
        return False
    woerter, ziele, schreib_ziele = _ja_umleitungen(roh)
    if any(_ja_startdatei(z) for z in ziele + schreib_ziele) or any(
        _JA_ZIEL_ERWEITERUNG.search(z) for z in schreib_ziele
    ):
        return False
    if not woerter:
        return True
    name = woerter[0]
    if name == "printf" and len(woerter) > 1 and woerter[1][:1] in "-$`*?[{":
        return False  # ``printf -v 'x[$(…)]'``: bash wertet den Array-Index aus
    if name == "uniq" and len(_positions_args(woerter[1:], minus_zaehlt=True)) > 1:
        return False  # zweites Datei-Argument ist die Ausgabe-Datei
    if name == "rg":
        return not any(
            (w.startswith("--pre") and not w.startswith("--pre-")) or w.startswith("--hostname-bin") for w in woerter
        )
    if name in _JA_NUR_TEXT or name in _JA_HARMLOS:
        return True
    if name == "find":
        return not any(w in _JA_FIND_START or w.startswith(_JA_FIND_SCHREIBT) for w in woerter)
    if name == "xargs":
        return _ja_xargs_frei(woerter, schreibt=schreibt)
    if name in ("git", "gh"):
        if schreibt or (name == "gh" and _ja_gh_web(woerter)):
            return False
        unter, ab = _ja_unterbefehl(woerter, name)
        if unter in _JA_GIT_FREI:
            return _ja_git_frei(woerter, unter)
        return unter in _JA_TEXT_UNTERBEFEHLE and not _ja_git_ausgabe_weg(unter, woerter[ab:])
    return False


def _ja_xargs_frei(woerter: list[str], *, schreibt: bool = False) -> bool:
    """``xargs`` nur mit bekannten Schaltern; das Ziel (ohne Ziel: ``echo``) muss selbst frei sein.
    Ein unbekannter Schalter sperrt — sein Wert könnte sonst für das Ziel gehalten werden.
    Ziel ``tee`` sperrt (Runde 5): es schreibt in Dateien, die die Schreib-Prüfung hier nicht sieht."""
    i = 1
    while i < len(woerter) and woerter[i].startswith("-"):
        wort = woerter[i]
        if wort in _JA_XARGS_MIT_WERT:
            i += 2
        elif (
            wort in _JA_XARGS_OHNE_WERT
            or wort[:2] in _JA_XARGS_MIT_WERT
            or ("=" in wort and wort.split("=", 1)[0] in _JA_XARGS_LANG)
        ):
            i += 1
        else:
            return False
    return i >= len(woerter) or (woerter[i] != "tee" and _ja_teil_frei(woerter[i:], schreibt=schreibt))


def _ja_wort_in(wort: str) -> bool:
    """Das Wort in ``wort``, auch an eine Kurz-Schalter-Gruppe geklebt (``-x'ja 5'``)."""
    return bool(_JA_WORT.search(wort) or _JA_GEKLEBT.match(wort))


def _ja_im_einfachen(woerter: list[str], befehl: str, *, roh_befehl: str = "") -> bool:
    """Ein einfacher Befehl ab dem Befehlsnamen; ``befehl`` = ganzer Text für Interpreter-Fälle.
    ``roh_befehl`` erhält Windows-Pfade mit einfachem Backslash, die ``shlex.split`` sonst verliert."""
    name = _name(woerter[0].lower()).removesuffix(".cmd")
    namen = {name}
    if roh_befehl:
        namen.add(_name(roh_befehl.lower()).removesuffix(".cmd"))
    if "cmd" in namen and _JA_CMD.search(re.sub(r"['\"^]", "", befehl)):
        return True
    if "eval" in namen and ja_aufrufe(" ".join(woerter[1:])):
        return True
    if namen & _SHELLS and (text := _shell_text(woerter[1:])) is not None and ja_aufrufe(text):
        return True
    if namen & _JA_WIN_SHELLS and _ja_in_win_shell(woerter[1:]):
        return True
    if "rg" in namen and _rg_vorfilter_ja(woerter):
        return True
    if (
        _ist_ja_name(woerter[0])
        or (roh_befehl and _ist_ja_name(roh_befehl))
        or "nutzer-antwort" in woerter
        or (any(_name(w) == "to_spawn.py" for w in woerter[:3]) and JA_UNTERBEFEHLE & set(woerter))
        or _ja_als_argument(woerter)
    ):
        return True
    if (namen & _JA_INTERPRETER or _JA_DYNAMISCH.search(woerter[0])) and _ja_im_text(befehl):
        return True
    if "xargs" in namen:
        i = 1
        while i < len(woerter) and woerter[i].startswith("-"):
            i += 2 if woerter[i] in _JA_SCHALTER_MIT_WERT else 1
        return i < len(woerter) and _ja_im_einfachen(woerter[i:], befehl)
    if name in _JA_NUR_TEXT or (name == "find" and not _JA_FIND_START.intersection(woerter)):
        return False
    if name in ("git", "gh"):
        woerter = _ja_ohne_text(woerter, name)
    return any(_ja_wort_in(w) for w in woerter)


def _ja_ohne_text(woerter: list[str], name: str) -> list[str]:
    """``git``/``gh``-Wörter ohne reinen Text — nur für benannte Unterbefehle ohne Programmstart.

    :data:`_JA_TEXT_UNTERBEFEHLE`: alle Argumente sind Text (``git log --grep=…``, ``gh … view --jq``;
    ``git grep`` nur ohne ``-O``/``--o…``). :data:`_JA_TEXT_WERTE`: nur die Werte der Text-Schalter
    (``-m``, ``--body`` …, auch ``--body=…``, ``-mText``, ``-am``). Jeder andere Unterbefehl oder ein
    globaler Schalter davor (``git -c …``) wird ganz geprüft. Vor dem git-Unterbefehl zählen nur
    ``-C <ordner>`` und ``--no-pager``.
    """
    unter, ab = _ja_unterbefehl(woerter, name)
    rest = woerter[ab:]
    if unter in _JA_TEXT_UNTERBEFEHLE:
        return woerter if _ja_git_ausgabe_weg(unter, rest) else woerter[:ab]
    schalter = _JA_TEXT_WERTE.get(unter)
    if schalter is None:
        return woerter
    kurz = "".join(s[1] for s in schalter if not s.startswith("--"))
    uebrig, k = [], 0
    while k < len(rest):
        wort = rest[k]
        k += 1
        if wort in schalter or re.fullmatch(rf"-[A-Za-z]*[{kurz}]", wort):
            k += 1  # Wert ist das nächste Wort
        elif not (wort.split("=", 1)[0] in schalter or re.fullmatch(rf"-[A-Za-z]*[{kurz}].+", wort, re.S)):
            uebrig.append(wort)
    return woerter[:ab] + uebrig


def _ja_unterbefehl(woerter: list[str], name: str) -> tuple[tuple[str, ...], int]:
    """``git``/``gh``-Unterbefehl (``("git", "commit")``, ``("gh", "pr", "create")``) und der Index
    des ersten Worts dahinter. Vor dem git-Unterbefehl zählen nur ``-C <ordner>`` und ``--no-pager``."""
    i = 1
    while name == "git" and i < len(woerter) and woerter[i] in ("-C", "--no-pager"):
        i += 2 if woerter[i] == "-C" else 1
    unter = (name, *woerter[i : i + (1 if name == "git" else 2)])
    return unter, i + len(unter) - 1


def _ja_git_ausgabe_weg(unter: tuple[str, ...], rest: list[str]) -> bool:
    """Ein Text-Unterbefehl gibt seine Ausgabe an ein Programm oder eine Datei (``git grep -O``/
    ``--open-files-in-pager``, ``git log --output``, auch abgekürzt oder mit Shell-Erweiterung) —
    dann ist er kein Text-Befehl."""
    if any(w[:1] in ("-", "{") and _JA_SCHALTER_ERWEITERUNG.search(w) for w in rest):
        return True
    if unter == ("git", "grep"):
        return any(w.startswith("--o") or re.match(r"-[^-]*O", w) for w in rest)
    return unter[0] == "git" and any(w.startswith("--ou") for w in rest)


#: Befehle, deren Argumente nie ein Programm starten — nur dort darf ``ja`` als Wort stehen (#591).
#: ``rg --pre``/``--hostname-bin`` prüft :func:`_rg_vorfilter_ja` vorher; ``find`` gilt nur ohne
#: :data:`_JA_FIND_START`.
_JA_NUR_TEXT = {"echo", "printf", "grep", "rg", "cat", "ls", "tee", "head", "tail", "wc", "cut", "tr", "uniq"}
_JA_FIND_START = {"-exec", "-execdir", "-ok", "-okdir"}
#: Unterbefehle, deren Argumente alle Text sind (kein Schalter startet ein Programm).
_JA_TEXT_UNTERBEFEHLE = {
    ("git", "log"),
    ("git", "grep"),
    *((("gh", art, unter) for art in ("issue", "pr") for unter in ("view", "list"))),
}
#: Unterbefehle, bei denen nur die Werte dieser Schalter Text sind (Nachricht, Titel, Body).
_JA_TEXT_WERTE: dict[tuple[str, ...], frozenset[str]] = {
    ("git", "commit"): frozenset({"-m", "--message"}),
    ("git", "tag"): frozenset({"-m", "--message"}),
    **{
        ("gh", art, unter): frozenset({"-b", "--body", "-t", "--title"})
        for art in ("issue", "pr")
        for unter in ("create", "comment", "edit")
    },
}
#: Datei-Endungen, unter denen der Freigabe-Befehl (auch unter Windows) ausführbar ist (#591, #621).
_JA_ENDUNGEN = ("", ".cmd", ".bat", ".ps1", ".exe")
_JA_NAMEN = frozenset(f"ja{e}" for e in _JA_ENDUNGEN)
_JA_ENDUNG_RE = rf"(?:\.(?:{'|'.join(e[1:] for e in _JA_ENDUNGEN if e)}))?\.*"
#: Das Wort an eine Kurz-Schalter-Gruppe geklebt (``-x'ja 5'`` → ``-xja 5``).
_JA_GEKLEBT = re.compile(rf"-[A-Za-z0-9]+?ja{_JA_ENDUNG_RE}(?![\w.-])", re.IGNORECASE)
#: Programme, die Befehle aus Text, Datei oder Eingabe lesen (``echo 'ja 5' | sh``, ``bash <(…)``).
_JA_INTERPRETER = {
    *_SHELLS,
    *("ksh", "ash", "fish", "busybox", "source", ".", "eval", "wsl"),
    *("python", "python3", "py", "node", "perl", "ruby", "php", "powershell", "pwsh", "cmd"),
}
#: ``ja`` (auch mit Endung aus :data:`_JA_ENDUNGEN`) als eigenes Wort, Groß/klein egal.
_JA_WORT = re.compile(rf"(?<![\w.-])ja{_JA_ENDUNG_RE}(?![\w.-])", re.IGNORECASE)
#: Unter ``cmd`` zählt das Wort auch an einen Schalter geklebt (``/cja``, ``//c"ja 5"``, ``/Kja``):
#: cmd trennt ``/c`` nicht per Leerzeichen ab. Geprüft auf dem Text ohne Quotes und ``^``.
_JA_CMD = re.compile(rf"(?:^|[^\w.-]|/[a-z]+)ja{_JA_ENDUNG_RE}(?![\w.-])", re.IGNORECASE)
#: Befehlsname, den erst die Shell festlegt (Variable, Ersetzung, Glob, Klammern).
_JA_DYNAMISCH = re.compile(r"[$`*?\[{]")
#: ANSI-C-Quote ``$'…'``: bash löst ``\\x6a``, ``\\152`` … auf, shlex nicht.
_JA_ANSI_C = re.compile(r"\$'((?:[^'\\]|\\.)*)'")


def _ansi_c_aufloesen(treffer: re.Match[str]) -> str:
    try:
        text = treffer.group(1).encode("latin-1", "backslashreplace").decode("unicode_escape")
    except UnicodeDecodeError:
        text = treffer.group(1).replace("\\", "")
    return shlex.quote(text)


def _ja_im_text(befehl: str) -> bool:
    """``ja`` als Wort irgendwo im Text, Quotes und Backslashes entfernt (``j''a``, ``j\\a``)."""
    return bool(_JA_WORT.search(re.sub(r"[\\'\"]", "", befehl)))


def _klammer_varianten(wort: str, tiefe: int = 0) -> list[str]:
    """Wörter nach bash-Klammer-Erweiterung (``{ja,}`` → ``ja``, ``{j,x}a`` → ``ja``, ``xa``)."""
    treffer = re.search(r"\{([^{}]*,[^{}]*)\}", wort)
    if not treffer or tiefe > 4:
        return [wort]
    kopf, rest = wort[: treffer.start()], wort[treffer.end() :]
    return [v for teil in treffer.group(1).split(",") for v in _klammer_varianten(kopf + teil + rest, tiefe + 1)]


def _ist_ja_name(wort: str) -> bool:
    """Befehlsname ist ``ja`` (auch mit Endung aus :data:`_JA_ENDUNGEN`) — Groß/klein egal,
    auch über Klammern oder Glob (``j?``)."""
    for variante in _klammer_varianten(wort):
        basis = _ja_basis(variante).lower()
        if basis in _JA_NAMEN or (
            re.search(r"[*?\[]", basis) and any(fnmatch.fnmatchcase(z, basis) for z in _JA_NAMEN)
        ):
            return True
    return False


#: Wörter, hinter denen das nächste Wort ein Befehl/Skript ist (``bash ja``, ``xargs ja``, ``. ja``).
_JA_STARTER = {"bash", "sh", "zsh", "dash", "source", ".", "xargs", "python", "python3", "env", "command"}
#: Grobe Erkennung auf Roh-Text, wenn die Prüfung ausfällt (kaputte Eingabe, Ausnahme).
_JA_GROB = re.compile(rf"[/\\]ja{_JA_ENDUNG_RE}(?![\w.-])|nutzer-antwort|to_spawn\.py\s+ja\b", re.IGNORECASE)


#: Schalter von xargs/python/env …, deren nächstes Wort sein Wert ist (``xargs -n 1 ja``, ``python -X utf8 …``).
_JA_SCHALTER_MIT_WERT = {"-I", "-n", "-L", "-P", "-s", "-d", "-E", "-X", "-W", "-u", "-C"}
_JA_WIN_PFAD = re.compile(rf"\\ja{_JA_ENDUNG_RE}(?![\w.-])", re.IGNORECASE)


def _ja_basis(wort: str) -> str:
    basis = wort.replace("\\", "/").rsplit("/", 1)[-1]
    return basis.rstrip(".") or basis if basis not in (".", "..") else basis


def _ja_als_argument(woerter: list[str]) -> bool:
    """``ja`` als Pfad (``…/ja``) oder als Ziel hinter einem Starter (``bash``/``xargs``/``python`` …, Schalter
    dazwischen übersprungen); ``to_spawn.py`` irgendwo im Teilbefehl, dann ``ja``/``nutzer-antwort``.

    Normales Deutsch (``echo "ja"``, ``grep ja datei``) bleibt frei: dort steht ``ja`` weder als Pfad
    noch als Ziel eines Starters.
    """

    def ziel(i: int) -> str:
        """Erstes Nicht-Schalter-Wort ab ``i`` (Schalter mit eigenem Wert nehmen es mit)."""
        while i < len(woerter) and woerter[i].startswith("-"):
            i += 2 if woerter[i] in _JA_SCHALTER_MIT_WERT else 1
        return woerter[i] if i < len(woerter) else ""

    for i, wort in enumerate(woerter):
        if i and _ja_basis(wort).lower() in _JA_NAMEN and ("/" in wort or "\\" in wort):
            return True
        # ``.``/``source`` sind nur als Befehlsname Starter — als Argument (``find . -name …``) ein Ordner.
        starter = _name(wort.lower()) in _JA_STARTER and (i == 0 or wort not in (".", "source"))
        if starter and _ja_basis(ziel(i + 1)).lower() in _JA_NAMEN:
            return True
        if _ja_basis(wort).lower() == "to_spawn.py" and ziel(i + 1) in JA_UNTERBEFEHLE:
            return True
    return False


# --- Ausnahmen und Windows-Shells der Ja-Sperre (#567) ---------------------------------------------

#: Kopfzeile der einzigen erlaubten heredoc-Form: ``cat > datei <<'WORT'`` (auch ``>>``, auch Ziel
#: hinten), nur ASCII-Leerzeichen; Ziel und WORT aus festem ASCII-Vorrat (kein ``$``, Glob, ``~``, Quote).
_DATEI_HEREDOC_KOPF = re.compile(
    r"cat ?(?:>>? ?[A-Za-z0-9_./:-]+ ?<<'([A-Za-z0-9_]+)'|<<'([A-Za-z0-9_]+)' ?>>? ?[A-Za-z0-9_./:-]+)",
    re.ASCII,
)
#: Steuerzeichen außer Zeilenumbruch und Tab (CR, NUL, Formfeed …) — dann gilt keine Ausnahme.
_STEUERZEICHEN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _nur_datei_heredoc(befehl: str) -> bool:
    """True, wenn der GANZE Bash-Befehl genau ``cat > datei <<'WORT'`` + Rumpf + Endzeile ist.

    Positivliste statt Verbotsliste (#567): Was hier nicht genau passt, wird normal geprüft —
    eine vergessene Sonderform kann so nur einen Fehlalarm machen, nie einen Aufruf durchlassen.
    Begrenzer in einfachen Anführungszeichen (die Shell ersetzt im Rumpf nichts), kein ``tee``,
    kein ``<<-``, kein CR/NUL/Steuerzeichen im Befehl. Der Rumpf ist frei; die erste Zeile gleich
    ``WORT`` muss die letzte sein, sonst endet der heredoc früher und der Rest liefe als Befehl.
    """
    if _STEUERZEICHEN.search(befehl):
        return False
    zeilen = befehl.removesuffix("\n").split("\n")
    kopf = _DATEI_HEREDOC_KOPF.fullmatch(zeilen[0])
    if not kopf or len(zeilen) < 2:
        return False
    ziel = re.search(r">>? ?([A-Za-z0-9_./:-]+)", zeilen[0])
    if ziel and _ja_startdatei(ziel.group(1)):  # Start-/Hook-Datei: läuft später von selbst (PR 610)
        return False
    wort = kopf.group(1) or kopf.group(2)
    return wort in zeilen[1:] and zeilen.index(wort, 1) == len(zeilen) - 1


#: Ein Wort der erlaubten Such-Form: feste Option, nacktes Wort oder ASCII-Text in Quotes — je GANZ,
#: nie geklebt. Nackt und in Quotes nie mit ``-`` oder Leerzeichen vorn, nie leer: bash entfernt die
#: Quotes, ``'--pre=x'`` wäre sonst eine echte Option.
_SUCH_WORT = (
    r"(?:-[rnilcowEFHhsvLx]+|-[ABCm][0-9]*|-e"
    r"|--(?:include|exclude|glob|type)=[A-Za-z0-9_.,/-]+"
    r"|[A-Za-z0-9_./:=,@%+][A-Za-z0-9_./:=,@%+-]*"
    r"|'[A-Za-z0-9_./:=,@%+][A-Za-z0-9 _./:=,@%+-]*'|\"[A-Za-z0-9_./:=,@%+][A-Za-z0-9 _./:=,@%+-]*\")"
)
#: Der ganze Befehl: ``grep``/``rg`` wortgleich vorn, danach Wörter mit je genau einem Leerzeichen.
_NUR_SUCHE = re.compile(rf"(?:grep|rg)(?: {_SUCH_WORT})+", re.ASCII)


def _nur_suche(befehl: str) -> bool:
    """True, wenn der GANZE Bash-Befehl eine ``grep``/``rg``-Suche in der engen Form ist.

    Positivliste statt Verbotsliste (#567): ein verankertes Muster aus erlaubten Zeichen und Wörtern
    (:data:`_SUCH_WORT`), kein Zerleger als Entscheidung. Erlaubt sind nur Such-Optionen ohne
    Programmstart (also kein ``--pre``, ``--hostname-bin``, ``-f``, keine Präfix-Abkürzung), nackte
    Wörter und Text in ASCII-Quotes ohne Shell-Sonderzeichen. Grep und rg führen solche Argumente
    nie aus — dann darf das Suchmuster den Aufruftext tragen. Alles andere: ``False``.
    """
    return bool(_NUR_SUCHE.fullmatch(befehl))


def _rg_vorfilter_ja(woerter: list[str]) -> bool:
    """True, wenn ``rg --pre``/``--hostname-bin <programm>`` ein Ja startet (Wörter schon ohne Quotes)."""
    for k, wort in enumerate(woerter):
        if (wort.startswith("--pre") and not wort.startswith("--pre-")) or wort.startswith("--hostname-bin"):
            wert = wort.split("=", 1)[1] if "=" in wort else (woerter[k + 1] if k + 1 < len(woerter) else "")
            if ja_aufrufe(wert) or _ja_basis(wert).lower() in _JA_NAMEN:
                return True
    return False


#: Windows-Shells, deren Argumente selbst ein Befehl sind (``powershell -Command …``, ``cmd /c …``).
_JA_WIN_SHELLS = {"powershell", "pwsh", "cmd"}


def _ja_in_win_shell(args: list[str]) -> bool:
    """True, wenn irgendein Argument-Ende ab einem Wort ein Ja-Aufruf ist, oder ein
    ``-EncodedCommand`` ein Ja enthält bzw. sich nicht lesen lässt.

    Alle Wort-Enden statt Schalter-Zerlegung: ein unbekannter Schalter mit Wert kann so nie den
    Befehlsanfang verdecken (fail-closed; ``-Command Write-Host ja`` ohne Quotes sperrt mit).
    """
    for k, arg in enumerate(args):
        teile = re.split(r"[:=]", arg, maxsplit=1)
        schalter = teile[0].lstrip("-/").lower()
        if arg[:1] in ("-", "/") and schalter and (schalter == "ec" or "encodedcommand".startswith(schalter)):
            roh_b64 = teile[1] if len(teile) > 1 else (args[k + 1] if k + 1 < len(args) else "")
            try:
                text = base64.b64decode(roh_b64, validate=True).decode("utf-16-le")
            except ValueError:
                return True
            if not roh_b64 or ja_aufrufe(text) or _ja_traegt(re.sub(r"[`‘’‚“”„]", "", text)):
                return True
    return any(ja_aufrufe(" ".join(args[k:])) for k in range(len(args)))


def _bash_text_ohne_aufruf(befehl: str) -> bool:
    """True, wenn der Bash-Befehl eine der zwei Positivlisten ist (#567) — dann ist der Aufruftext
    nur Text. Nur für das Bash-Werkzeug: PowerShell & Co. liest Quotes anders (typografische)."""
    return _nur_datei_heredoc(befehl) or _nur_suche(befehl)


def hook_ja_sperre(strom: TextIO | None = None, fehler: TextIO | None = None) -> int:
    """PreToolUse-Hook (Bash) in ``~/.claude/settings.json``: Ja-Befehle aus Werkzeug-Aufrufen ablehnen.

    Der ``!``-Befehl des Nutzers läuft nicht durch PreToolUse-Hooks, ein Bash-Werkzeug-Aufruf einer
    Session schon — das ist der Menschen-Nachweis ohne Terminal (#531). Exit 2 bei Treffer, sonst 0.
    Fail-closed: fällt die Prüfung aus (kaputte Eingabe, jede Ausnahme, Rekursionstiefe), sperrt das
    Wort irgendwo im Befehl bzw. in der Roh-Eingabe; Befehle ohne das Wort laufen durch (wie #204).
    Nur das Bash-Werkzeug wird zerlegt: jedes andere (PowerShell) liest Quotes, ``iex``, ``& (…)``
    und Backtick-Escapes anders — dort sperrt jedes Vorkommen des Worts (keine eigene Positivliste).
    Der Befehl steht bei Bash und PowerShell in ``tool_input.command``.
    """
    text, befehl = "", None
    try:
        text = (strom or sys.stdin).read()
        daten = json.loads(text)
        roh = daten.get("tool_input") if isinstance(daten, dict) else None
        befehl = roh.get("command") if isinstance(roh, dict) else None
        werkzeug = daten.get("tool_name") if isinstance(daten, dict) else None
        if not isinstance(befehl, str):
            treffer = False
        elif werkzeug != "Bash":
            treffer = ja_aufrufe(befehl) or _ja_traegt(re.sub(r"[`‘’‚“”„]", "", befehl))
        else:
            treffer = not _bash_text_ohne_aufruf(befehl) and ja_aufrufe(befehl)
    except Exception:  # fail-closed: jede Ausnahme (auch RecursionError) sperrt beim Wort
        log.exception("Ja-Sperre: Prüfung fehlgeschlagen — Wort im Text entscheidet.")
        quelle = befehl if isinstance(befehl, str) else re.sub(r"\\[nrt]", " ", text)
        treffer = _ja_traegt(quelle) or bool(_JA_GROB.search(text))
    if treffer:
        (fehler or sys.stderr).write(
            "Gesperrt: das Live-Ja gibt nur der Nutzer selbst (`! ja <N>` im Eingabefeld). "
            "Eine Session meldet ihm die Frage und wartet.\n"
        )
        return 2
    return 0


#: Pfad der Skill-CLI für den Hinweis (dort liegt sie in jeder Installation).
SKILL_CLI = (Path(__file__).resolve().parent.parent / "to_spawn.py").as_posix()
