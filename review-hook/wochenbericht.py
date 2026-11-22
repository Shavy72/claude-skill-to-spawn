"""SessionStart-Hook: Review-Wochenbericht einmal pro 7 Tage erzeugen (VERTRAG.md §8).

Ruft `review/review_bericht.py --tage 7 --md <Datei>` auf, meldet Pfad + Kernzahl
per additionalContext. Läuft ein Stand-Datum jünger als 7 Tage, bleibt der Hook still.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from _hookbasis import REVIEW, ausfuehren

LOG = logging.getLogger("review.hook.wochenbericht")

BERICHT_SKRIPT = REVIEW / "review_bericht.py"
TAGE_ABSTAND = 7
DECKEL_S = 25


def basis_ordner() -> Path:
    return Path(os.environ.get("REVIEW_WOCHENBERICHT_ORDNER") or Path.home() / ".claude" / ".review")


def stand_pfad() -> Path:
    return basis_ordner() / "wochenbericht-stand.json"


def bericht_pfad(heute: date) -> Path:
    return basis_ordner() / f"wochenbericht-{heute.isoformat()}.md"


def _stand_lesen() -> date | None:
    pfad = stand_pfad()
    if not pfad.is_file():
        return None
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        return date.fromisoformat(str(daten["letzter_lauf"]))
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as fehler:
        LOG.warning("%s unlesbar: %s", pfad, fehler)
        return None


def _stand_schreiben(heute: date) -> None:
    pfad = stand_pfad()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps({"letzter_lauf": heute.isoformat()}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, pfad)


def faellig(heute: date) -> bool:
    letzter = _stand_lesen()
    return letzter is None or (heute - letzter).days >= TAGE_ABSTAND


def kernzahl(text: str) -> str:
    """Fehlalarm-Quote über alle Tabellenzeilen (Spalte gestrichen/roh summiert)."""
    gestrichen_summe = 0
    roh_summe = 0
    for zeile in text.splitlines():
        if not zeile.startswith("|") or zeile.startswith("|---") or "Fehlalarm" in zeile:
            continue
        felder = [f.strip() for f in zeile.strip("|").split("|")]
        if len(felder) < 9:
            continue
        treffer = re.fullmatch(r"(\d+)/(\d+)", felder[7])
        if treffer:
            gestrichen_summe += int(treffer.group(1))
            roh_summe += int(treffer.group(2))
    if roh_summe == 0:
        return "keine Prüfungen"
    return f"Fehlalarm-Quote {gestrichen_summe / roh_summe:.0%}"


def bericht_erzeugen(ziel: Path) -> str | None:
    try:
        ergebnis = subprocess.run(
            [
                sys.executable,
                str(BERICHT_SKRIPT),
                "--tage",
                str(TAGE_ABSTAND),
                "--md",
                str(ziel),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DECKEL_S,
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        LOG.error("review_bericht nicht startbar: %s", fehler)
        return None
    if ergebnis.returncode != 0:
        LOG.error("review_bericht Exit %d: %s", ergebnis.returncode, ergebnis.stderr.strip())
        return None
    return ergebnis.stdout


def haupt(eingabe: dict[str, Any]) -> dict[str, Any] | None:
    del eingabe  # Eingabe unbenutzt, Signatur passt zu ausfuehren()
    heute = datetime.now().astimezone().date()
    if not faellig(heute):
        return None
    ziel = bericht_pfad(heute)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    text = bericht_erzeugen(ziel)
    if text is None:
        return None
    _stand_schreiben(heute)
    zeile = f"Review-Wochenbericht: {ziel} ({kernzahl(text)})"
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": zeile,
        }
    }


if __name__ == "__main__":
    ausfuehren("wochenbericht", haupt)
