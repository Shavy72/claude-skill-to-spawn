"""Ein tmux-Aufruf mit klaren Fehlern: :class:`TmuxFehler` trägt Unterbefehl + stderr.

Welches tmux läuft (``TO_SPAWN_TMUX``), entscheidet ``capo._tmux_befehl``. Hier steht
nur, wie ein Aufruf scheitert: Exit ≠ 0, Zeitüberschreitung oder kein tmux → :class:`TmuxFehler`,
fehlendes Fenster/Pane → :class:`FensterWeg`. ``start=True`` (neues Fenster) läuft über die
Start-Tür :mod:`to_spawn.starter` (#1406).
"""

from __future__ import annotations

import subprocess

from to_spawn import capo, starter


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


def spec_session_schliessen(spec: int) -> None:
    """tmux-Session ``spec-<S>`` samt allen Fenstern (``wache <S>``, ``bau <N>``) schließen (#546).

    Fehlt die Session schon, ist das kein Fehler. Windows-Terminal: kein Weg vorhanden, dort
    scheitert der Aufruf als :class:`TmuxFehler` (kein tmux) und der Aufrufer loggt nur.
    """
    try:
        aufrufen("kill-session", "-t", f"=spec-{spec}")
    except FensterWeg:
        return
    except TmuxFehler as fehler:
        if "can't find session" in fehler.stderr or "no server running" in fehler.stderr:
            return
        raise
