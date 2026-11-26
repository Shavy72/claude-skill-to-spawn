"""Startklar-Prüfung Teil 3: Werkzeug-Probe (deny-Regeln, PreToolUse-Hooks, Trockenlauf).

Ausgelagert aus ``to_spawn/startklar.py`` (#49 / App #461). Spielt jedes
Aufseher-Werkzeug mit dem exakten Prompt-Befehl durch ``permissions.deny``,
PreToolUse-Hooks und einen Trockenlauf.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from to_spawn.startklar_basis import (
    HOOK_TIMEOUT_S,
    SKILL,
    WERKZEUG_TIMEOUT_S,
    WERKZEUGE,
    Befund,
    Laeufer,
    Werkzeug,
    _kurz,
    _py_name,
    _schwaerzen,
    _standard_laeufer,
    befehl_text,
)
from to_spawn.startklar_schluessel import _beispiel_ticket, _geheime_werte

log = logging.getLogger("to_spawn.startklar")


def standard_settings_dateien(ordner: Path) -> list[Path]:
    """Alle Settings-Dateien, deren PreToolUse-Hooks eine Session im ``ordner`` hätte."""
    if sys.platform == "win32":
        verwaltet = Path("C:/Program Files/ClaudeCode/managed-settings.json")
    elif sys.platform == "darwin":
        verwaltet = Path("/Library/Application Support/ClaudeCode/managed-settings.json")
    else:
        verwaltet = Path("/etc/claude-code/managed-settings.json")
    return [
        verwaltet,
        Path.home() / ".claude" / "settings.json",
        Path(ordner) / ".claude" / "settings.json",
        Path(ordner) / ".claude" / "settings.local.json",
    ]


def _passt(matcher: str, werkzeug: str = "Bash") -> bool:
    if matcher in ("", "*"):
        return True
    try:
        return re.fullmatch(matcher, werkzeug) is not None
    except re.error:
        return matcher == werkzeug


def _timeout(wert: object) -> float:
    try:
        zahl = float(wert) if wert not in (None, "") else HOOK_TIMEOUT_S  # type: ignore[arg-type]
    except (TypeError, ValueError):
        log.warning("Hook-timeout %r keine Zahl — nehme %s s.", wert, HOOK_TIMEOUT_S)
        return float(HOOK_TIMEOUT_S)
    return zahl if zahl > 0 else float(HOOK_TIMEOUT_S)


@dataclass(frozen=True)
class _Regeln:
    """Was eine Bash-Session aus den Settings bekommt."""

    hooks: list[tuple[str, float]]
    deny: list[str]
    fehler: list[str]


def _regeln(settings_dateien: Iterable[Path]) -> _Regeln:
    hooks: list[tuple[str, float]] = []
    deny: list[str] = []
    fehler: list[str] = []
    for datei in settings_dateien:
        datei = Path(datei)
        if not datei.is_file():
            continue
        try:
            daten = json.loads(datei.read_text(encoding="utf-8"))
            if not isinstance(daten, dict):
                raise TypeError("kein JSON-Objekt")
        except (OSError, ValueError, TypeError) as grund:
            log.warning("Settings %s unlesbar: %s", datei, grund)
            fehler.append(f"Settings {datei} unlesbar: {_kurz(str(grund), 120)}")
            continue
        regeln = (daten.get("permissions", {}) or {}).get("deny", []) or []
        deny.extend(str(r) for r in regeln if isinstance(r, str))
        for eintrag in (daten.get("hooks", {}) or {}).get("PreToolUse", []) or []:
            if not isinstance(eintrag, dict) or not _passt(str(eintrag.get("matcher", "") or "")):
                continue
            for hook in eintrag.get("hooks", []) or []:
                if isinstance(hook, dict) and hook.get("type", "command") == "command" and hook.get("command"):
                    hooks.append((str(hook["command"]), _timeout(hook.get("timeout"))))
    return _Regeln(hooks, deny, fehler)


def _deny_trifft(regel: str, befehl: str) -> bool:
    """``permissions.deny``-Regel gegen einen Bash-Befehl (vereinfacht wie Claude Code).

    ``Bash`` = alles · ``Bash(präfix:*)`` = Präfix · ``Bash(mit*stern)`` = Muster ·
    ``Bash(text)`` = genau dieser Befehl.
    """
    regel = regel.strip()
    if regel == "Bash":
        return True
    treffer = re.fullmatch(r"Bash\((.*)\)", regel, re.DOTALL)
    if not treffer:
        return False
    muster = treffer.group(1).strip()
    if muster.endswith(":*"):
        return befehl.startswith(muster[:-2])
    if "*" in muster:
        return fnmatch.fnmatchcase(befehl, muster)
    return befehl == muster


def _block_grund(ergebnis: subprocess.CompletedProcess[str]) -> str | None:
    """Grund (roh, ungekürzt, ungeschwärzt), wenn der Hook blockt, sonst ``None``.

    Block = Exit 2, ``permissionDecision`` deny/ask (ask wartet unbeaufsichtigt
    ewig), ``decision: block`` oder ``continue: false``.
    """
    if ergebnis.returncode == 2:
        return ergebnis.stderr or ergebnis.stdout or "Exit 2"
    if ergebnis.returncode != 0:
        return None
    try:
        daten = json.loads(ergebnis.stdout or "null")
    except ValueError:
        return None
    if not isinstance(daten, dict):
        return None
    spezifisch = daten.get("hookSpecificOutput") or {}
    if isinstance(spezifisch, dict):
        entscheidung = spezifisch.get("permissionDecision")
        if entscheidung in ("deny", "ask"):
            return f"{entscheidung}: {spezifisch.get('permissionDecisionReason') or ''}"
    if daten.get("decision") == "block":
        return str(daten.get("reason") or "block")
    if daten.get("continue") is False:
        return f"continue: false {daten.get('stopReason') or ''}"
    return None


def _rot(werkzeug: Werkzeug, text: str, behebung: str) -> Befund:
    return Befund("werkzeug", False, f"{werkzeug.name}: {text}", behebung)


def _probe_eines(
    werkzeug: Werkzeug,
    ordner: Path,
    spec: int,
    ticket: str,
    regeln: _Regeln,
    laeufer: Laeufer,
    geheim: Sequence[str],
    *,
    skill: Path | None = None,
) -> Befund:
    skill_ordner = SKILL if skill is None else skill
    befehl = befehl_text(werkzeug, spec, ticket, skill=skill_ordner)
    datei = Path(
        werkzeug.datei.format(
            S=spec,
            spec=spec,
            SKILL=skill_ordner.as_posix(),
            skill=skill_ordner.as_posix(),
            home=Path.home().as_posix(),
            py=_py_name(),
        )
    )
    if not datei.is_file():
        return _rot(
            werkzeug,
            f"Werkzeug fehlt ({datei})",
            "Skill/Hooks neu ausrollen (install.sh)",
        )
    for regel in regeln.deny:
        if _deny_trifft(regel, befehl):
            return _rot(
                werkzeug,
                f"permissions.deny `{regel}` verbietet `{befehl}`",
                "deny-Regel in den Settings lockern, dann erneut prüfen",
            )
    eingabe = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": befehl},
            "cwd": str(ordner),
            "session_id": "startklar",
        }
    )
    warnungen: list[str] = []
    for hook, timeout in regeln.hooks:
        name = f"Hook `{_kurz(hook, 120)}`"
        try:
            ergebnis = laeufer(hook, ordner, eingabe, timeout, {"CLAUDE_PROJECT_DIR": str(ordner)})
        except subprocess.TimeoutExpired:
            return _rot(
                werkzeug,
                f"{name} Timeout nach {timeout:.0f} s — Ergebnis unbekannt",
                "Hook von Hand mit dem Befehl prüfen (hängt er?), dann erneut prüfen",
            )
        except OSError as fehler:
            return _rot(
                werkzeug,
                f"{name} startet nicht: {fehler}",
                "bash/Hook-Programm installieren (Windows: Git-Bash), dann erneut prüfen",
            )
        grund = _block_grund(ergebnis)
        if grund is not None:
            return _rot(
                werkzeug,
                f"{name} blockt den Befehl: {_kurz(_schwaerzen(grund, geheim))}",
                "Hook-Regel für dieses Werkzeug freigeben (Hook-Datei/Settings anpassen), dann erneut prüfen",
            )
        if ergebnis.returncode != 0:
            fehlertext = _kurz(_schwaerzen(ergebnis.stderr, geheim), 120)
            warnungen.append(f"Hook `{_kurz(hook, 80)}` Exit {ergebnis.returncode} (kein Block): {fehlertext}")
    for warnung in warnungen:
        log.warning("%s: %s", werkzeug.name, warnung)
    trocken = befehl + werkzeug.trocken
    try:
        lauf = laeufer(trocken, ordner, None, WERKZEUG_TIMEOUT_S, {})
    except subprocess.TimeoutExpired:
        return _rot(
            werkzeug,
            f"Trockenlauf Timeout ({WERKZEUG_TIMEOUT_S} s)",
            f"`{trocken}` von Hand prüfen",
        )
    except OSError as fehler:
        return _rot(
            werkzeug,
            f"Trockenlauf startet nicht ({fehler})",
            "bash/node/python installieren (Windows: Git-Bash), dann erneut prüfen",
        )
    if lauf.returncode != 0:
        fehlertext = _kurz(_schwaerzen(lauf.stderr or lauf.stdout, geheim), 160)
        return _rot(
            werkzeug,
            f"Trockenlauf Exit {lauf.returncode}: {fehlertext}",
            f"`{trocken}` von Hand prüfen",
        )
    text = f"{werkzeug.name}: Hooks lassen durch, Trockenlauf ok"
    if warnungen:
        text += " — Warnung: " + "; ".join(warnungen)
    return Befund("werkzeug", True, text)


def werkzeug_probe(
    ordner: Path,
    spec: int,
    settings_dateien: Iterable[Path] | None = None,
    laeufer: Laeufer = _standard_laeufer,
    werkzeuge: Sequence[Werkzeug] = WERKZEUGE,
    environ: Mapping[str, str] | None = None,
    *,
    skill: Path | None = None,
) -> list[Befund]:
    """Spielt jedes Aufseher-Werkzeug durch deny-Regeln, PreToolUse-Hooks und trocken."""
    ordner = Path(ordner)
    dateien = standard_settings_dateien(ordner) if settings_dateien is None else list(settings_dateien)
    regeln = _regeln(dateien)
    befunde = [Befund("werkzeug", False, text, "Settings-Datei reparieren (gültiges JSON)") for text in regeln.fehler]
    ticket = _beispiel_ticket(ordner, spec)
    geheim = _geheime_werte(ordner, environ, spec)
    return befunde + [_probe_eines(w, ordner, spec, ticket, regeln, laeufer, geheim, skill=skill) for w in werkzeuge]
