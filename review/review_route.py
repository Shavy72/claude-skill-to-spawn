"""Diff → Stufe, Signale, Prüfer-Liste als JSON auf stdout (VERTRAG.md §3, §4)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from _gemeinsam import (
    MOMENTE,
    EingabeFehler,
    berechne_route,
    diff_argumente,
    jetzt,
    log_pfad,
    logging_einrichten,
)

LOG = logging.getLogger("review.route")


def kurzzeile(route: dict[str, Any]) -> str:
    namen = [p["name"] for p in route["pruefer"]] + [
        b["befehl"] for b in route["builtin"]
    ]
    liste = ", ".join(namen) if namen else "–"
    zeile = f"Review {route['stufe']}: {liste} ({route['diff_zeilen']} Zeilen, {len(route['dateien'])} Dateien)"
    regeln = route.get("projektregeln")
    return f"{zeile} · Projektregeln fehlen ({regeln['datei']})" if regeln and not regeln["vorhanden"] else zeile


def ausloeser_loggen(route: dict[str, Any]) -> None:
    zeile = {k: route[k] for k in ("repo", "moment", "stufe", "fingerprint")}
    zeile = {"zeit": jetzt(), **zeile}
    pfad = log_pfad()
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        with pfad.open("a", encoding="utf-8") as datei:
            datei.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    except OSError as fehler:
        LOG.error("Auslöser-Log %s nicht schreibbar: %s", pfad, fehler)


def main(argv: list[str] | None = None) -> int:
    logging_einrichten()
    parser = argparse.ArgumentParser(description="Review-Route aus Git-Diff berechnen.")
    parser.add_argument("--moment", required=True, choices=MOMENTE)
    diff_argumente(parser)
    parser.add_argument("--log-ausloeser", action="store_true")
    parser.add_argument("--kurz", action="store_true")
    args = parser.parse_args(argv)
    try:
        route = berechne_route(
            args.repo, args.moment, args.base, args.staged, args.files
        )
    except EingabeFehler as fehler:
        LOG.error("%s", fehler)
        return 2
    if args.log_ausloeser:
        ausloeser_loggen(route)
    if args.kurz:
        print(kurzzeile(route))
    else:
        print(json.dumps(route, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
