"""Gestelltes ``claude``-Programm für den Weg-Test (die einzige echte Attrappe).

Verhält sich wie eine Bau-Session: liest den Prompt (letztes Argument), legt ein
Mini-Transkript mit ``message.usage`` an und endet je nach Szenario.

Umgebung:
  ``FAKE_CLAUDE_SZENARIO``  ``fertig`` (endet einfach) oder ``handoff``
                            (erster Lauf schreibt Handoff + Marker)
  ``FAKE_CLAUDE_AUSGABE``   Ordner für Prompt-Mitschriften und Transkripte
  ``TO_SPAWN_TICKET``       Ticket-Nummer (setzt die Staffel-Schleife)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    ausgabe = Path(os.environ.get("FAKE_CLAUDE_AUSGABE") or Path.cwd() / ".testlauf")
    ausgabe.mkdir(parents=True, exist_ok=True)
    ticket = os.environ.get("TO_SPAWN_TICKET", "0")

    zaehler_datei = ausgabe / f"aufrufe-{ticket}.txt"
    aufruf = int(zaehler_datei.read_text(encoding="utf-8")) + 1 if zaehler_datei.is_file() else 1
    zaehler_datei.write_text(str(aufruf), encoding="utf-8")
    (ausgabe / f"prompt-{ticket}-{aufruf}.txt").write_text(prompt, encoding="utf-8")

    jetzt = datetime.now(timezone.utc).isoformat()
    transkript = ausgabe / f"transkript-{ticket}-{aufruf}.jsonl"
    with transkript.open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": jetzt,
                    "message": {"model": "fake-modell", "usage": {"input_tokens": 10}},
                }
            )
            + "\n"
        )

    szenario = os.environ.get("FAKE_CLAUDE_SZENARIO", "fertig")
    if szenario == "handoff" and aufruf == 1:
        repo = Path.cwd()
        ordner = repo / "docs" / "handoffs"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / f"HANDOFF_2026-09-18_{ticket}.md").write_text(
            "Stand: Hälfte gebaut, Rest offen — Fortsetzung übernimmt.\n",
            encoding="utf-8",
        )
        marker = repo / ".to-spawn" / f"stop-{ticket}"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("handoff", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
