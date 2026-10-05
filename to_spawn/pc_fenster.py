"""Bau-Sessions am PC (Windows): finden, still-Zeit lesen, beenden, im neuen Tab starten (#501, E10).

Am PC gibt es kein tmux. Jede Bau-Session läuft in einem Windows-Terminal-Tab ``bau <N>``
(``pwsh`` → ``bau.py <N>`` → ``claude``). In ein Fenster tippen oder seinen Bildschirm
lesen geht hier nicht — deshalb kennt der PC kein Anstupsen: Ablösung heißt alte Session
samt ``bau.py`` beenden und einen neuen Tab mit dem Handoff-Auftrag öffnen.

Einzige Stelle der Plattform-Weiche ist :func:`am_pc`; Aufrufer (``respawn``,
``aufseher_stand``, ``leiter``) fragen nur sie. Alles Windows-Wissen (Prozessliste,
``taskkill``, ``wt``-Befehl, Transkript-Zeit) steht hier.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

from to_spawn import prozessbaum, sessions_datei

log = logging.getLogger(__name__)

#: Höchste Wartezeit, bis ein beendeter Prozessbaum wirklich weg ist.
BEENDEN_MAX_S = 15.0
_TAKT_S = 0.3


def am_pc() -> bool:
    """Läuft der Skill am PC (Windows, kein tmux)? Die eine Plattform-Weiche."""
    return sys.platform == "win32"


class Alte(NamedTuple):
    """Laufende Bau-Session eines Tickets am PC (``None`` = nicht gefunden)."""

    bau_pid: int | None  # ``bau.py <N>`` — wird mitbeendet, sonst startet es eine Folge-Runde
    session_pid: int | None  # ``claude`` unter ``bau.py`` (oder verwaist ohne ``bau.py``)


def alte_session(repo: Path, spec: int, ticket: int) -> Alte:
    """``bau.py`` + Claude-Session von ``ticket`` aus der Prozessliste (wie ``sessions <S>``)."""
    from to_spawn import spawn  # spät: spawn zieht capo/probesitz nach

    os.environ["TO_SPAWN_REPO"] = str(repo)
    eintrag = spawn.ticket_eintrag(spec, ticket)
    return Alte(_pid(eintrag.pid), _pid(eintrag.session_pid))


def _pid(wert: object) -> int | None:
    try:
        return int(wert) if wert else None  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def baum_beenden(pid: int) -> bool:
    """Beendet ``pid`` mit allen Kindern (``taskkill /T /F``) und wartet, bis er weg ist.

    True = weg (auch: war schon weg). Ohne Fenster gestartet (CREATE_NO_WINDOW).
    """
    if not prozessbaum._lebt_windows(pid):
        return True
    erg = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
        **prozessbaum.ohne_fenster(),
    )
    log.info("taskkill /T /F %d → Exit %d %s", pid, erg.returncode, (erg.stderr or "").strip())
    ende = time.monotonic() + BEENDEN_MAX_S
    while time.monotonic() < ende:
        if not prozessbaum._lebt_windows(pid):
            return True
        time.sleep(_TAKT_S)
    return False


def tab_befehl(repo: Path, ticket: int, auftrag: str) -> list[str]:
    """argv für einen neuen Tab ``bau <N>`` wie ``neustart`` am PC, mit Remote Control."""
    from to_spawn import spawn

    return spawn.wt_tab_befehl(repo, ticket, auftrag, "--remote-control")


def tab_starten(repo: Path, ticket: int, auftrag: str) -> None:
    """Öffnet den Tab ``bau <N>`` (der Tab ist gewollt sichtbar, ``wt`` selbst ohne Konsole).

    :class:`OSError`, wenn ``wt`` fehlt oder mit Exit ≠ 0 endet.
    """
    befehl = tab_befehl(repo, ticket, auftrag)
    log.info("Neuer Tab: %s", befehl)
    erg = subprocess.run(
        befehl, cwd=str(repo), capture_output=True, text=True, check=False, **prozessbaum.ohne_fenster()
    )
    if erg.returncode != 0:
        raise OSError(f"wt Exit {erg.returncode}: {(erg.stderr or '').strip() or 'ohne Meldung'}")


def still_s(repo: Path, ticket: int, jetzt: float) -> float | None:
    """Sekunden seit dem letzten Transkript-Eintrag der Session von ``bau <N>``.

    Quelle: ``.to-spawn/sessions/<N>.json`` (Session-ID + cwd, schreibt ``bau.py``) →
    ``~/.claude/projects/<cwd>/<id>.jsonl``. ``None`` = keine Session-Datei/kein Transkript.
    """
    from to_spawn.waechter_lauf import transkript_ordner  # spät: Kreis über respawn

    daten = sessions_datei.lesen(repo, str(ticket))
    if not daten or not daten.get("session_id") or not daten.get("cwd"):
        return None
    datei = transkript_ordner(Path(str(daten["cwd"]))) / f"{daten['session_id']}.jsonl"
    try:
        return max(0.0, jetzt - datei.stat().st_mtime)
    except OSError:
        return None
