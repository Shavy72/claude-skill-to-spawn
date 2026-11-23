"""Ersatz für ``gh api repos/<slug>/issues/comments/<id>`` (GitHub = externer Dienst, #278 P3b).

Liefert den Kommentar aus ``GH_KOMMENTAR_DATEI`` (Form aus einer echten Aufzeichnung,
``gh_kommentar_278.json``), wenn ``<slug>`` und ``<id>`` zu dessen ``html_url`` passen; sonst Exit 1
wie ``gh`` bei „Not Found“.
"""

from __future__ import annotations

import json
import os
import re
import sys

args = sys.argv[1:]
kommentar = json.loads(open(os.environ["GH_KOMMENTAR_DATEI"], encoding="utf-8").read())
treffer = re.fullmatch(r"repos/([^/]+/[^/]+)/issues/comments/([0-9]+)", args[1]) if args[:1] == ["api"] else None
url = str(kommentar.get("html_url", ""))
if treffer and url.startswith(f"https://github.com/{treffer.group(1)}/") and url.endswith(f"-{treffer.group(2)}"):
    print(json.dumps(kommentar))
    sys.exit(0)
print("gh: Not Found (HTTP 404)", file=sys.stderr)
sys.exit(1)
