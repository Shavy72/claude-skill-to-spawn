"""Aufzeichnungs-Ersatz für ``claude`` (externer Dienst, #316).

Hängt Aufruf und Argumente als JSON-Zeile an ``$CLAUDE_AUFZEICHNUNG`` an.
Ist ``$CLAUDE_AUFZEICHNUNG_NOTIZ`` gesetzt, schreibt es wie die echte Session am
Ende den Notizzettel über die CLI: ``to_spawn.py takt <S> --notiz <text>``
(``<S>`` aus ``$CLAUDE_AUFZEICHNUNG_SPEC``). Exit-Code aus ``$CLAUDE_AUFZEICHNUNG_EXIT``.
Zeichnet stdin (Prompt, Fixrunde) und die Ticket-Variablen der Umgebung auf;
``$CLAUDE_AUFZEICHNUNG_SCHLAF`` (Sekunden) lässt es hängen wie ein stehender Claude.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent


def main(argv: list[str]) -> int:
    ziel = Path(os.environ["CLAUDE_AUFZEICHNUNG"])
    eingabe = sys.stdin.buffer.read().decode("utf-8") if sys.stdin else ""
    umgebung = {
        k: os.environ.get(k)
        for k in ("TO_SPAWN_TICKET", "TO_SPAWN_SPEC", "TO_SPAWN_LOG_REPO")
    }
    eintrag = {"argv": argv, "cwd": os.getcwd(), "stdin": eingabe, "env": umgebung}
    with ziel.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    time.sleep(float(os.environ.get("CLAUDE_AUFZEICHNUNG_SCHLAF", "0")))
    notiz = os.environ.get("CLAUDE_AUFZEICHNUNG_NOTIZ")
    if notiz:
        spec = os.environ["CLAUDE_AUFZEICHNUNG_SPEC"]
        subprocess.run(
            [
                sys.executable,
                str(SKILL / "to_spawn.py"),
                "takt",
                spec,
                "--notiz",
                notiz,
            ],
            check=True,
            timeout=60,
        )
    return int(os.environ.get("CLAUDE_AUFZEICHNUNG_EXIT", "0"))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
