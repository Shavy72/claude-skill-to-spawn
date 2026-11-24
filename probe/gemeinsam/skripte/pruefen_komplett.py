"""Komplett-Prüfung der Probe-App: alle App-Tests plus Rauchtest (App starten, ein Abruf).

``python skripte/pruefen_komplett.py`` — Exit 0 nur, wenn beides grün ist. Der Rauchtest nutzt
einen freien Port, stört also weder Staging noch die Übungs-Live-App.

Aus einem Bau-Ticket (``TO_SPAWN_TICKET`` gesetzt, #474) lehnt es mit Exit 2 ab: je Ticket nur die
schnelle Prüfung; die Komplett-Prüfung startet der Skill genau einmal je Spec (dort ohne die
Variable). Gleiche Regel wie ``to_spawn/komplett_lauf.py`` im Skill.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
log = logging.getLogger("pruefen_komplett")


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def rauchtest() -> bool:
    port = _freier_port()
    prozess = subprocess.Popen(
        [sys.executable, "-m", "app.server", "--port", str(port), "--umgebung", "rauchtest"],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ende = time.monotonic() + 15
        while time.monotonic() < ende:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as antwort:
                    text = antwort.read().decode("utf-8")
                print(f"Rauchtest: {text.strip()}")
                return "(rauchtest)" in text
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.2)
        log.error("Rauchtest: App antwortet nicht auf Port %s", port)
        return False
    finally:
        prozess.terminate()
        prozess.wait(timeout=10)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "TO_SPAWN_TICKET" in os.environ:
        log.error(
            "Komplett-Prüfung nicht aus einem Bau-Ticket (TO_SPAWN_TICKET gesetzt). Handlung: je "
            "Ticket nur die schnelle Prüfung (python -m unittest discover -s tests -t .); die "
            "Komplett-Prüfung läuft genau einmal je Spec über `to_spawn.py komplett <S> --moment "
            "vor-live` bzw. im capo-Takt, sobald alle Tickets auf Staging sind."
        )
        return 2
    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=REPO,
        check=False,
    )
    if tests.returncode != 0:
        log.error("App-Tests rot (Exit %s)", tests.returncode)
        return 1
    return 0 if rauchtest() else 1


if __name__ == "__main__":
    raise SystemExit(main())
