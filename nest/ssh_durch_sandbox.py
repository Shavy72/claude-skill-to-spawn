#!/usr/bin/env python3
"""ProxyCommand für ssh INNERHALB der Sandbox (srt) — HTTP-CONNECT über den Sandbox-Proxy.

In srt gibt es kein eigenes Netz, nur den Proxy der Sandbox (``$HTTPS_PROXY``, Form
``http://<nutzer>:<passwort>@localhost:<port>``). Dieses Skript öffnet dort einen
CONNECT-Tunnel zu ``<host>:<port>`` und kopiert danach stdin/stdout in beide Richtungen.
Die Proxy-Anmeldung steht nur im Anfrage-Kopf, nie in argv und nie in einer Ausgabe.

Aufruf (aus ``~/.ssh/config``): ``ssh_durch_sandbox.py %h %p``
"""

from __future__ import annotations

import base64
import logging
import os
import socket
import sys
import threading
from urllib.parse import unquote, urlsplit

log = logging.getLogger("ssh_durch_sandbox")
PUFFER = 65536


def _proxy() -> tuple[str, int, str | None]:
    """(Host, Port, Anmeldung ``nutzer:passwort`` oder None) aus ``$HTTPS_PROXY``."""
    roh = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    if not roh:
        raise SystemExit("ssh_durch_sandbox: kein HTTPS_PROXY — läuft nicht in der Sandbox")
    teile = urlsplit(roh if "://" in roh else f"http://{roh}")
    if not teile.hostname or not teile.port:
        raise SystemExit("ssh_durch_sandbox: HTTPS_PROXY ohne Host:Port — Abbruch")
    anmeldung = None
    if teile.username is not None:
        anmeldung = f"{unquote(teile.username)}:{unquote(teile.password or '')}"
    return teile.hostname, teile.port, anmeldung


def _tunnel(ziel_host: str, ziel_port: str) -> tuple[socket.socket, bytes]:
    host, port, anmeldung = _proxy()
    verbindung = socket.create_connection((host, port), timeout=30)
    kopf = [f"CONNECT {ziel_host}:{ziel_port} HTTP/1.1", f"Host: {ziel_host}:{ziel_port}"]
    if anmeldung is not None:
        wert = base64.b64encode(anmeldung.encode()).decode()
        kopf.append(f"Proxy-Authorization: Basic {wert}")
    verbindung.sendall(("\r\n".join(kopf) + "\r\n\r\n").encode())
    antwort = b""
    while b"\r\n\r\n" not in antwort:
        teil = verbindung.recv(PUFFER)
        if not teil:
            raise SystemExit("ssh_durch_sandbox: Proxy hat die Verbindung geschlossen")
        antwort += teil
    kopfteil, _, rest = antwort.partition(b"\r\n\r\n")
    status = kopfteil.split(b"\r\n", 1)[0].decode("latin-1")
    if len(status.split()) < 2 or status.split()[1] != "200":
        raise SystemExit(f"ssh_durch_sandbox: Proxy lehnt ab ({status})")
    verbindung.settimeout(None)
    return verbindung, rest


def _nach_aussen(verbindung: socket.socket) -> None:
    """stdin → Tunnel; bei Ende von stdin Schreibseite schließen."""
    try:
        while True:
            daten = os.read(0, PUFFER)
            if not daten:
                break
            verbindung.sendall(daten)
    except OSError as fehler:
        log.debug("stdin/Tunnel beendet: %s", fehler)
    finally:
        try:
            verbindung.shutdown(socket.SHUT_WR)
        except OSError as fehler:
            log.debug("shutdown: %s", fehler)


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        print("Aufruf: ssh_durch_sandbox.py <host> [<port>]", file=sys.stderr)
        return 2
    ziel_host, ziel_port = argv[0], argv[1] if len(argv) > 1 else "22"
    try:
        verbindung, rest = _tunnel(ziel_host, ziel_port)
    except OSError as fehler:
        print(f"ssh_durch_sandbox: Proxy nicht erreichbar ({type(fehler).__name__})", file=sys.stderr)
        return 1
    if rest:
        os.write(1, rest)
    threading.Thread(target=_nach_aussen, args=(verbindung,), daemon=True).start()
    while True:
        daten = verbindung.recv(PUFFER)
        if not daten:
            break
        os.write(1, daten)
    verbindung.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
