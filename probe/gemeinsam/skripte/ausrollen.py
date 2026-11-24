"""Stand der Probe-App nach Staging oder in die Übungs-Live-App bringen bzw. wieder aufräumen.

``python skripte/ausrollen.py <staging|live>``: ``app/`` in den Ordner ``<umgebung>/`` kopieren,
dort im Hintergrund starten, per Abruf belegen (Zeile ``Beleg: <url> -> <text>``).
``python skripte/ausrollen.py <staging|live> --aufraeumen``: Prozess beenden, Ordner löschen.

Die Ports stehen nur in ``ports.json`` (von ``anlegen.sh`` geschrieben). Ohne Staging-Port
(Variante ``ohne-staging``) lehnt der Staging-Schritt mit Exit 2 ab.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
log = logging.getLogger("ausrollen")


def _port(umgebung: str) -> int | None:
    ports = json.loads((REPO / "ports.json").read_text(encoding="utf-8"))
    wert = ports.get(umgebung)
    return int(wert) if wert else None


def _abruf(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=2) as antwort:
            return antwort.read().decode("utf-8")
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return None


def _warte(url: str, erreichbar: bool, sekunden: float = 15.0) -> str | None:
    ende = time.monotonic() + sekunden
    while True:
        text = _abruf(url)
        if (text is not None) == erreichbar or time.monotonic() > ende:
            return text
        time.sleep(0.2)


def aufraeumen(umgebung: str, port: int) -> None:
    ordner = REPO / umgebung
    pid_datei = ordner / "server.pid"
    if pid_datei.is_file():
        pid = int(pid_datei.read_text(encoding="utf-8").strip())
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as fehler:
            log.info("Prozess %s schon weg (%s)", pid, fehler)
        _warte(f"http://127.0.0.1:{port}/", erreichbar=False)
    if ordner.exists():
        shutil.rmtree(ordner, ignore_errors=True)
    log.info("%s aufgeräumt", umgebung)


def ausrollen(umgebung: str, port: int) -> int:
    aufraeumen(umgebung, port)
    ordner = REPO / umgebung
    shutil.copytree(REPO / "app", ordner / "app", ignore=shutil.ignore_patterns("__pycache__"))
    optionen: dict[str, object] = {}
    if os.name == "nt":
        optionen["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )
    else:
        optionen["start_new_session"] = True
    with open(ordner / "server.log", "ab") as protokoll:
        prozess = subprocess.Popen(
            [sys.executable, "-m", "app.server", "--port", str(port), "--umgebung", umgebung],
            cwd=ordner,
            stdin=subprocess.DEVNULL,
            stdout=protokoll,
            stderr=subprocess.STDOUT,
            **optionen,  # type: ignore[arg-type]
        )
    (ordner / "server.pid").write_text(str(prozess.pid), encoding="utf-8")
    url = f"http://127.0.0.1:{port}/"
    text = _warte(url, erreichbar=True)
    if text is None or f"({umgebung})" not in text:
        log.error("%s antwortet nicht wie erwartet: %r", url, text)
        aufraeumen(umgebung, port)
        return 1
    print(f"Beleg: {url} -> {text.strip()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("umgebung", choices=("staging", "live"))
    parser.add_argument("--aufraeumen", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    port = _port(args.umgebung)
    if port is None:
        log.error(
            "Kein Port für %s in ports.json — dieses Repo hat keine Staging-App.", args.umgebung
        )
        return 2
    if args.aufraeumen:
        aufraeumen(args.umgebung, port)
        return 0
    return ausrollen(args.umgebung, port)


if __name__ == "__main__":
    raise SystemExit(main())
