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
import json
import logging
import os
import re
import shlex
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from . import bau_log, befund, config

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
    summe["gesamt"] = summe["input"] + summe["cache_read"] + summe["cache_creation"] + summe["output"]
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

    Spec aus ``TO_SPAWN_SPEC`` (setzt ``bau.py``); ohne Spec nichts. Fehler nur loggen.
    """
    spec = os.environ.get("TO_SPAWN_SPEC", "").strip()
    if not spec.isdigit():
        return
    try:
        from . import waechter_takt

        ziel = Path(os.environ["TO_SPAWN_REPO"]) if os.environ.get("TO_SPAWN_REPO") else repo
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
    _starte_takt(repo, ticket)

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
#: Namen, die :func:`geschlossene_nummern` selbst auswertet.
_ZIELE = {"gh", "eval", *_SHELLS}
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


def _einfache_befehle(befehl: str) -> Iterator[list[str]]:
    """Einfache Befehle in ``befehl`` als Wortlisten, ohne Zuweisungen/Präfixe davor.

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
            woerter = shlex.split(teil)
        except ValueError:
            woerter = teil.split()
        woerter = _befehlskern(woerter)
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
    """Befehlsname ohne Pfad und ``.exe`` (``/usr/bin/gh`` → ``gh``)."""
    return wort.rsplit("/", 1)[-1].removesuffix(".exe")


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


# --- Ja-Sperre (#531): das Live-Ja kommt nur vom Menschen, nie aus einem Werkzeug-Aufruf ---------

#: Unterbefehle von ``to_spawn.py``, die das Ja des Nutzers schreiben (``ja`` = Kurzform, #531).
JA_UNTERBEFEHLE = {"ja", "nutzer-antwort"}


def ja_aufrufe(befehl: str) -> bool:
    """True, wenn ``befehl`` das Ja des Nutzers schreiben würde — die eine Stelle des Musters (#531).

    Erkannt: ``ja``/``ja.cmd`` als Befehl, ``nutzer-antwort`` als Wort, ``to_spawn.py ja`` (auch mit
    ``python`` davor); ``eval …``, ``bash -c "…"``, ``powershell``/``pwsh``/``cmd`` (auch
    ``-EncodedCommand``) werden ausgepackt.
    Bewusst breit und ohne Ausnahmen; die zwei Positivlisten für das Bash-Werkzeug (#567) prüft
    :func:`hook_ja_sperre` vorher (:func:`_bash_text_ohne_aufruf`).
    ponytail: nur wörtliche Aufrufe — Variablen, ein Skript, ``python -c`` oder ein direkter
    Schreibzugriff auf die Briefkasten-Datei umgehen die Sperre (gleiche Grenze wie ``hook_spec_sperre``);
    Upgrade-Pfad: Prüfung im Briefkasten selbst über einen Nachweis, den nur der Nutzer-Weg trägt.
    """
    if _JA_WIN_PFAD.search(befehl):  # Windows-Pfad: shlex verschluckt die Backslashes
        return True
    for woerter in _einfache_befehle(befehl):
        name = _name(woerter[0]).removesuffix(".cmd")
        if name == "eval":
            if ja_aufrufe(" ".join(woerter[1:])):
                return True
        elif name in _SHELLS and (text := _shell_text(woerter[1:])) is not None:
            if ja_aufrufe(text):
                return True
        elif name.lower().removesuffix(".exe") in _JA_WIN_SHELLS:
            if _ja_in_win_shell(woerter[1:]):
                return True
        elif name == "rg" and _rg_vorfilter_ja(woerter):
            return True
        elif (
            name == "ja"
            or "nutzer-antwort" in woerter
            or (any(_name(w) == "to_spawn.py" for w in woerter[:3]) and JA_UNTERBEFEHLE & set(woerter))
            or _ja_als_argument(woerter)
        ):
            return True
    return False


#: Wörter, hinter denen das nächste Wort ein Befehl/Skript ist (``bash ja``, ``xargs ja``, ``. ja``).
_JA_STARTER = {"bash", "sh", "zsh", "dash", "source", ".", "xargs", "python", "python3", "env", "command"}
#: Grobe Erkennung auf Roh-Text, wenn die Prüfung ausfällt (kaputte Eingabe, Ausnahme).
_JA_GROB = re.compile(r"[/\\]ja(\.cmd)?(?![\w.-])|nutzer-antwort|to_spawn\.py\s+ja\b")


