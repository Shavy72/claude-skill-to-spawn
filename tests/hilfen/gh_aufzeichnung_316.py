"""Spielt echte ``gh``-Aufzeichnungen ab (GitHub ist ein externer Dienst, #316).

``gh api repos/<slug>/issues/<S>/sub_issues…`` → Inhalt von
``$GH_AUFZEICHNUNG_ORDNER/sub_issues_<S>.json`` (einmal echt mit ``gh`` geholt).
Mit ``$GH_AUFZEICHNUNG_KOMMENTARE`` (#315): ``gh issue comment <N> … --body <text>`` hängt einen
Kommentar in der Form der echten Aufzeichnung ``kommentar_315.json`` an diese Datei an,
``gh api repos/<slug>/issues/<N>/comments…`` liefert die Kommentare von ``<N>`` zurück.
Alles andere: Exit 1 (unbekannte Frage = kein erfundenes Ergebnis).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def _kommentare(argv: list[str], ordner: Path, ablage: Path) -> int | None:
    if argv[:2] == ["issue", "comment"] and "--body" in argv:
        vorlage = json.loads((ordner / "kommentar_315.json").read_text(encoding="utf-8"))
        eintrag = {**vorlage, "body": argv[argv.index("--body") + 1], "issue": argv[2]}
        with ablage.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
        return 0
    treffer = re.search(r"issues/(\d+)/comments", " ".join(argv[1:2])) if argv[:1] == ["api"] else None
    if treffer:
        zeilen = ablage.read_text(encoding="utf-8").splitlines() if ablage.is_file() else []
        alle = [json.loads(z) for z in zeilen if z.strip()]
        sys.stdout.write(json.dumps([k for k in alle if k.pop("issue") == treffer.group(1)]))
        return 0
    return None


def main(argv: list[str]) -> int:
    ordner = Path(os.environ.get("GH_AUFZEICHNUNG_ORDNER", ""))
    ablage = os.environ.get("GH_AUFZEICHNUNG_KOMMENTARE")
    if ablage:
        code = _kommentare(argv, ordner, Path(ablage))
        if code is not None:
            return code
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
