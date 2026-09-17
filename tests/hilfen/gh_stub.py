"""Ersatz für die ``gh``-CLI im Test (GitHub ist ein externer Dienst).

Beantwortet nur, was der Kern fragt:
  ``issue view <N> --json state``                → ``{"state": "OPEN"|"CLOSED"}``
  ``api repos/<slug>/issues/<N>/dependencies/blocked_by`` → Liste

Umgebung: ``GH_STUB_ZU`` (Komma-Liste geschlossener Tickets),
``GH_STUB_BLOCKER`` (``<Ticket>:<Blocker>``-Paare, Komma-getrennt).
"""

from __future__ import annotations

import json
import os
import re
import sys


def _zu() -> set[str]:
    return {t.strip() for t in os.environ.get("GH_STUB_ZU", "").split(",") if t.strip()}


def _blocker(ticket: str) -> list[dict]:
    paare = [p for p in os.environ.get("GH_STUB_BLOCKER", "").split(",") if ":" in p]
    return [
        {"number": int(p.split(":")[1]), "state": "closed"}
        for p in paare
        if p.split(":")[0].strip() == ticket
    ]


def main() -> int:
    args = sys.argv[1:]
    if args[:2] == ["issue", "view"]:
        nummer = args[2]
        print(json.dumps({"state": "CLOSED" if nummer in _zu() else "OPEN"}))
        return 0
    if args[:1] == ["api"]:
        treffer = re.search(r"issues/(\d+)/dependencies/blocked_by", args[1])
        print(json.dumps(_blocker(treffer.group(1)) if treffer else []))
        return 0
    print(json.dumps({}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
