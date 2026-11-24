"""Komplett-Prüfung der Probe-App: alle App-Tests plus Rauchtest (App starten, ein Abruf).

``python skripte/pruefen_komplett.py`` — Exit 0 nur, wenn beides grün ist. Der Rauchtest nutzt
einen freien Port, stört also weder Staging noch die Übungs-Live-App.
"""

from __future__ import annotations

import logging
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
