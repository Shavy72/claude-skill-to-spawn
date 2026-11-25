"""CLI zur Abnahme-Rundschau (#529): ``erzeugen`` baut das Deck, ``pruefen`` prüft jede Datei.

``rundschau_deck.py erzeugen --texte <json> --entscheidungen <json> --nach <html>``
``rundschau_deck.py pruefen <html> --entscheidungen <json>``
Die Definition steht allein in ``to_spawn/rundschau_deck.py``. Exit 0 = ok, 4 = Prüfung rot,
5 = Skill ``rundschau`` fehlt, 2 = Eingabe unbrauchbar.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import rundschau_deck as rd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="befehl", required=True)
    e = sub.add_parser("erzeugen")
    e.add_argument("--texte", type=Path, required=True)
    e.add_argument("--entscheidungen", type=Path, required=True)
    e.add_argument("--nach", type=Path, required=True)
    p = sub.add_parser("pruefen")
    p.add_argument("datei", type=Path)
    p.add_argument("--entscheidungen", type=Path, required=True)
    p.add_argument(
        "--tickets", default="", help="kommagetrennte Bau-Ticket-Nummern für Pflicht-Folie Bild + Klick-Schritte"
    )
    a = ap.parse_args(argv)
    try:
        daten = json.loads(a.entscheidungen.read_text(encoding="utf-8"))
        if a.befehl == "erzeugen":
            if not rd.skill_da():
                sys.stderr.write(rd.SKILL_FEHLT + "\n")
                return 5
            texte = json.loads(a.texte.read_text(encoding="utf-8"))
            a.nach.write_text(rd.erzeuge(texte, daten), encoding="utf-8")
            return 0
        tickets = [t.strip() for t in a.tickets.split(",") if t.strip()] if a.tickets else None
        maengel = rd.pruefe(a.datei.read_text(encoding="utf-8"), daten, tickets=tickets)
    except (OSError, ValueError, KeyError) as exc:
        sys.stderr.write(f"Eingabe unbrauchbar: {exc}\n")
        return 2
    for m in maengel:
        sys.stdout.write(f"MANGEL: {m}\n")
    return 4 if maengel else 0


if __name__ == "__main__":
    sys.exit(main())
