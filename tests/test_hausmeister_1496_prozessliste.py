"""Hausmeister #1496: Prozessliste am echten Windows-Laptop lesbar.

Befund 21.11.2026 am PC: Windows PowerShell 5.1 gibt ohne feste Ausgabe-Kodierung in
der Konsolen-Codepage aus. Typografische Anführungszeichen in einer ``CommandLine``
(Bau-Prompts enthalten „…“) werden dabei zu ``"`` — nach dem JSON-Escaping, also
kaputtes JSON. Folge: „Prozessliste nicht lesbar (PowerShell-JSON)“ bei jedem Ordner,
der Hausmeister löschte am Laptop nie etwas.

Weg-Test ohne Attrappe: echter Kindprozess mit solchen Zeichen, echte PowerShell.
Läuft nur unter Windows (auf dem Bau-Server gibt es keine PowerShell).
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from to_spawn import hausmeister_windows

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="echte PowerShell nur unter Windows")

MARKE = "hausmeister-1496 „Ticket“ – läuft"


def test_prozessliste_mit_typografischen_anfuehrungszeichen_lesbar() -> None:
    kind = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", MARKE],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        prozesse = hausmeister_windows._prozesse()
    finally:
        kind.kill()
        kind.wait(timeout=10)
    befehle = {pid: befehl for pid, befehl, _exe in prozesse}
    assert kind.pid in befehle
    assert MARKE in (befehle[kind.pid] or "")
