"""Stateful ``gh``-Aufzeichnungs-Stub für Taktgeber-Weg-Tests (#906, App #1402).

Baut auf den echten GitHub-Aufzeichnungen unter ``tests/hilfen/takt_316/`` auf
(``sub_issues_293.json``, ``kommentar_315.json``) und hält den Ticket-Stand einer
Test-Welt unter Dateisperre (``to_spawn.sperre``), damit auch zwei echte parallele
Taktgeber-Prozesse atomar gegen denselben GitHub-Stand arbeiten.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SKILL = Path(__file__).resolve().parents[2]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import konsole, sperre


def _laden(pfad: Path) -> dict[str, Any]:
    return json.loads(pfad.read_text(encoding="utf-8"))


def _speichern(pfad: Path, daten: dict[str, Any]) -> None:
    tmp = pfad.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(pfad)


def _issue_finden(daten: dict[str, Any], nummer: int) -> dict[str, Any] | None:
    for eintrag in daten.get("issues", []):
        if int(eintrag["number"]) == nummer:
            return eintrag
    return None


def _label_namen(issue: dict[str, Any]) -> list[str]:
    return [str(x["name"] if isinstance(x, dict) else x) for x in issue.get("labels") or []]


def _setze_labels(issue: dict[str, Any], namen: list[str]) -> None:
    issue["labels"] = [{"name": n} for n in namen]


def _verarbeite(argv: list[str], daten: dict[str, Any]) -> tuple[int, str, bool]:
    """Verarbeitet einen ``gh``-Aufruf → ``(exit_code, stdout, geaendert)``."""
    protokoll = daten.setdefault("aufrufe", [])
    if argv[:1] == ["api"] and len(argv) >= 2:
        pfad = argv[1]
        if re.search(r"/issues/\d+/sub_issues", pfad):
            return 0, json.dumps(daten.get("issues", []), ensure_ascii=False), False
        treffer = re.search(r"/issues/(\d+)/comments", pfad)
        if treffer:
            nr = treffer.group(1)
            kommentare = daten.get("kommentare", {}).get(nr, [])
            if "--slurp" in argv:
                return 0, json.dumps([kommentare], ensure_ascii=False), False
            return 0, json.dumps(kommentare, ensure_ascii=False), False
        label_treffer = re.search(r"/labels/([^/?]+)$", pfad)
        if label_treffer:
            name = label_treffer.group(1).replace("%3A", ":").replace("%3a", ":")
            if name in daten.get("repo_labels", []):
                return 0, json.dumps({"name": name}), False
            return 1, "", False
        return 1, "", False

    if argv[:2] == ["label", "create"] and len(argv) >= 3:
        name = argv[2]
        repo_labels = daten.setdefault("repo_labels", [])
        if name not in repo_labels:
            repo_labels.append(name)
        protokoll.append({"aktion": "label_create", "label": name})
        return 0, "", True

    if argv[:2] == ["issue", "view"] and len(argv) >= 3:
        issue = _issue_finden(daten, int(argv[2]))
        if issue is None:
            return 1, "", False
        antwort = {
            "state": str(issue.get("state", "open")).upper(),
            "labels": issue.get("labels") or [],
            "stateReason": (issue.get("state_reason") or "").upper() or None,
            "assignees": issue.get("assignees") or [],
        }
        return 0, json.dumps(antwort, ensure_ascii=False), False

    if argv[:2] == ["issue", "close"] and len(argv) >= 3:
        nr = int(argv[2])
        issue = _issue_finden(daten, nr)
        if issue is None:
            return 1, "", False
        kommentar = argv[argv.index("--comment") + 1] if "--comment" in argv else ""
        issue["state"] = "closed"
        issue["state_reason"] = "completed"
        if kommentar:
            vorlage = daten.get("kommentar_vorlage") or {"author_association": "OWNER"}
            daten.setdefault("kommentare", {}).setdefault(str(nr), []).append(
                {**vorlage, "body": kommentar, "created_at": "2026-11-27T12:00:00Z"}
            )
        protokoll.append({"aktion": "close", "ticket": nr, "kommentar": kommentar})
        return 0, "", True

    if argv[:2] == ["issue", "edit"] and len(argv) >= 3:
        nr = int(argv[2])
        issue = _issue_finden(daten, nr)
        if issue is None:
            return 1, "", False
        aktuell = _label_namen(issue)
        i = 3
        while i < len(argv):
            if argv[i] == "--add-label" and i + 1 < len(argv):
                label = argv[i + 1]
                if label not in aktuell:
                    aktuell.append(label)
                protokoll.append({"aktion": "add_label", "ticket": nr, "label": label})
                i += 2
            elif argv[i] == "--remove-label" and i + 1 < len(argv):
                label = argv[i + 1]
                aktuell = [x for x in aktuell if x != label]
                protokoll.append({"aktion": "remove_label", "ticket": nr, "label": label})
                i += 2
            else:
                i += 1
        _setze_labels(issue, aktuell)
        return 0, "", True

    return 1, "", False


def main(argv: list[str]) -> int:
    konsole.utf8_ausgabe()  # wie echtes gh; Windows-Pipe wäre cp1252 (#553, #907)
    zustand_pfad = Path(os.environ["GH_TAKTGEBER_ZUSTAND"])
    sperr_pfad = zustand_pfad.with_suffix(".lock")
    with sperre.halte(sperr_pfad, warten=10.0) as ok:
        if not ok:
            return 1
        daten = _laden(zustand_pfad)
        code, ausgabe, geaendert = _verarbeite(argv, daten)
        if geaendert:
            _speichern(zustand_pfad, daten)
    if ausgabe:
        sys.stdout.write(ausgabe)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
