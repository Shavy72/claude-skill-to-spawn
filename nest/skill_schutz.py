#!/usr/bin/env python3
"""PreToolUse-Hook: Skill-Dateien auf dem Bau-Server schreibgeschützt (duoplus-management#325).

Eine Bau-Session darf ``~/.claude/skills/`` nicht ändern — Skill-Änderungen entstehen
am PC, ``/to-spawn`` schiebt sie selbst. Liest das Hook-JSON von stdin
(``tool_name``, ``tool_input``, ``cwd``) und verweigert mit Exit 2 (Grund auf stderr):

* Edit/Write/MultiEdit/NotebookEdit, deren ``file_path``/``notebook_path`` unter
  ``<home>/.claude/skills/`` liegt (``~`` und relative Pfade gegen ``cwd`` aufgelöst).
* Bash-Befehle, deren SCHREIBZIEL unter den Skills liegt (nicht bloß das Vorkommen
  des Pfads — ``python3 ~/.claude/skills/to-spawn/to_spawn.py … > /tmp/log`` bleibt
  erlaubt). Erkannte Schreibmuster:
    - Umleitung ``>``/``>>``/``&>`` (Regex ``_UMLEITUNG``) — Ziel wird geprüft
    - ``tee``, ``touch``, ``rm``, ``rmdir``, ``unlink``, ``mkdir``, ``truncate``,
      ``chmod``, ``chown``, ``patch`` — jedes Nicht-Options-Argument
    - ``cp``, ``install``, ``ln``, ``rsync`` — letztes Argument (Ziel);
      ``mv`` — jedes Argument (die Quelle verschwindet auch)
    - ``sed -i``/``--in-place``, ``perl -i`` — jedes Nicht-Options-Argument
    - ``dd of=…``; ``tar -x``/``--extract`` mit Skill-Pfad (z. B. ``-C``)
    - ``git`` mit schreibendem Unterbefehl (``_GIT_SCHREIBEND``) im Skill-Ordner
      (``-C <pfad>``, vorheriges ``cd`` oder ``cwd``)
    - ``python``/``python3`` mit Inline-Code (``-c`` oder ``- <<``), der schreibt
      (Regex ``_PY_SCHREIBT``) und den Skill-Pfad nennt
  ``cd <pfad>`` im Befehl verschiebt das Arbeitsverzeichnis für die Folgeteile.

Alles andere und kaputtes JSON → Exit 0 (fail-open, bei kaputtem JSON eine
stderr-Zeile). Unerwartete Ausnahme beim Prüfen → Exit 2 (verweigern, geloggt).
``SKILL_SCHUTZ_WURZEL`` ersetzt das Home (für Tests).

ponytail: Regex-/Token-Heuristik, keine Shell-Semantik (eval, Variablen, Heredocs
anderer Interpreter rutschen durch); Upgrade: Skill-Ordner zusätzlich per
Dateirechte (chattr/read-only-Mount) sperren.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.WARNING, format="skill_schutz: %(message)s", stream=sys.stderr
)
log = logging.getLogger("skill_schutz")

GRUND = "Skill-Dateien sind auf dem Bau-Server schreibgeschützt — Änderung am PC machen, /to-spawn schiebt sie selbst."
DATEI_WERKZEUGE = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

_TRENNER = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
_UMLEITUNG = re.compile(r"(?:^|[^<>&\d])(?:\d|&)?>>?\|?\s*([^\s;&|<>]+)")
_ALLE_ARGS = {
    "tee",
    "touch",
    "rm",
    "rmdir",
    "unlink",
    "mkdir",
    "truncate",
    "chmod",
    "chown",
    "patch",
    "mv",
}
_ZIEL_LETZTES = {"cp", "install", "ln", "rsync"}
_GIT_SCHREIBEND = {
    "checkout", "switch", "apply", "reset", "restore", "pull", "merge", "am", "rebase",
    "stash", "clean", "rm", "mv", "commit", "cherry-pick", "revert", "add",
}  # fmt: skip
_PY_SCHREIBT = re.compile(
    r"open\([^)]*['\"][wax]\+?b?['\"]|write_text|write_bytes|shutil\.|os\.(?:remove|unlink|rename|replace)"
    r"|\.unlink\(|\.rename\(|Path\([^)]*\)\.replace\("
)
_PRAEFIXE = {"sudo", "env", "nohup", "command", "exec", "time"}


def _home() -> Path:
    return Path(os.environ.get("SKILL_SCHUTZ_WURZEL") or Path.home())


def _wurzel() -> Path:
    return (_home() / ".claude" / "skills").resolve()


def _aufloesen(pfad: str, cwd: Path) -> Path:
    """``~``/``$HOME`` gegen das Home, relative Pfade gegen ``cwd`` auflösen."""
    pfad = pfad.strip("'\"")
    for kuerzel in ("~", "$HOME", "${HOME}"):
        if pfad == kuerzel or pfad.startswith(kuerzel + "/"):
            pfad = str(_home()) + pfad[len(kuerzel) :]
            break
    p = Path(pfad)
    if not p.is_absolute():
        p = cwd / p
    return p.resolve()


def _in_skills(pfad: str, cwd: Path) -> bool:
    if not pfad:
        return False
    try:
        return _aufloesen(pfad, cwd).is_relative_to(_wurzel())
    except (OSError, ValueError):
        return ".claude/skills" in pfad


def _tokens(teil: str) -> list[str]:
    try:
        return shlex.split(teil)
    except ValueError:
        return teil.split()


def _teil_schreibt(tokens: list[str], cwd: Path) -> bool:
    while tokens and (
        tokens[0] in _PRAEFIXE or ("=" in tokens[0] and not tokens[0].startswith("-"))
    ):
        tokens = tokens[1:]
    if not tokens:
        return False
    befehl, args = Path(tokens[0]).name, tokens[1:]
    nicht_optionen = [a for a in args if not a.startswith("-")]
    if befehl in _ALLE_ARGS:
        return any(_in_skills(a, cwd) for a in nicht_optionen)
    if befehl in _ZIEL_LETZTES:
        return bool(nicht_optionen) and _in_skills(nicht_optionen[-1], cwd)
    if befehl in ("sed", "perl") and any(
        re.match(r"^-[a-zA-Z]*i", a) or a.startswith("--in-place") for a in args
    ):
        return any(_in_skills(a, cwd) for a in nicht_optionen)
    if befehl == "dd":
        return any(a.startswith("of=") and _in_skills(a[3:], cwd) for a in args)
    if befehl == "tar" and any(
        a == "--extract" or re.match(r"^-?[a-zA-Z]*x", a) for a in args[:2]
    ):
        return any(_in_skills(a, cwd) for a in nicht_optionen) or _in_skills(".", cwd)
    if befehl == "git":
        ordner = cwd
        rest = list(args)
        while rest and rest[0].startswith("-"):
            if rest[0] == "-C" and len(rest) > 1:
                ordner = _aufloesen(rest[1], cwd)
                rest = rest[2:]
            else:
                rest = rest[1:]
        return (
            bool(rest) and rest[0] in _GIT_SCHREIBEND and _in_skills(str(ordner), cwd)
        )
    if re.fullmatch(r"python\d*(?:\.\d+)?", befehl) and "-c" in args:
        code = args[args.index("-c") + 1] if args.index("-c") + 1 < len(args) else ""
        return bool(_PY_SCHREIBT.search(code)) and (
            ".claude/skills" in code or _in_skills(".", cwd)
        )
    return False


def bash_schreibt_in_skills(befehl: str, cwd: Path) -> bool:
    """True, wenn der Bash-Befehl erkennbar in den Skill-Ordner schreibt."""
    # Python-Heredoc (``python3 - <<EOF … EOF``): Code steht hinter ``<<``.
    heredoc = re.search(r"python\d*(?:\.\d+)?\s+-\s*<<", befehl)
    if heredoc:
        code = befehl[heredoc.end() :]
        if _PY_SCHREIBT.search(code) and (
            ".claude/skills" in code or _in_skills(".", cwd)
        ):
            return True
        befehl = befehl[: heredoc.start()]
    aktuell = cwd
    for teil in _TRENNER.split(befehl):
        if not teil:
            continue
        for ziel in _UMLEITUNG.findall(teil):
            if _in_skills(ziel, aktuell):
                return True
        tokens = [t for t in _tokens(teil) if not re.match(r"^(?:\d|&)?>", t)]
        if tokens[:1] == ["cd"] and len(tokens) > 1:
            aktuell = _aufloesen(tokens[1], aktuell)
            continue
        if _teil_schreibt(tokens, aktuell):
            return True
    return False


def pruefe(daten: dict[str, Any]) -> bool:
    """True = verweigern."""
    werkzeug = daten.get("tool_name")
    eingabe = daten.get("tool_input") or {}
    cwd = Path(daten.get("cwd") or os.getcwd())
    if werkzeug in DATEI_WERKZEUGE:
        pfad = eingabe.get("file_path") or eingabe.get("notebook_path") or ""
        return _in_skills(str(pfad), cwd)
    if werkzeug == "Bash":
        return bash_schreibt_in_skills(str(eingabe.get("command") or ""), cwd)
    return False


def main() -> int:
    try:
        daten = json.loads(sys.stdin.read())
        if not isinstance(daten, dict):
            raise ValueError("kein JSON-Objekt")
    except (ValueError, OSError) as fehler:
        log.warning("Hook-JSON unlesbar (%s) — durchgelassen.", fehler)
        return 0
    try:
        verweigern = pruefe(daten)
    except Exception as fehler:  # noqa: BLE001 — Schutz-Hook: im Zweifel verweigern
        log.error(
            "Prüfung abgebrochen (%s: %s) — verweigert.", type(fehler).__name__, fehler
        )
        return 2
    if verweigern:
        sys.stderr.write(f"{GRUND}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
