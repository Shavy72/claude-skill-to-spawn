"""Ersatz für die ``gh``-CLI im Test (GitHub ist ein externer Dienst).

Beantwortet nur, was der Kern fragt:
  ``issue view <N> --json state``                → ``{"state": "OPEN"|"CLOSED"}``
  ``api repos/<slug>/issues/<N>/dependencies/blocked_by`` → Liste
  ``api repos/<slug>/issues/<S>/sub_issues [--jq …]``    → Unter-Tickets (#236);
    mit ``--jq`` nur die Nummern der offenen, eine je Zeile (einziger jq-Filter,
    den der Aufpasser nutzt)
  ``issue comment <N> --body …``                  → protokolliert, Exit 0 (#236)

Umgebung: ``GH_STUB_ZU`` (Komma-Liste geschlossener Tickets),
``GH_STUB_BLOCKER`` (``<Ticket>:<Blocker>``-Paare, Komma-getrennt),
``GH_STUB_DATEN`` (Pfad zu JSON ``{"<N>": {"labels": [..], "body": "..",
"assignees": [..]}}`` — wird bei ``issue view`` in die Antwort gemischt;
ohne die Variable bleibt die Antwort wie bisher nur ``state``),
``GH_STUB_KAPUTT`` (Komma-Liste: ``issue view`` dieser Nummern scheitert mit Exit 1),
``GH_STUB_UNTER`` (``<S>:<n1>|<n2>,<S2>:<n3>`` — Unter-Tickets je Spec; Zustand je
Nummer wie bisher über ``GH_STUB_ZU``),
``GH_STUB_PROTOKOLL`` (Pfad: jeder Aufruf wird als Zeile angehängt).
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


def _zusatz(ticket: str) -> dict:
    pfad = os.environ.get("GH_STUB_DATEN")
    if not pfad:
        return {}
    with open(pfad, encoding="utf-8") as fh:
        eintrag = json.load(fh).get(ticket) or {}
    zusatz: dict = {}
    if "labels" in eintrag:
        zusatz["labels"] = [{"name": name} for name in eintrag["labels"]]
    if "assignees" in eintrag:
        zusatz["assignees"] = [{"login": login} for login in eintrag["assignees"]]
    if "body" in eintrag:
        zusatz["body"] = eintrag["body"]
    return zusatz


def _unter(spec: str) -> list[dict]:
    """Unter-Tickets der Spec aus ``GH_STUB_UNTER`` (#236)."""
    for eintrag in os.environ.get("GH_STUB_UNTER", "").split(","):
        kopf, _, rest = eintrag.partition(":")
        if kopf.strip() != spec:
            continue
        return [
            {"number": int(n), "state": "closed" if n.strip() in _zu() else "open"}
            for n in rest.split("|")
            if n.strip()
        ]
    return []


def _liste(name: str) -> set[str]:
    return {t.strip() for t in os.environ.get(name, "").split(",") if t.strip()}


def main() -> int:
    args = sys.argv[1:]
    protokoll = os.environ.get("GH_STUB_PROTOKOLL")
    if protokoll:
        with open(protokoll, "a", encoding="utf-8") as fh:
            fh.write(" ".join(args) + "\n")
    if args[:2] == ["issue", "view"]:
        nummer = args[2]
        if nummer in _liste("GH_STUB_KAPUTT"):
            print("gh: Abfrage gescheitert", file=sys.stderr)
            return 1
        antwort = {"state": "CLOSED" if nummer in _zu() else "OPEN"}
        antwort.update(_zusatz(nummer))
        print(json.dumps(antwort))
        return 0
    if args[:2] == ["issue", "comment"]:
        print(f"https://github.com/stub/issues/{args[2]}#issuecomment-stub")
        return 0
    if args[:1] == ["api"]:
        unter = re.search(r"issues/(\d+)/sub_issues", args[1])
        if unter:
            liste = _unter(unter.group(1))
            if "--jq" in args:
                print("\n".join(str(e["number"]) for e in liste if e["state"] == "open"))
            else:
                print(json.dumps(liste))
            return 0
        treffer = re.search(r"issues/(\d+)/dependencies/blocked_by", args[1])
        print(json.dumps(_blocker(treffer.group(1)) if treffer else []))
        return 0
    print(json.dumps({}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
