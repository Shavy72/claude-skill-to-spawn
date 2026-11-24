"""Winziger Web-Server der Probe-App: ``GET /`` liefert den Versions-Text.

Start: ``python -m app.server --port <N> [--umgebung lokal|staging|live]``.
Nur Standardbibliothek, damit das Lauf-Repo ohne Installation startet.
"""

from __future__ import annotations

import argparse
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import VERSION

log = logging.getLogger("probe-app")


def versions_text(umgebung: str) -> str:
    """Antwort-Text eines Abrufs, z. B. ``Probe-App 1.0.0 (live)``."""
    return f"Probe-App {VERSION} ({umgebung})\n"


def baue_server(port: int, umgebung: str = "lokal") -> ThreadingHTTPServer:
    """Server an ``127.0.0.1:port`` (``port=0`` = freier Port); Start mit ``serve_forever``."""
    text = versions_text(umgebung).encode("utf-8")

    class Antwort(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in ("/", "/version"):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(text)))
            self.end_headers()
            self.wfile.write(text)

        def log_message(self, format: str, *args: object) -> None:
            log.info(format, *args)

    return ThreadingHTTPServer(("127.0.0.1", port), Antwort)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe-App starten")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--umgebung", default="lokal")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    server = baue_server(args.port, args.umgebung)
    log.info("Probe-App %s (%s) auf Port %s", VERSION, args.umgebung, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("beendet")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
