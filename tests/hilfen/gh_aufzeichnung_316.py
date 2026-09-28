"""Spielt echte ``gh``-Aufzeichnungen ab (GitHub ist ein externer Dienst, #316).

``gh api repos/<slug>/issues/<S>/sub_issues…`` → Inhalt von
``$GH_AUFZEICHNUNG_ORDNER/sub_issues_<S>.json`` (einmal echt mit ``gh`` geholt).
Alles andere: Exit 1 (unbekannte Frage = kein erfundenes Ergebnis).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    ordner = Path(os.environ.get("GH_AUFZEICHNUNG_ORDNER", ""))
    for arg in argv:
        treffer = re.search(r"issues/(\d+)/sub_issues", arg)
        if treffer and argv and argv[0] == "api":
            datei = ordner / f"sub_issues_{treffer.group(1)}.json"
            if datei.is_file():
                sys.stdout.write(datei.read_text(encoding="utf-8"))
                return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
