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
  ``api repos/<slug>/issues/<N>/comments…``        → Kommentare (#285)
  ``issue edit <N> --repo <slug> --remove-label``  → Label entfernen/ergänzen (#285)
Kommentare dürfen als Text (Autor ``bot``, Zeit jetzt) oder als Objekt
``{"autor": …, "body": …, "created_at": …}`` im Zustand stehen (#285).
``GH_STUB_PROTOKOLL`` (Pfad): jeder Aufruf wird als Zeile angehängt.
``GH_STUB_FEHLER`` (z. B. ``comment`` oder ``reopen,comment``): diese Aufrufe enden mit Exit 1.
``api user`` → ``{"login": <Zustand ``login``, Vorgabe bau-bot>}`` (#402).
``api repos/<slug>/issues/<N>/dependencies/blocked_by`` → Zustand ``blocker[N]``
(Liste ``{"number", "state"}``, Vorgabe leer); ``GH_STUB_FEHLER=blocked_by`` → Exit 1 (#451).
``labels`` (Liste von Namen) und ``state_reason`` im Issue werden durchgereicht (Fixrunde #213).
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
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
        "labels": [{"name": name} for name in eintrag.get("labels", [])],
        "state_reason": eintrag.get("state_reason"),
    }


def _kommentare(nummer: str, daten: dict) -> list[dict]:
    """Kommentare wie die GitHub-API (#285); Texte ohne Autor gelten als Bot-Kommentar."""
    jetzt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    antwort = []
    for eintrag in daten.get("kommentare", {}).get(nummer, []):
        if isinstance(eintrag, str):
            eintrag = {"autor": "bot", "body": eintrag, "created_at": jetzt}
        antwort.append(
            {
                "user": {"login": eintrag.get("autor", "bot")},
                "body": eintrag.get("body", ""),
                "created_at": eintrag.get("created_at", jetzt),
                # GitHub-REST liefert die Rolle je Kommentar; Vorgabe OWNER (#402 S1).
                "author_association": eintrag.get("author_association", "OWNER"),
            }
        )
    return antwort


def main() -> int:
    args = sys.argv[1:]
    protokoll = os.environ.get("GH_STUB_PROTOKOLL")
    if protokoll:
        with open(protokoll, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(args, ensure_ascii=False) + "\n")
    pfad, daten = _lade()
    if args[:1] == ["api"]:
        if args[1:2] == ["user"]:  # gh-Login der Session (#402), Vorgabe ``bau-bot``
            print(json.dumps({"login": daten.get("login", "bau-bot")}))
            return 0
        treffer = re.search(r"issues/(\d+)/sub_issues", args[1])
        if treffer:
            kinder = daten.get("sub", {}).get(treffer.group(1), [])
            print(json.dumps([_issue(str(k), daten) for k in kinder]))
            return 0
        treffer = re.search(r"issues/(\d+)/dependencies/blocked_by", args[1])
        if treffer:
            if "blocked_by" in os.environ.get("GH_STUB_FEHLER", "").split(","):
                print("gh-Ersatz #213: blocked_by absichtlich gescheitert", file=sys.stderr)
                return 1
            print(json.dumps(daten.get("blocker", {}).get(treffer.group(1), [])))
            return 0
        treffer = re.search(r"issues/(\d+)/comments", args[1])
        if treffer:
            print(json.dumps(_kommentare(treffer.group(1), daten), ensure_ascii=False))
            return 0
        print(json.dumps([]))
        return 0
    if args[:2] == ["issue", "edit"]:
        eintrag = daten.setdefault("issues", {}).setdefault(args[2], {})
        weg = _wert(args, "--remove-label")
        if weg:
            eintrag["labels"] = [n for n in eintrag.get("labels", []) if n != weg]
        dazu = _wert(args, "--add-label")
        if dazu and dazu not in eintrag.get("labels", []):
            eintrag.setdefault("labels", []).append(dazu)
        pfad.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
        return 0
    if args[:2] in (["issue", "reopen"], ["issue", "comment"]):
        if args[1] in os.environ.get("GH_STUB_FEHLER", "").split(","):
            print(f"gh-Ersatz #213: {args[1]} absichtlich gescheitert", file=sys.stderr)
            return 1
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
