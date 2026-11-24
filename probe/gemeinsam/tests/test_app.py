"""App-Tests der Probe-App (laufen mit ``unittest`` und mit ``pytest``)."""

from __future__ import annotations

import threading
import unittest
import urllib.request

from app import VERSION
from app.server import baue_server, versions_text


class VersionsText(unittest.TestCase):
    def test_nennt_version_und_umgebung(self) -> None:
        self.assertEqual(versions_text("live"), f"Probe-App {VERSION} (live)\n")


class Abruf(unittest.TestCase):
    def test_server_antwortet_mit_versions_text(self) -> None:
        server = baue_server(0, "test")
        faden = threading.Thread(target=server.serve_forever, daemon=True)
        faden.start()
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as antwort:
                self.assertEqual(antwort.read().decode("utf-8"), versions_text("test"))
        finally:
            server.shutdown()
            server.server_close()
            faden.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