#: Schalter von xargs/python/env …, deren nächstes Wort sein Wert ist (``xargs -n 1 ja``, ``python -X utf8 …``).
_JA_SCHALTER_MIT_WERT = {"-I", "-n", "-L", "-P", "-s", "-d", "-E", "-X", "-W", "-u", "-C"}
_JA_WIN_PFAD = re.compile(r"\\ja(\.cmd)?(?![\w.-])")


def _ja_basis(wort: str) -> str:
    return wort.replace("\\", "/").rsplit("/", 1)[-1]


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
        if i and _ja_basis(wort) in ("ja", "ja.cmd") and ("/" in wort or "\\" in wort):
            return True
        if _name(wort) in _JA_STARTER and _ja_basis(ziel(i + 1)) in ("ja", "ja.cmd"):
            return True
        if _ja_basis(wort) == "to_spawn.py" and ziel(i + 1) in JA_UNTERBEFEHLE:
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
            if ja_aufrufe(wert) or _ja_basis(wert) in ("ja", "ja.cmd"):
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
        schalter = arg.lstrip("-/").lower()
        if arg[:1] in ("-", "/") and schalter and (schalter == "ec" or "encodedcommand".startswith(schalter)):
            try:
                text = base64.b64decode(args[k + 1], validate=True).decode("utf-16-le")
            except (IndexError, ValueError):
                return True
            if ja_aufrufe(text):
                return True
    return any(ja_aufrufe(" ".join(args[k:])) for k in range(len(args)))


def _bash_text_ohne_aufruf(befehl: str) -> bool:
    """True, wenn der Bash-Befehl eine der zwei Positivlisten ist (#567) — dann ist der Aufruftext
    nur Text. Nur für das Bash-Werkzeug: PowerShell & Co. liest Quotes anders (typografische)."""
    return _nur_datei_heredoc(befehl) or _nur_suche(befehl)


def hook_ja_sperre(strom: TextIO | None = None, fehler: TextIO | None = None) -> int:
    """PreToolUse-Hook (Bash) in ``~/.claude/settings.json``: Ja-Befehle aus Werkzeug-Aufrufen ablehnen.

    Der ``!``-Befehl des Nutzers läuft nicht durch PreToolUse-Hooks, ein Bash-Werkzeug-Aufruf einer
    Session schon — das ist der Menschen-Nachweis ohne Terminal (#531). Exit 2 bei Treffer, sonst 0;
    fällt die Prüfung aus (kaputte Eingabe, Ausnahme), entscheidet eine grobe Suche auf der Roh-Eingabe
    (``…/ja``, ``nutzer-antwort``, ``to_spawn.py ja`` → Exit 2), fremde Befehle laufen durch (wie #204).
    Der Befehl steht bei Bash und PowerShell in ``tool_input.command``.
    """
    text = ""
    try:
        text = (strom or sys.stdin).read()
        daten = json.loads(text)
        roh = daten.get("tool_input") if isinstance(daten, dict) else None
        befehl = roh.get("command") if isinstance(roh, dict) else None
        werkzeug = daten.get("tool_name") if isinstance(daten, dict) else None
        treffer = (
            isinstance(befehl, str)
            and not (werkzeug == "Bash" and _bash_text_ohne_aufruf(befehl))
            and ja_aufrufe(befehl)
        )
    except Exception:  # der Hook darf die Session bei fremden Befehlen nie stören
        log.exception("Ja-Sperre: Prüfung fehlgeschlagen — grobe Prüfung auf Roh-Eingabe.")
        treffer = bool(_JA_GROB.search(text))
    if treffer:
        (fehler or sys.stderr).write(
            "Gesperrt: das Live-Ja gibt nur der Nutzer selbst (`! ja <N>` im Eingabefeld). "
            "Eine Session meldet ihm die Frage und wartet.\n"
        )
        return 2
    return 0


#: Pfad der Skill-CLI für den Hinweis (dort liegt sie in jeder Installation).
SKILL_CLI = (Path(__file__).resolve().parent.parent / "to_spawn.py").as_posix()
