"""Gemeinsame Grundlagen der Startklar-Prüfung (Typen, Befehle, Git-Hilfen).

Ausgelagert aus ``to_spawn/startklar.py`` (#49 / App #461), damit alle
Startklar-Module unterhalb der Projektgrenze bleiben und gemeinsame Typen,
Konstanten, Befehlsformatierung und Git-Hilfen an genau einer Stelle leben.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from to_spawn import bash_wahl, konsole, prozessbaum

log = logging.getLogger("to_spawn.startklar")

SKILL = Path(__file__).resolve().parent.parent


def aufraeumen_pfad() -> Path:
    """Pfad des Staffel-Aufräumers (``~/.claude/hooks/…/aufraeumen.mjs``).

    Erst beim Aufruf aufgelöst (#68): ``Path.home()`` auf Modulebene ließ schon den
    Import scheitern, wenn kein Heimordner bestimmbar ist (Kindprozess ohne
    ``HOME``/``USERPROFILE``)."""
    return Path.home() / ".claude" / "hooks" / "smart-zone" / "staffel" / "aufraeumen.mjs"


HOOK_TIMEOUT_S = 20
WERKZEUG_TIMEOUT_S = 60
GIT_TIMEOUT_S = 20
GH_TIMEOUT_S = 30
INTERPRETER_TIMEOUT_S = 60
VENV_TIMEOUT_S = 300
PIP_TIMEOUT_S = 900
#: Werte kürzer als das werden nicht geschwärzt (sonst trifft „1“ jede Zahl).
GEHEIM_MIN_LAENGE = 6
#: Umgebungsvariable zum Abschalten des Gates (nur Notfall, wird im Log gewarnt).
SCHALTER = "TO_SPAWN_STARTKLAR"

# --- Aufseher-Befehle: eine Quelle für Prompt (skripte/wache.py) und Probe -------
#: Platzhalter wie im Aufseher-Prompt: ``{S}`` Spec, ``{SKILL}`` Skill-Ordner,
#: ``<N>`` Ticket. ``python`` wird unter Linux zu ``python3`` (``aufseher_vorlage``).
BEFEHL_AUFRAEUMEN = "node ~/.claude/hooks/smart-zone/staffel/aufraeumen.mjs --spec {S}"
BEFEHL_NEUSTART = "python {SKILL}/to_spawn.py neustart {S} <N>"
BEFEHL_RESPAWN = "python {SKILL}/to_spawn.py respawn {S} <N>"

#: Läufer: (Shell-Befehl, cwd, stdin, timeout, Zusatz-Umgebung) → Ergebnis.
#: Wirft ``subprocess.TimeoutExpired``/``OSError`` wie ``subprocess.run``.
Laeufer = Callable[
    [str, Path, str | None, float, Mapping[str, str]],
    "subprocess.CompletedProcess[str]",
]
#: Issue-Leser: (Ordner, Ticketnummer) → Issue-Text; wirft bei Fehler.
IssueLeser = Callable[[Path, str], str]


@dataclass(frozen=True)
class Befund:
    """Ein Prüfergebnis. ``bereich`` ist "venv", "konfig", "schlüssel" oder "werkzeug"."""

    bereich: str
    ok: bool
    text: str
    behebung: str = ""


@dataclass(frozen=True)
class Werkzeug:
    """Ein Aufseher-Werkzeug.

    ``befehl`` ist die Vorlage, genau so, wie der Aufseher sie tippt (Platzhalter
    ``{S}``/``{spec}``, ``{SKILL}``/``{skill}``, ``{home}``, ``{py}``, ``<N>``);
    ``trocken`` wird für den Trockenlauf angehängt. ``datei`` muss existieren.
    """

    name: str
    befehl: str
    datei: str
    trocken: str = ""


def _py_name() -> str:
    return "python" if os.name == "nt" else "python3"


#: Die Werkzeuge aus dem Aufseher-Prompt (skripte/wache.py), je mit Trockenlauf.
WERKZEUGE: tuple[Werkzeug, ...] = (
    Werkzeug(
        "aufraeumen",
        BEFEHL_AUFRAEUMEN,
        "{home}/.claude/hooks/smart-zone/staffel/aufraeumen.mjs",
        " --dry-run",
    ),
    Werkzeug("neustart", BEFEHL_NEUSTART, "{SKILL}/to_spawn.py", " --help"),
    Werkzeug("respawn", BEFEHL_RESPAWN, "{SKILL}/to_spawn.py", " --help"),
)


def aufseher_vorlage(text: str) -> str:
    """Prompt-Vorlage fürs Betriebssystem: Linux kennt nur ``python3``.

    Ersetzt nur das nackte Befehlswort ``python``, nie das Ende eines Pfads
    (``~/.local/bin/python`` ist ein vorhandener Interpreter, ein daraus
    gebautes ``…/python3`` gibt es nicht, #1502).
    """
    if os.name == "nt":
        return text
    return re.sub(r"(?<![\w/\\.~-])python(?= )", _py_name(), text)


def skill_pfad(skill: Path | None = None) -> str:
    """Skill-Ordner, wie er in Aufseher-Befehlen steht (posix, für bash gequotet)."""
    ziel = SKILL if skill is None else skill
    return shlex.quote(ziel.as_posix())


def befehl_text(werkzeug: Werkzeug, spec: int, ticket: int | str, *, skill: Path | None = None) -> str:
    """Befehl genau so, wie er im Aufseher-Prompt steht (``<N>`` → ``ticket``)."""
    heim = shlex.quote(Path.home().as_posix())
    skill_text = skill_pfad(skill)
    return (
        aufseher_vorlage(werkzeug.befehl)
        .format(
            S=spec,
            spec=spec,
            SKILL=skill_text,
            skill=skill_text,
            home=heim,
            py=_py_name(),
        )
        .replace("<N>", str(ticket))
    )


def _standard_laeufer(
    befehl: str,
    cwd: Path,
    eingabe: str | None,
    timeout: float,
    zusatz: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    """Probe-Befehl in bash (Tür ``to_spawn.bash_wahl``); fehlt bash → ``FileNotFoundError``."""
    return bash_wahl.lauf(
        ["-c", befehl],
        cwd=str(cwd),
        input=eingabe,
        capture_output=True,
        **konsole.UNTERPROZESS_TEXT,
        timeout=timeout,
        env={**os.environ, **zusatz},
    )


def _kurz(text: str, laenge: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= laenge else text[: laenge - 1] + "…"


def _schwaerzen(text: str, geheim: Iterable[str]) -> str:
    for wert in sorted(set(geheim), key=len, reverse=True):
        if len(wert) >= GEHEIM_MIN_LAENGE:
            text = text.replace(wert, "***")
    return text


# --- Git-Hilfen ----------------------------------------------------------------


def _git_lauf(ordner: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """``git <args>`` in ``ordner``; ``None``, wenn git nicht startet/hängt."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(ordner),
            capture_output=True,
            **konsole.UNTERPROZESS_PFADE,
            timeout=GIT_TIMEOUT_S,
            check=False,
            **prozessbaum.fensterlos(),
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("git %s in %s fehlgeschlagen: %s", args[0], ordner, fehler)
        return None


def _git_dirs(ordner: Path) -> tuple[Path, Path] | None:
    """(gemeinsames .git, eigenes .git) von ``ordner`` oder ``None``."""
    ergebnis = _git_lauf(ordner, "rev-parse", "--path-format=absolute", "--git-common-dir", "--git-dir")
    if ergebnis is None or ergebnis.returncode != 0:
        return None
    zeilen = [z for z in ergebnis.stdout.splitlines() if z.strip()]
    if len(zeilen) != 2:
        return None
    return Path(zeilen[0]).resolve(), Path(zeilen[1]).resolve()


def hauptrepo(ordner: Path) -> Path | None:
    """Hauptrepo-Ordner, wenn ``ordner`` ein Git-Worktree ist; sonst ``None``."""
    dirs = _git_dirs(ordner)
    if dirs is None or dirs[0] == dirs[1]:
        return None
    return dirs[0].parent


def _ist_ignoriert(ordner: Path, pfad: str) -> bool | None:
    ergebnis = _git_lauf(ordner, "check-ignore", "-q", "--no-index", pfad)
    if ergebnis is None or ergebnis.returncode not in (0, 1):
        return None
    return ergebnis.returncode == 0


def _venv_ausschliessen(ordner: Path) -> str:
    """Trägt ``/.venv`` in ``<git-common-dir>/info/exclude`` ein (idempotent).

    Ein ``.venv``-Symlink ist eine Datei — ``.venv/`` in ``.gitignore`` greift nicht,
    und anders als echte venvs bringt er keine eigene ``.gitignore`` mit.
    """
    dirs = _git_dirs(ordner)
    if dirs is None:
        return " — Warnung: kein Git-Ordner, .venv nicht ausgeschlossen"
    exclude = dirs[0] / "info" / "exclude"
    try:
        zeilen = exclude.read_text(encoding="utf-8").splitlines() if exclude.is_file() else []
        if "/.venv" in zeilen:
            return ""
        exclude.parent.mkdir(parents=True, exist_ok=True)
        vorher = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        trenner = "" if not vorher or vorher.endswith("\n") else "\n"
        exclude.write_text(f"{vorher}{trenner}/.venv\n", encoding="utf-8")
    except OSError as fehler:
        log.warning("%s nicht beschreibbar: %s", exclude, fehler)
        return f" — Warnung: .venv nicht in .gitignore und {exclude} nicht beschreibbar (nicht committen!)"
    return f" — .venv stand nicht in .gitignore → /.venv in {exclude} eingetragen"
