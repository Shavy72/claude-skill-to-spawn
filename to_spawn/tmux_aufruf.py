"""Ein tmux-Aufruf mit klaren Fehlern: :class:`TmuxFehler` trägt Unterbefehl + stderr.

Welches tmux läuft (``TO_SPAWN_TMUX``), entscheidet ``capo._tmux_befehl``. Hier steht
nur, wie ein Aufruf scheitert: Exit ≠ 0, Zeitüberschreitung oder kein tmux → :class:`TmuxFehler`,
fehlendes Fenster/Pane → :class:`FensterWeg`.
"""

from __future__ import annotations

import subprocess

from to_spawn import capo


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


def aufrufen(*argumente: str, eingabe: str | None = None) -> str:
    """Ein tmux-Aufruf; Ausgabe als Text, jeder Fehler als :class:`TmuxFehler`."""
    unterbefehl = argumente[0] if argumente else ""
    try:
        fertig = subprocess.run(
            [*capo._tmux_befehl(), *argumente],
            input=eingabe,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
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
