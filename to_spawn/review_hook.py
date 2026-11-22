"""Review-Hook (``review-hook/`` im Skill) nach ``~/.claude/hooks/review`` installieren (#214).

Die eine Stelle für die Überschreib-Regel, gilt für ``install.sh`` und ``install.ps1``:
eine vorhandene Datei wird nur ersetzt, wenn sie dem zuletzt installierten Stand entspricht
(Prüfsummen in ``.installiert.json`` im Ziel) oder ohne CR gleich der neuen ist. Weicht sie
ab, landet sie vorher unter ``<ziel>/../_alt/<zielname>-<zeit>/`` und der Bericht nennt sie —
nie still überschreiben, nie nachfragen. Dateien, die nur im Ziel liegen, bleiben unberührt.

Tür: :func:`installiere`. Aufruf: ``python -m to_spawn.review_hook [--ziel <ordner>]``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

QUELL_ORDNER = Path(__file__).resolve().parents[1] / "review-hook"
STAND_DATEI = ".installiert.json"
_NIE = {"__pycache__", ".pytest_cache"}


def _pruefsumme(daten: bytes) -> str:
    """Prüfsumme ohne CR: CRLF und LF gelten als gleich."""
    return hashlib.sha256(daten.replace(b"\r", b"")).hexdigest()


def _quell_dateien(quelle: Path) -> list[str]:
    return sorted(
        p.relative_to(quelle).as_posix()
        for p in quelle.rglob("*")
        if p.is_file() and not _NIE.intersection(p.relative_to(quelle).parts)
    )


def installiere(ziel: Path, quelle: Path = QUELL_ORDNER, zeit: str | None = None) -> list[str]:
    """Bringt ``ziel`` auf den Stand von ``quelle``; gibt die Berichtszeilen zurück."""
    stand_pfad = ziel / STAND_DATEI
    try:
        alt_stand: dict[str, str] = json.loads(stand_pfad.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        # Kein/kaputter Stand: jede abweichende Datei gilt als fremd und wird gesichert.
        alt_stand = {}
    sicherung = ziel.parent / "_alt" / f"{ziel.name}-{zeit or datetime.now().strftime('%Y%m%d-%H%M%S')}"
    while sicherung.exists():
        sicherung = sicherung.with_name(sicherung.name + "-1")
    bericht: list[str] = []
    neu_stand: dict[str, str] = {}
    for rel in _quell_dateien(quelle):
        neu = (quelle / rel).read_bytes()
        neu_stand[rel] = _pruefsumme(neu)
        datei = ziel / rel
        if datei.exists():
            alt = datei.read_bytes()
            if alt == neu:
                continue
            summe = _pruefsumme(alt)
            if summe not in (neu_stand[rel], alt_stand.get(rel)):
                (sicherung / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(datei, sicherung / rel)
                bericht.append(f"abweichend, gesichert: {rel} → {sicherung / rel}")
            else:
                bericht.append(f"aktualisiert: {rel}")
        else:
            datei.parent.mkdir(parents=True, exist_ok=True)
            bericht.append(f"angelegt: {rel}")
        datei.write_bytes(neu)
    if neu_stand != alt_stand:
        stand_pfad.write_text(json.dumps(neu_stand, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return bericht


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Review-Hook nach ~/.claude/hooks/review installieren")
    parser.add_argument("--ziel", type=Path, default=Path.home() / ".claude" / "hooks" / "review")
    args = parser.parse_args(argv)
    try:
        bericht = installiere(args.ziel)
    except (OSError, ValueError) as fehler:
        log.error("[!!] Review-Hook nicht installiert (%s): %s", args.ziel, fehler)
        return 2
    for zeile in bericht:
        log.info("Review-Hook %s", zeile)
    log.info(
        "Review-Hook installiert: %s (%s)", args.ziel, f"{len(bericht)} Änderung(en)" if bericht else "schon aktuell"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
