"""Ein tmux-Aufruf mit klaren Fehlern: :class:`TmuxFehler` trägt Unterbefehl + stderr.

Welches tmux läuft (``TO_SPAWN_TMUX``), entscheidet ``capo._tmux_befehl``. Hier steht
nur, wie ein Aufruf scheitert: Exit ≠ 0, Zeitüberschreitung oder kein tmux → :class:`TmuxFehler`,
fehlendes Fenster/Pane → :class:`FensterWeg`. ``start=True`` (neues Fenster) läuft über die
Start-Tür :mod:`to_spawn.starter` (#1406).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from to_spawn import capo, starter

log = logging.getLogger(__name__)


class TmuxFehler(RuntimeError):
    """Ein tmux-Aufruf ist gescheitert (Exit ≠ 0 oder Zeitüberschreitung).

    Trägt Unterbefehl und stderr, damit die Ergebniszeile sagt, was tmux meldete.
    """

    def __init__(self, unterbefehl: str, stderr: str) -> None:
        super().__init__(f"tmux {unterbefehl}: {stderr.strip() or 'ohne Meldung'}")
        self.unterbefehl = unterbefehl
        self.stderr = stderr


class FensterWeg(TmuxFehler):
    """Das Ziel-Fenster/-Pane gibt es nicht mehr (Session hat sich selbst beendet)."""


def aufrufen(*argumente: str, eingabe: str | None = None, start: bool = False) -> str:
    """Ein tmux-Aufruf; Ausgabe als Text, jeder Fehler als :class:`TmuxFehler`.

    ``start=True``: der Aufruf startet etwas (``new-window``) und geht durch die Start-Tür.
    """
    unterbefehl = argumente[0] if argumente else ""
    lauf_args = {"input": eingabe, "capture_output": True, "text": True, "check": True, "timeout": 30}
    try:
        if start:
            fertig = starter.starte([*capo._tmux_befehl(), *argumente], warten=True, **lauf_args)
        else:
            fertig = subprocess.run([*capo._tmux_befehl(), *argumente], **lauf_args)
    except subprocess.CalledProcessError as fehler:
        stderr = str(fehler.stderr or "")
        if "can't find window" in stderr or "can't find pane" in stderr:
            raise FensterWeg(unterbefehl, stderr) from fehler
        raise TmuxFehler(unterbefehl, stderr) from fehler
    except OSError as fehler:  # tmux fehlt (z. B. am PC: FileNotFoundError, #501)
        raise TmuxFehler(unterbefehl, f"nicht aufrufbar ({fehler})") from fehler
    except subprocess.TimeoutExpired as fehler:
        raise TmuxFehler(
            unterbefehl, f"keine Antwort nach {fehler.timeout} s"
        ) from fehler
    return fertig.stdout


def spec_session_schliessen(repo: Path, spec: int) -> None:
    """tmux-Session ``spec-<S>`` schließen, aber nur wenn sie nachweislich zu ``repo`` gehört (#546).

    Der Name trägt keinen Repo-Anteil (Skill-Issue 457): erst die Startpfade der Fenster
    abfragen, mindestens einer muss im Repo-Ordner liegen. Nicht belegbar (Abfrage scheitert,
    kein Pfad im Repo) → nichts schließen, eine Log-Zeile. Fehlt die Session, ist das kein Fehler.
    Windows-Terminal: kein Weg vorhanden, ohne tmux scheitert die Abfrage → nichts geschlossen.
    """
    ziel = f"=spec-{spec}"
    try:
        roh = aufrufen("list-panes", "-s", "-t", ziel, "-F", "#{pane_start_path}")
    except TmuxFehler as fehler:
        if "can't find session" not in fehler.stderr and "no server running" not in fehler.stderr:
            log.warning("Session spec-%s nicht geschlossen: Pfade nicht abfragbar (%s)", spec, fehler)
        return
    wurzel = repo.resolve()
    pfade = [Path(z).resolve() for z in roh.splitlines() if z.strip()]
    if not any(p == wurzel or wurzel in p.parents for p in pfade):
        log.warning("Session spec-%s nicht geschlossen: kein Fenster startet in %s", spec, wurzel)
        return
    try:
        aufrufen("kill-session", "-t", ziel)
    except FensterWeg:
        return
