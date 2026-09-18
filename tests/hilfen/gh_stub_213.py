"""Ersatz für die ``gh``-CLI in den Wächter-Tests (duoplus-management#213).

GitHub ist ein externer Dienst; der echte Weg läuft im Weg-Test
``test_waechter_213_weg.py`` gegen echte Issues. Dieser Ersatz hält einen
kleinen Zustand in einer JSON-Datei (``GH_STUB213_ZUSTAND``), damit
Wieder-Öffnen und Kommentare nachprüfbar sind:

    {"sub": {"900": [901, 902]},
     "issues": {"901": {"state": "closed", "closed_at": "…", "updated_at": "…",
                        "assignees": ["x"], "title": "…"}},
     "kommentare": {"901": ["…"]}}

Beantwortet:
  ``api repos/<slug>/issues/<S>/sub_issues…``      → Liste der Kind-Issues
  ``issue reopen <N> --repo <slug> --comment <T>`` → state = open, Kommentar merken
  ``issue comment <N> --repo <slug> --body <T>``   → Kommentar merken
``GH_STUB_PROTOKOLL`` (Pfad): jeder Aufruf wird als Zeile angehängt.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def _lade() -> tuple[Path, dict]:
    pfad = Path(os.environ["GH_STUB213_ZUSTAND"])
    return pfad, json.loads(pfad.read_text(encoding="utf-8"))


def _wert(args: list[str], schalter: str) -> str:
    return args[args.index(schalter) + 1] if schalter in args else ""


def _issue(nummer: str, daten: dict) -> dict:
    eintrag = daten.get("issues", {}).get(nummer, {})
    return {
        "number": int(nummer),
        "title": eintrag.get("title", f"Ticket {nummer}"),
        "state": eintrag.get("state", "open"),
        "closed_at": eintrag.get("closed_at"),
        "updated_at": eintrag.get("updated_at"),
        "assignees": [{"login": name} for name in eintrag.get("assignees", [])],
    }


def main() -> int:
    args = sys.argv[1:]
    protokoll = os.environ.get("GH_STUB_PROTOKOLL")
    if protokoll:
        with open(protokoll, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(args, ensure_ascii=False) + "\n")
    pfad, daten = _lade()
    if args[:1] == ["api"]:
        treffer = re.search(r"issues/(\d+)/sub_issues", args[1])
        if treffer:
            kinder = daten.get("sub", {}).get(treffer.group(1), [])
            print(json.dumps([_issue(str(k), daten) for k in kinder]))
            return 0
        print(json.dumps([]))
        return 0
    if args[:2] in (["issue", "reopen"], ["issue", "comment"]):
        nummer = args[2]
        text = _wert(args, "--comment") or _wert(args, "--body")
        eintrag = daten.setdefault("issues", {}).setdefault(nummer, {})
        if args[1] == "reopen":
            eintrag["state"] = "open"
            eintrag["closed_at"] = None
        if text:
            daten.setdefault("kommentare", {}).setdefault(nummer, []).append(text)
        pfad.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
        return 0
    print("gh-Ersatz #213: unbekannter Aufruf", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
