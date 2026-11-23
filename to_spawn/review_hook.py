"""Review-Hook (``review-hook/`` im Skill) nach ``~/.claude/hooks/review`` installieren (#214),
dazu das Review-Werkzeug (``review/``) nach ``~/.claude/review`` (#329 P2).

Die eine Stelle für die Überschreib-Regel, gilt für ``install.sh`` und ``install.ps1``:
eine vorhandene Datei wird nur ersetzt, wenn sie dem zuletzt installierten Stand entspricht
(Prüfsummen in ``.installiert.json`` im Ziel) oder ohne CR gleich der neuen ist. Weicht sie
ab, landet sie vorher unter ``<ziel>/../_alt/<zielname>-<zeit>/`` und der Bericht nennt sie —
nie still überschreiben, nie nachfragen. Dateien, die nur im Ziel liegen, bleiben unberührt.
Ziel-Dateien, die Links sind, werden nur gemeldet, nie durchgeschrieben; geschrieben wird atomar.

Tür: :func:`installiere`. Aufruf: ``python -m to_spawn.review_hook [--ziel <ordner>]``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

QUELL_ORDNER = Path(__file__).resolve().parents[1] / "review-hook"
#: Review-Werkzeug (#329 P2): gleiche Regel, Ziel ``~/.claude/review``; Laufzeit-Daten dort bleiben.
WERKZEUG_QUELLE = Path(__file__).resolve().parents[1] / "review"
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
        alt_stand = {}
    if not isinstance(alt_stand, dict):
        alt_stand = {}  # Kein/kaputter Stand (auch null, [], 5): jede abweichende Datei wird gesichert.
    sicherung = ziel.parent / "_alt" / f"{ziel.name}-{zeit or datetime.now().strftime('%Y%m%d-%H%M%S')}"
    while sicherung.exists():
        sicherung = sicherung.with_name(sicherung.name + "-1")
    bericht: list[str] = []
    neu_stand: dict[str, str] = {}
    ziel_echt = ziel.resolve()
    for rel in _quell_dateien(quelle):
        neu = (quelle / rel).read_bytes()
        datei = ziel / rel
        if datei.is_symlink() or not datei.resolve().is_relative_to(ziel_echt):
            # Link (auch tot oder über einen Unter-Ordner): nie durchschreiben, nur melden.
            bericht.append(f"abweichend, Link nicht angefasst: {rel}")
            continue
        if datei.is_dir():
            bericht.append(f"abweichend, Ordner nicht angefasst: {rel}")
            continue
        neu_stand[rel] = _pruefsumme(neu)
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
        _schreibe_atomar(datei, neu)
    if neu_stand != alt_stand:
        _schreibe_atomar(stand_pfad, (json.dumps(neu_stand, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return bericht


def _schreibe_atomar(datei: Path, daten: bytes) -> None:
    """Erst Temp-Datei im selben Ordner, dann ``os.replace``: der laufende Hook liest nie halbe Dateien.

    Die Temp-Datei entsteht neu und exklusiv (``mkstemp``), ein dort liegender Link wird nie durchgeschrieben.
    """
    fd, name = tempfile.mkstemp(dir=datei.parent, prefix=f".{datei.name}.", suffix=".neu")
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(daten)
        if datei.exists():
            shutil.copymode(datei, temp)
        else:
            os.chmod(temp, 0o666 & ~_umask())  # mkstemp legt 0600 an; neue Datei wie write_bytes
        os.replace(temp, datei)
    finally:
        temp.unlink(missing_ok=True)


def _umask() -> int:
    alt = os.umask(0)
    os.umask(alt)
    return alt


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Review-Hook nach ~/.claude/hooks/review installieren")
    parser.add_argument("--ziel", type=Path, default=Path.home() / ".claude" / "hooks" / "review")
    args = parser.parse_args(argv)
    for name, ziel, quelle in (
        ("Review-Hook", args.ziel, QUELL_ORDNER),
        ("Review-Werkzeug", Path.home() / ".claude" / "review", WERKZEUG_QUELLE),
    ):
        try:
            bericht = installiere(ziel, quelle)
        except (OSError, ValueError) as fehler:
            log.error("[!!] %s nicht installiert (%s): %s", name, ziel, fehler)
            return 2
        for zeile in bericht:
            log.info("%s %s", name, zeile)
        log.info("%s installiert: %s (%s)", name, ziel, f"{len(bericht)} Änderung(en)" if bericht else "schon aktuell")
    return 0


if __name__ == "__main__":
    sys.exit(main())
