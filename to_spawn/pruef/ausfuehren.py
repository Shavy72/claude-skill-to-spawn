"""Einen Prüf-Befehl ausführen und messen (Paket-intern, #99).

Ein Prozess, Ausgabe gesammelt, Wandzeit gemessen. ``worker()`` sagt, wie viele parallele
Test-Worker der Rechner gerade trägt (CPU-Kerne unter RAM-Deckel, 3b-3 #102); die Adapter geben
die Zahl an ihr Werkzeug weiter (pytest-xdist ``--dist worksteal``).
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from to_spawn import prozessbaum, speicher, temp_ordner

#: RAM je Test-Worker (MiB), Grundlage des Deckels.
# ponytail: Schätzwert, nicht gemessen; Upgrade: Spitze je Worker messen (3b-5).
JE_WORKER_MIB = 512
#: Ohne lesbaren freien Speicher (z. B. Windows) höchstens so viele Worker.
OHNE_RAM_WERT = 2

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
            env={**os.environ, **temp_ordner.umgebung()},  # #136: Temp nie auf die RAM-Platte
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **prozessbaum.fensterlos(),
        )
    except OSError as fehler:
        return Lauf(NICHT_GESTARTET, f"{befehl[0]}: {fehler}", time.monotonic() - start)
    return Lauf(erg.returncode, erg.stdout or "", time.monotonic() - start)


def worker() -> int:
    """Parallele Test-Worker: CPU-Kerne, gedeckelt durch freien RAM / ``JE_WORKER_MIB``; mind. 1."""
    kerne = os.cpu_count() or 1
    frei = speicher.frei_mib()
    deckel = OHNE_RAM_WERT if frei is None else frei // JE_WORKER_MIB
    return max(1, min(kerne, deckel))
