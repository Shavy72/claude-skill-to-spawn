"""Werkzeug-Schnittstelle (#660): Bau-Sessions wahlweise mit ``claude`` oder ``agy``.

Einzige Stelle für die Wahl des Werkzeugs (Konfig-Feld ``runner`` in ``.to-spawn/config.json``,
Standard ``claude``), die Modell-Auflösung je Werkzeug, den Startbefehl sowie die Erkennung,
ob eine Session im Prozessbaum bzw. tmux-Pane läuft oder still ist.

Aufrufer: Starter (``skripte/bau.py``), Prozess- und Speicher-Prüfung
(``skripte/sessions_stand.py``, ``to_spawn/speicher.py``) sowie Aufsicht
(``to_spawn/aufpasser.py``, ``to_spawn/respawn.py``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from . import bau_log, config, prozessbaum, starter

log = logging.getLogger("to_spawn.werkzeug")
_WINDOWS = sys.platform == "win32"

#: Standard-Werkzeug, wenn ``runner`` fehlt, leer oder unbekannt ist.
STANDARD_RUNNER = "claude"
#: Unterstützte Werkzeuge für Bau-Sessions.
WERKZEUGE: tuple[str, ...] = ("claude", "agy")
#: Vorgabe-Modell für ``agy``, wenn nur ``runner: "agy"`` umgeschaltet wird.
AGY_STANDARD_MODELL = "gemini-4-neon-high"
#: Unterstützte ``agy``-Modelle für Setup-Wizard und Bau-Sessions (#660).
AGY_MODELLE: tuple[tuple[str, str], ...] = (
    ("gemini-4-neon-high", "Gemini 4 Neon (High)"),
    ("gemini-4-neon-xhigh", "Gemini 4 Neon (XHigh)"),
    ("gemini-4-neon-medium", "Gemini 4 Neon (Medium)"),
    ("gemini-4-neon-low", "Gemini 4 Neon (Low)"),
)
#: Relativer Datenordner von Antigravity CLI unterhalb des Heimordners (#660).
AGY_DATEN_REL = Path(".gemini") / "antigravity-cli"
#: Ausdrücklicher Staffel-Übergabe-Marker im Handoff (wie ``staffel_stop.py``).
_AGY_STAFFEL_MARKER = re.compile(r"^\s*\**\s*staffel\s*\**\s*:\s*\**\s*weiter", re.IGNORECASE | re.MULTILINE)
_NANO_ZEIT = re.compile(r"(\.\d{6})\d+")

#: Normalisierte Programmnamen (ohne Endung/Pfad) laufender Session-Prozesse.
SESSION_PROZESSNAMEN: frozenset[str] = frozenset({"claude", "agy"})
#: Prozessnamen für ``sessions_stand.py`` (Windows + Linux, inkl. npm-Node-Hülle).
SESSION_NAMEN_ALLE: frozenset[str] = frozenset({"claude.exe", "node.exe", "claude", "node", "agy.exe", "agy"})

#: Pane-Marker, die eine arbeitende Session anzeigen (Claude Code und agy).
ARBEITS_MARKER: tuple[str, ...] = (
    "esc to interrupt",
    "background agent",
    "usage limit",
    "limit reset",
)
#: Pane-Marker, die eine offene Rückfrage anzeigen.
RUECKFRAGE_MARKER: tuple[str, ...] = (
    "Do you want to proceed?",
    "Do you want to",
    "Yes, and don't ask again",
    "(y/n)",
    "Trust",
    "Esc to cancel",
)
#: Aktive Spinner-Zeile einer laufenden Session (Claude Code und CLI-Spinner).
_SPINNER_ZEILE = re.compile(r"^\s*[✻✶✳✢✽⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s+\S", re.MULTILINE)

SessionZustand = Literal["aus", "arbeitet", "rueckfrage", "still"]


def runner_von(konfig: Mapping[str, Any] | None) -> str:
    """Werkzeug aus ``konfig["runner"]`` (``claude`` oder ``agy``); Vorgabe ``claude``.

    Fehlende, leere oder unbekannte Werte fallen mit Warnung (bei unbekanntem Wert)
    auf ``claude`` zurück — wie alle Konfig-Leser in ``to_spawn.config``.
    """
    if not isinstance(konfig, Mapping):
        return STANDARD_RUNNER
    roh = konfig.get("runner")
    if roh is None:
        return STANDARD_RUNNER
    if not isinstance(roh, str):
        log.warning("Konfig-Wert runner=%r ist kein Text — nehme %s.", roh, STANDARD_RUNNER)
        return STANDARD_RUNNER
    wert = roh.strip().lower()
    if not wert:
        return STANDARD_RUNNER
    if wert in WERKZEUGE:
        return wert
    log.warning(
        "Unbekannter runner %r in der Konfig (erlaubt: %s) — nehme %s.",
        roh,
        ", ".join(WERKZEUGE),
        STANDARD_RUNNER,
    )
    return STANDARD_RUNNER


def modell_fuer(
    runner: str,
    konfig: Mapping[str, Any] | None = None,
    *,
    cli_modell: str | None = None,
    default_modell: str | None = None,
) -> str | None:
    """Modell für ``runner`` auflösen: ``--model`` > Konfig ``modelle.ticket`` > Vorgabe.

    Bei ``runner == "agy"`` reicht das Umschalten von ``runner`` allein: ein aus den
    Vorgaben geerbtes Claude-Modell (``claude-*``) wird durch :data:`AGY_STANDARD_MODELL`
    ersetzt. Ein ausdrückliches ``--model`` auf der Kommandozeile gewinnt immer.
    """
    if cli_modell and cli_modell.strip():
        return cli_modell.strip()
    modelle = konfig.get("modelle") if isinstance(konfig, Mapping) else None
    konfig_modell = (str(modelle.get("ticket") or "").strip() if isinstance(modelle, Mapping) else "") or None
    if runner == "agy":
        if konfig_modell and not konfig_modell.startswith("claude-"):
            return konfig_modell
        return AGY_STANDARD_MODELL
    return konfig_modell or default_modell


def startbefehl(
    runner: str,
    *,
    programm: str | None = None,
    modell: str | None = None,
    prompt: str | None = None,
    interaktiv: bool = True,
    effort: str | None = None,
    settings_pfad: Path | str | None = None,
    mcp_pfad: Path | str | None = None,
    chrome: bool = False,
    probesitz: bool = False,
    probesitz_rechte: Sequence[str] = (),
    remote_control: str | None = None,
    resume: str | None = None,
    session_id: str | None = None,
) -> list[str]:
    """Startbefehl (argv-Liste) für ``claude`` oder ``agy`` bauen.

    Für ``claude`` entspricht der Aufbau exakt der bisherigen ``bau.py``-Startzeile.
    Für ``agy`` werden nur agy-Schalter gesetzt: interaktiv ``-i <prompt>``, im
    Print-/Probesitz-Modus ``-p <prompt> --output-format json``, jeweils mit ``--model``
    und ``--dangerously-skip-permissions`` (ohne Claude-spezifische Flags wie
    ``--settings``, ``--mcp-config``, ``--no-chrome`` oder ``--session-id``).
    """
    if runner not in WERKZEUGE:
        raise ValueError(f"Unbekanntes Werkzeug: {runner!r} (erlaubt: {', '.join(WERKZEUGE)})")
    bin_pfad = programm or starter.finde(runner)
    druck = probesitz or not interaktiv

    if runner == "claude":
        cmd = [bin_pfad]
        if druck:
            cmd.append("-p")
            if probesitz_rechte:
                cmd += ["--allowedTools", *probesitz_rechte]
        if settings_pfad:
            cmd += ["--settings", str(settings_pfad)]
        if mcp_pfad:
            cmd += ["--mcp-config", str(mcp_pfad), "--strict-mcp-config"]
        if not chrome:
            cmd.append("--no-chrome")
        if modell:
            cmd += ["--model", modell]
        if effort:
            cmd += ["--effort", effort]
        if remote_control:
            cmd += ["--remote-control", remote_control]
        if resume:
            cmd += ["--resume", resume]
        elif session_id:
            cmd += ["--session-id", session_id]
        if prompt is not None:
            cmd.append(prompt)
        return cmd

    cmd = [bin_pfad]
    if prompt is not None:
        cmd += ["-p" if druck else "-i", prompt]
    if modell:
        cmd += ["--model", modell]
    cmd.append("--dangerously-skip-permissions")
    if druck:
        cmd += ["--output-format", "json"]
    elif remote_control:
        cmd.append("--remote-control")
    if resume:
        cmd += ["--resume", resume]
    return cmd


def _programm_index(cmd: Sequence[str], runner: str) -> int:
    """Index des Werkzeug-Programms in ``cmd`` (auch hinter einem Sandbox-Präfix)."""
    for i, teil in enumerate(cmd):
        if prozessbaum.programm_name(teil) == runner:
            return i
    return 0


def prompt_setzen(
    cmd: list[str],
    runner: str,
    prompt: str,
    *,
    hat_prompt: bool,
    interaktiv: bool = True,
) -> list[str]:
    """Prompt in einem bestehenden Startbefehl ersetzen oder (nach ``--ohne-prompt``) einfügen."""
    if runner not in WERKZEUGE:
        raise ValueError(f"Unbekanntes Werkzeug: {runner!r} (erlaubt: {', '.join(WERKZEUGE)})")
    if runner == "claude":
        if hat_prompt:
            cmd[-1] = prompt
        else:
            cmd.append(prompt)
        return cmd
    for flag in ("-i", "--prompt-interactive", "-p", "--print", "--prompt"):
        if flag in cmd[:-1]:
            i = cmd.index(flag)
            cmd[i + 1] = prompt
            return cmd
    pos = _programm_index(cmd, runner) + 1
    cmd[pos:pos] = ["-i" if interaktiv else "-p", prompt]
    return cmd


def ist_session_programm(arg: str, *, windows: bool = _WINDOWS) -> bool:
    """Gehört ``arg`` (``argv[0]`` oder Pfad) zu einer unterstützten Bau-Session (``claude``/``agy``)?"""
    if not arg:
        return False
    return prozessbaum.programm_name(arg, windows=windows) in SESSION_PROZESSNAMEN


def ist_session_zeile(zeile: str, *, windows: bool = _WINDOWS) -> bool:
    """Ist diese Kommandozeile eine laufende ``claude``- oder ``agy``-Session (kein Wrapper/Launcher)?"""
    if prozessbaum.ist_session_zeile(zeile, windows=windows):
        return True
    teile = [t.strip('"') for t in zeile.split()] if windows else zeile.split()
    if not teile or any(marke in zeile for marke in prozessbaum.NIE_SESSION):
        return False
    programm = prozessbaum.programm_name(teile[0], windows=windows)
    if programm in prozessbaum.WRAPPER_NAMEN and "-c" in teile[1:3]:
        return False
    return programm == "agy"


def ist_session_pid(pid: int) -> bool:
    """Ist ``pid`` ein laufender ``claude``- oder ``agy``-Session-Prozess?

    Prüft zuerst :func:`prozessbaum.ist_claude` (bewahrt alle Claude-Regeln inkl. npm-Node-Hülle
    und bestehende Test-Nähte) und erkennt zusätzlich ``agy`` über ``argv[0]``.
    """
    if prozessbaum.ist_claude(pid):
        return True
    argv = prozessbaum.argv(pid)
    return bool(argv) and ist_session_programm(argv[0])


def session_laeuft(pane_pid: int) -> bool:
    """Läuft im Prozessbaum unter ``pane_pid`` eine ``claude``- oder ``agy``-Session?"""
    return any(ist_session_pid(pid) for pid in prozessbaum.baum(pane_pid))


def session_zustand(
    pane_text: str,
    *,
    status: str | None = None,
    laeuft: bool = True,
) -> SessionZustand:
    """Zustand einer Session aus Prozess-Existenz, optionalem JSON-Status und Pane-Text.

    Rückgabe: ``aus`` (kein Session-Prozess), ``rueckfrage`` (offener Bestätigungsdialog),
    ``arbeitet`` (Status ``busy`` oder Arbeits-/Spinner-Marker im Pane), sonst ``still``.
    """
    if not laeuft:
        return "aus"
    if any(marker in pane_text for marker in RUECKFRAGE_MARKER):
        return "rueckfrage"
    if status == "busy":
        return "arbeitet"
    if any(marker in pane_text for marker in ARBEITS_MARKER) or _SPINNER_ZEILE.search(pane_text):
        return "arbeitet"
    return "still"


# --- agy-Kontext-Auswertung & Staffel-Übergabe (#660) -----------------------


def agy_daten_ordner(heim: Path | None = None) -> Path:
    """Antigravity-Datenordner unter ``~/.gemini/antigravity-cli`` (#660)."""
    basis = heim if heim is not None else config.heim_ordner()
    return basis / AGY_DATEN_REL


def agy_transkript_pfad(conversation_id: str, *, agy_ordner: Path | None = None) -> Path:
    """Pfad zur JSONL-Transkriptdatei einer ``agy``-Gesprächs-ID."""
    basis = agy_ordner if agy_ordner is not None else agy_daten_ordner()
    return basis / "brain" / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"


def _zahl(wert: Any) -> int:
    if isinstance(wert, bool) or wert is None:
        return 0
    try:
        return max(0, int(wert))
    except (TypeError, ValueError):
        return 0


def _parse_zeitstempel(roh: Any) -> datetime | None:
    if not isinstance(roh, str):
        return None
    text = roh.strip()
    if not text:
        return None
    # SQLite-/Go-Zeitstempel tragen teils 9 Nachkommastellen (Nanosekunden) und Leerzeichen.
    text = _NANO_ZEIT.sub(r"\1", text).replace("Z", "+00:00")
    try:
        wert = datetime.fromisoformat(text)
    except ValueError:
        return None
    return wert if wert.tzinfo else wert.replace(tzinfo=UTC)


def _token_werte_aus(eintrag: Mapping[str, Any]) -> tuple[int, int, int, int] | None:
    """``(input, cache_read, cache_creation, output)`` aus Transkript-Zeile oder JSON-Objekt."""
    quelle: Mapping[str, Any] = eintrag
    if isinstance(eintrag.get("usage"), Mapping):
        quelle = eintrag["usage"]
    elif eintrag.get("type") not in (None, "PLANNER_RESPONSE") and not any(
        k in eintrag for k in ("input_tokens", "cache_read_tokens", "output_tokens")
    ):
        return None
    inp = _zahl(quelle.get("input_tokens"))
    cr = _zahl(
        quelle.get("cache_read_tokens") if "cache_read_tokens" in quelle else quelle.get("cache_read_input_tokens")
    )
    cc = _zahl(
        quelle.get("cache_creation_tokens")
        if "cache_creation_tokens" in quelle
        else quelle.get("cache_creation_input_tokens")
    )
    out = _zahl(quelle.get("output_tokens"))
    if inp == 0 and cr == 0 and cc == 0 and out == 0:
        return None
    return inp, cr, cc, out


def _eintraege_auswerten(eintraege: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Gemeinsame Token- und Spitzen-Kontext-Auswertung für ``agy``-Transkripte und JSON-Ausgaben."""
    tokens = {"input": 0, "cache_read": 0, "cache_creation": 0, "output": 0, "gesamt": 0}
    spitze = 0
    aufrufe = 0
    modell: str | None = None
    session_id: str | None = None
    zeiten: list[datetime] = []

    for eintrag in eintraege:
        zeit = _parse_zeitstempel(eintrag.get("created_at") or eintrag.get("timestamp"))
        if zeit is not None:
            zeiten.append(zeit)
        cid = eintrag.get("conversation_id") or eintrag.get("session_id")
        if isinstance(cid, str) and cid.strip():
            session_id = cid.strip()
        werte = _token_werte_aus(eintrag)
        if werte is None:
            continue
        inp, cr, cc, out = werte
        aufrufe += 1
        spitze = max(spitze, inp + cr + cc)
        tokens["input"] += inp
        tokens["cache_read"] += cr
        tokens["cache_creation"] += cc
        tokens["output"] += out
        m = eintrag.get("model")
        if isinstance(m, str) and m.strip():
            modell = m.strip()

    if aufrufe == 0:
        return None
    tokens["gesamt"] = tokens["input"] + tokens["cache_read"] + tokens["cache_creation"] + tokens["output"]
    dauer_s = max(0, int((max(zeiten) - min(zeiten)).total_seconds())) if len(zeiten) >= 2 else 0
    ergebnis: dict[str, Any] = {
        "tokens": tokens,
        "kontext": {"spitze": spitze, "aufrufe": aufrufe},
        "spitze_k": round(spitze / 1000, 1) if spitze else None,
        "modell": modell,
        "dauer_s": dauer_s,
    }
    if session_id:
        ergebnis["session_id"] = session_id
    return ergebnis


def agy_transkript_auswerten(pfad: Path) -> dict[str, Any] | None:
    """``agy``-Transkript (``transcript.jsonl``) fehlertolerant auswerten (#660).

    Rückgabe: ``{"tokens", "kontext": {"spitze", "aufrufe"}, "spitze_k", "modell", "dauer_s"}``
    oder ``None``, wenn die Datei fehlt oder keine Token-Einträge enthält.
    """
    if not pfad or not pfad.is_file():
        return None
    eintraege: list[dict[str, Any]] = []
    try:
        zeilen = pfad.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as fehler:
        log.warning("agy-Transkript %s nicht lesbar: %s", pfad, fehler)
        return None
    for roh in zeilen:
        roh = roh.strip()
        if not roh:
            continue
        try:
            daten = json.loads(roh)
        except ValueError:
            continue
        if isinstance(daten, dict):
            eintraege.append(daten)
    return _eintraege_auswerten(eintraege)


def agy_json_auswerten(text: str) -> dict[str, Any] | None:
    """Ausgabe von ``agy -p … --output-format json`` (Objekt oder NDJSON) auswerten (#660)."""
    if not text or not text.strip():
        return None
    roh = text.strip()
    try:
        ganz = json.loads(roh)
    except ValueError:
        ganz = None
    if isinstance(ganz, dict):
        return _eintraege_auswerten([ganz])
    if isinstance(ganz, list):
        return _eintraege_auswerten([e for e in ganz if isinstance(e, dict)])
    eintraege: list[dict[str, Any]] = []
    for zeile in roh.splitlines():
        zeile = zeile.strip()
        if not zeile:
            continue
        try:
            obj = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(obj, dict):
            eintraege.append(obj)
    return _eintraege_auswerten(eintraege)


_WIN_URI_LAUFWERK = re.compile(r"^/[A-Za-z]:(?:[\\/]|$)")


def _uri_passt_zu_ordner(
    uri: str,
    ziel: PurePath,
    ziel_uri: str,
    *,
    windows: bool = _WINDOWS,
) -> bool:
    if uri == ziel_uri:
        return True
    if not uri.startswith("file://"):
        return False
    geparst = urlparse(uri)
    pfad = unquote(geparst.path)
    if windows:
        netloc = unquote(geparst.netloc)
        if netloc and netloc.lower() != "localhost":
            if len(netloc) == 2 and netloc[0].isalpha() and netloc[1] == ":":
                pfad = f"{netloc}{pfad}"
            else:
                pfad = f"\\\\{netloc}{pfad.replace('/', '\\\\')}"
        elif _WIN_URI_LAUFWERK.match(pfad):
            pfad = pfad[1:]
        if not pfad:
            return False
        if isinstance(ziel, Path) and sys.platform == "win32":
            try:
                return os.path.normcase(str(Path(pfad).resolve())) == os.path.normcase(str(ziel))
            except OSError:
                return False
        return str(PureWindowsPath(pfad)).lower() == str(PureWindowsPath(ziel)).lower()
    if not pfad:
        return False
    try:
        return Path(pfad).resolve() == ziel
    except OSError:
        return False


def agy_gespraech_fuer_ordner(
    ordner: Path,
    *,
    agy_ordner: Path | None = None,
    nach: float | None = None,
) -> str | None:
    """Jüngste Haupt-Gesprächs-ID von ``agy`` für den Arbeitsordner ``ordner`` (#660).

    Liest ``conversation_summaries.db`` nur lesend (``mode=ro``), überspringt Subagenten
    (``parent_conversation_id`` gesetzt) und ältere Gespräche (``<= nach``).
    """
    basis = agy_ordner if agy_ordner is not None else agy_daten_ordner()
    db = basis / "conversation_summaries.db"
    if not db.is_file():
        return None
    try:
        ziel = ordner.expanduser().resolve()
        ziel_uri = ziel.as_uri()
        db_uri = f"{db.resolve().as_uri()}?mode=ro"
    except (OSError, ValueError):
        return None
    try:
        con = sqlite3.connect(db_uri, uri=True, timeout=1.0)
    except sqlite3.Error as fehler:
        log.debug("agy-Datenbank %s nicht lesbar: %s", db, fehler)
        return None
    try:
        zeilen = con.execute(
            "SELECT conversation_id, workspace_uris, last_modified_time, parent_conversation_id "
            "FROM conversation_summaries ORDER BY last_modified_time DESC"
        ).fetchall()
    except sqlite3.Error as fehler:
        log.debug("agy-Datenbankabfrage fehlgeschlagen (%s): %s", db, fehler)
        return None
    finally:
        con.close()

    ist_windows = sys.platform == "win32"
    for cid, uris_roh, mod_roh, parent in zeilen:
        if not isinstance(cid, str) or not cid.strip():
            continue
        if isinstance(parent, str) and parent.strip():
            continue
        try:
            uris = json.loads(uris_roh) if isinstance(uris_roh, str) else []
        except ValueError:
            continue
        if not isinstance(uris, list) or not any(
            isinstance(u, str) and _uri_passt_zu_ordner(u, ziel, ziel_uri, windows=ist_windows) for u in uris
        ):
            continue
        if nach is not None:
            zeit = _parse_zeitstempel(mod_roh)
            if zeit is not None and zeit.timestamp() <= nach:
                continue
        return cid.strip()
    return None


def agy_kontext_fuer_ordner(
    ordner: Path,
    *,
    agy_ordner: Path | None = None,
    nach: float | None = None,
) -> dict[str, Any] | None:
    """Kontext- und Token-Kennzahlen der jüngsten ``agy``-Session im Ordner ``ordner``."""
    cid = agy_gespraech_fuer_ordner(ordner, agy_ordner=agy_ordner, nach=nach)
    if cid is None:
        return None
    daten = agy_transkript_auswerten(agy_transkript_pfad(cid, agy_ordner=agy_ordner))
    if daten is None:
        return None
    return {**daten, "session_id": cid}


def agy_spitze_k(
    ordner: Path,
    *,
    agy_ordner: Path | None = None,
    nach: float | None = None,
) -> float | None:
    """Spitzen-Kontext (in k) der jüngsten ``agy``-Session für ``ordner``; ``None`` ohne Daten."""
    daten = agy_kontext_fuer_ordner(ordner, agy_ordner=agy_ordner, nach=nach)
    return daten["spitze_k"] if daten else None


def agy_bau_log_nachtragen(
    repo: Path,
    ticket: str | int,
    worktree: Path,
    *,
    start_ts: float | None = None,
    runde: int = 1,
    modell: str | None = None,
    effort: str | None = None,
    agy_ordner: Path | None = None,
) -> dict[str, Any] | None:
    """Schreibt ``session_start`` (falls neu) und ``session_ende`` für eine beendete ``agy``-Runde ins Bau-Log."""
    daten = agy_kontext_fuer_ordner(worktree, agy_ordner=agy_ordner, nach=start_ts)
    if daten is None:
        return None
    ziel = worktree if worktree.is_dir() else repo
    sid = str(daten["session_id"])
    verwendetes_modell = daten.get("modell") or modell
    dauer_s = int(daten.get("dauer_s") or 0)
    if dauer_s == 0 and start_ts is not None:
        dauer_s = max(0, int(datetime.now(UTC).timestamp() - start_ts))
    bestehende = bau_log.lese(ziel, ticket, hauptbaum=repo if ziel != repo else None)
    hat_start = any(z.get("typ") == "session_start" and z.get("session_id") == sid for z in bestehende)
    if not hat_start:
        bau_log.schreibe(
            ziel,
            ticket,
            "session_start",
            session_id=sid,
            modell=verwendetes_modell,
            effort=effort,
            staffel=runde,
            runner="agy",
        )
    return bau_log.schreibe(
        ziel,
        ticket,
        "session_ende",
        session_id=sid,
        modell=verwendetes_modell,
        tokens=daten["tokens"],
        kontext=daten["kontext"],
        dauer_s=dauer_s,
        staffel=runde,
        runner="agy",
    )


def agy_staffel_handoff(
    ordner: Sequence[Path],
    ticket: str,
    *,
    seit: float,
    schon_uebergeben: str = "",
) -> tuple[Path, str] | None:
    """Frischen Handoff mit ``Staffel: weiter`` nach einer ``agy``-Runde erkennen (#660).

    Nutzt dieselbe Namens- und Fingerabdruck-Regel wie ``staffel_stop.py`` (ohne Claude-Hook).
    Rückgabe: ``(pfad, sha256_fingerabdruck)`` oder ``None``.
    """
    muster = re.compile(rf"^HANDOFF_\d{{4}}-\d{{2}}-\d{{2}}_{re.escape(ticket)}(_[\w-]+)?\.md$")
    treffer: list[tuple[float, Path, str]] = []
    for verzeichnis in ordner:
        if not verzeichnis.is_dir():
            continue
        for datei in verzeichnis.glob(f"HANDOFF_*_{ticket}*.md"):
            if not muster.match(datei.name):
                continue
            try:
                stand = datei.stat().st_mtime
                roh = datei.read_bytes()
            except OSError:
                continue
            if stand <= seit:
                continue
            inhalt = roh.decode("utf-8", errors="replace")
            if not _AGY_STAFFEL_MARKER.search(inhalt):
                continue
            fp = hashlib.sha256(roh).hexdigest()
            if schon_uebergeben and fp == schon_uebergeben:
                continue
            treffer.append((stand, datei, fp))
    if not treffer:
        return None
    _, datei, fp = max(treffer, key=lambda eintrag: eintrag[0])
    return datei, fp
