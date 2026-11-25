"""Werkzeug-Schnittstelle (#660): Bau-Sessions wahlweise mit ``claude`` oder ``agy``.

Einzige Stelle für die Wahl des Werkzeugs (Konfig-Feld ``runner`` in ``.to-spawn/config.json``,
Standard ``claude``), die Modell-Auflösung je Werkzeug, den Startbefehl sowie die Erkennung,
ob eine Session im Prozessbaum bzw. tmux-Pane läuft oder still ist.

Aufrufer: Starter (``skripte/bau.py``), Prozess- und Speicher-Prüfung
(``skripte/sessions_stand.py``, ``to_spawn/speicher.py``) sowie Aufsicht
(``to_spawn/aufpasser.py``, ``to_spawn/respawn.py``).
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from . import prozessbaum, starter

log = logging.getLogger("to_spawn.werkzeug")
_WINDOWS = sys.platform == "win32"

#: Standard-Werkzeug, wenn ``runner`` fehlt, leer oder unbekannt ist.
STANDARD_RUNNER = "claude"
#: Unterstützte Werkzeuge für Bau-Sessions.
WERKZEUGE: tuple[str, ...] = ("claude", "agy")
#: Vorgabe-Modell für ``agy``, wenn nur ``runner: "agy"`` umgeschaltet wird.
AGY_STANDARD_MODELL = "gemini-4-neon-high"

#: Normalisierte Programmnamen (ohne Endung/Pfad) laufender Session-Prozesse.
SESSION_PROZESSNAMEN: frozenset[str] = frozenset({"claude", "agy"})
#: Prozessnamen für ``sessions_stand.py`` (Windows + Linux, inkl. npm-Node-Hülle).
SESSION_NAMEN_ALLE: frozenset[str] = frozenset(
    {"claude.exe", "node.exe", "claude", "node", "agy.exe", "agy"}
)

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
    konfig_modell = (
        str(modelle.get("ticket") or "").strip() if isinstance(modelle, Mapping) else ""
    ) or None
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
