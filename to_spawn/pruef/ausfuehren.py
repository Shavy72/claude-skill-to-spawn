"""Einen Prüf-Befehl ausführen und messen (Paket-intern, #99).

Nur der einfache Lauf: ein Prozess, Ausgabe gesammelt, Wandzeit gemessen. Worker-Zahl unter
RAM-Deckel, worksteal und Aufteilung nach Laufzeit kommen in 3b-3.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

#: Exit-Code, wenn der Befehl gar nicht startet (wie die Shell bei „nicht gefunden“).
NICHT_GESTARTET = 127


@dataclass(frozen=True)
class Lauf:
    code: int
    ausgabe: str
    dauer_s: float


def lauf(befehl: list[str], ordner: Path) -> Lauf:
    """Startet ``befehl`` (Liste, keine Shell) in ``ordner``; stdout und stderr zusammen."""
    # ponytail: keine Zeitgrenze, ein hängender Test hängt die Tür; Upgrade in 3b-3 (Ausführen).
    start = time.monotonic()
    try:
        erg = subprocess.run(
            befehl,
            cwd=ordner,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as fehler:
        return Lauf(NICHT_GESTARTET, f"{befehl[0]}: {fehler}", time.monotonic() - start)
    return Lauf(erg.returncode, erg.stdout or "", time.monotonic() - start)
