"""Zählbericht Soll (Auslöser) vs. Ist (Belege) je Moment (VERTRAG.md §7)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from _gemeinsam import MOMENTE, log_pfad, logging_einrichten

LOG = logging.getLogger("review.bericht")
DEFEKT = "Hook oder Dirigent defekt?"


def _zeit(wert: Any) -> datetime | None:
    try:
        zeit = datetime.fromisoformat(str(wert))
    except ValueError:
        return None
    return zeit if zeit.tzinfo else zeit.astimezone()


def _im_fenster(wert: Any, ab: datetime) -> bool:
    zeit = _zeit(wert)
    return zeit is None or zeit >= ab


def ausloeser_lesen(ab: datetime) -> list[dict[str, Any]]:
    pfad = log_pfad()
    if not pfad.is_file():
        return []
    zeilen: list[dict[str, Any]] = []
    for nr, roh in enumerate(pfad.read_text(encoding="utf-8").splitlines(), 1):
        if not roh.strip():
            continue
        try:
            eintrag = json.loads(roh)
        except json.JSONDecodeError:
            LOG.warning("%s Zeile %d kaputt, übersprungen", pfad, nr)
            continue
        if isinstance(eintrag, dict) and _im_fenster(eintrag.get("zeit"), ab):
            zeilen.append(eintrag)
    return zeilen


def belege_lesen(repos: set[str], ab: datetime) -> list[dict[str, Any]]:
    belege: list[dict[str, Any]] = []
    for repo in sorted(repos):
        for pfad in sorted((Path(repo) / ".review").glob("beleg-*.json")):
            try:
                beleg = json.loads(pfad.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as fehler:
                LOG.warning("Beleg %s unlesbar: %s", pfad, fehler)
                continue
            if isinstance(beleg, dict) and _im_fenster(beleg.get("zeit"), ab):
                belege.append(beleg)
    return belege


def tabelle(ausloeser: list[dict[str, Any]], belege: list[dict[str, Any]]) -> str:
    # Soll = verschiedene Reviews (repo, moment, fingerprint) mit Stufe ≠ keine.
    soll: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for a in ausloeser:
        if a.get("stufe", "keine") != "keine":
            soll[str(a.get("moment"))].add((str(a.get("repo")), str(a.get("fingerprint"))))
    ist: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for b in belege:
        ist[str(b.get("moment"))].append(b)

    reihenfolge = {m: i for i, m in enumerate(MOMENTE)}
    momente = sorted(set(soll) | set(ist), key=lambda m: (reihenfolge.get(m, 99), m))
    kopf = "| Moment | Soll | Ist | Quote | gruen | gelb | rot | gestrichen/roh | Fehlalarm | Token | Hinweis |"
    zeilen = [kopf, "|" + "---|" * 11]
    for m in momente:
        n_soll, liste = len(soll[m]), ist[m]
        n_ist = len(liste)
        status = {s: sum(1 for b in liste if b.get("status") == s) for s in ("gruen", "gelb", "rot")}
        roh = sum(int(p.get("befunde_roh", 0)) for b in liste for p in b.get("pruefer", []))
        gestrichen = 0
        for b in liste:
            richter = b.get("richter")
            if isinstance(richter, dict) and "gestrichen" in richter:
                gestrichen += int(richter["gestrichen"])
            else:
                gestrichen += sum(1 for x in b.get("befunde", []) if x.get("status") == "gestrichen")
        token = sum(int(b.get("token_schaetzung", 0)) for b in liste)
        quote = f"{n_ist / n_soll:.0%}" if n_soll else "–"
        fehlalarm = f"{gestrichen / roh:.0%}" if roh else "–"
        hinweis = DEFEKT if n_soll != n_ist else ""
        zeilen.append(
            f"| {m} | {n_soll} | {n_ist} | {quote} | {status['gruen']} | {status['gelb']} | "
            f"{status['rot']} | {gestrichen}/{roh} | {fehlalarm} | {token} | {hinweis} |"
        )
    if not momente:
        zeilen.append("| – | 0 | 0 | – | 0 | 0 | 0 | 0/0 | – | 0 | |")
    return "\n".join(zeilen)


def main(argv: list[str] | None = None) -> int:
    logging_einrichten()
    parser = argparse.ArgumentParser(description="Review-Zählbericht Soll vs. Ist.")
    parser.add_argument("--tage", type=int, default=7)
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--md")
    args = parser.parse_args(argv)
    ab = datetime.now().astimezone() - timedelta(days=args.tage)
    ausloeser = ausloeser_lesen(ab)
    repos = {str(a["repo"]) for a in ausloeser if a.get("repo")}
    repos |= {Path(r).resolve().as_posix() for r in args.repo}
    text = f"# Review-Bericht (letzte {args.tage} Tage)\n\n" + tabelle(ausloeser, belege_lesen(repos, ab))
    print(text)
    if args.md:
        try:
            Path(args.md).write_text(text + "\n", encoding="utf-8")
        except OSError as fehler:
            LOG.error("%s nicht schreibbar: %s", args.md, fehler)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
