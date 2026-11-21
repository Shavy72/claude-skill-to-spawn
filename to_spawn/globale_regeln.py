"""Globale Regel-Blöcke der Kette (``kette/global/*.md``) in eine ``CLAUDE.md`` schreiben.

Jede Quelldatei wird ein Block zwischen zwei eigenen Marker-Zeilen; ein erneuter Lauf
ersetzt den Block an Ort und Stelle statt eine Kopie anzuhängen. Eine neue Regel heißt:
eine Datei unter ``kette/global/`` anlegen — sonst nichts. Port von
``scripts/install_global_claude_blocks.py`` aus dem ersten App-Repo; die Start-Zeile wird
nur am Präfix ``<!-- global:<name>:start`` erkannt, damit Blöcke mit alter Quellangabe
ersetzt statt verdoppelt werden.

Türen: :func:`schreibe` (Datei auf Soll bringen) und :func:`pruefe` (ist sie auf Soll?).
Aufruf: ``python -m to_spawn.globale_regeln --ziel <CLAUDE.md> [--pruefen]``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

QUELL_ORDNER = Path(__file__).resolve().parents[1] / "kette" / "global"


def start_marker(name: str) -> str:
    return f"<!-- global:{name}:start (Quelle claude-skill-to-spawn/kette/global/{name}.md) -->"


def ende_marker(name: str) -> str:
    return f"<!-- global:{name}:ende -->"


def _bloecke(ordner: Path) -> dict[str, str]:
    """{name: markierter Block} je Quelldatei, nach Name sortiert."""
    bloecke = {
        quelle.stem: f"{start_marker(quelle.stem)}\n{quelle.read_text(encoding='utf-8').strip()}\n{ende_marker(quelle.stem)}\n"
        for quelle in sorted(ordner.glob("*.md"))
    }
    if not bloecke:
        raise FileNotFoundError(f"keine Regel-Dateien in {ordner}")
    return bloecke


def _setze_block(text: str, name: str, block: str) -> str:
    """``text`` mit ersetztem Block ``name``, sonst mit angehängtem Block."""
    start = text.find(f"<!-- global:{name}:start")
    ende_zeile = ende_marker(name)
    ende = text.find(ende_zeile)
    if start != -1 and ende > start:
        ende += len(ende_zeile)
        if text[ende : ende + 1] == "\n":
            ende += 1
        return text[:start] + block + text[ende:]
    if start != -1 or ende != -1:
        raise ValueError(
            f"Block {name}: nur eine Marker-Zeile gefunden — von Hand prüfen"
        )
    return block if not text else text.rstrip("\n") + "\n\n" + block


def _soll(ziel: Path, ordner: Path) -> tuple[str, str]:
    alt = ziel.read_text(encoding="utf-8") if ziel.exists() else ""
    neu = alt
    for name, block in _bloecke(ordner).items():
        neu = _setze_block(neu, name, block)
    return alt, neu


def pruefe(ziel: Path, ordner: Path = QUELL_ORDNER) -> bool:
    """True, wenn jeder Block in ``ziel`` vorhanden und aktuell ist (nichts zu schreiben)."""
    alt, neu = _soll(ziel, ordner)
    return alt == neu


def schreibe(ziel: Path, ordner: Path = QUELL_ORDNER) -> bool:
    """Bringt ``ziel`` auf Soll; True, wenn geschrieben wurde. Aktuelle Datei bleibt byte-gleich.

    Wirft ``ValueError`` bei halbem Block (nur eine Marker-Zeile) — dann wird nichts geschrieben.
    """
    alt, neu = _soll(ziel, ordner)
    if neu == alt:
        return False
    ziel.parent.mkdir(parents=True, exist_ok=True)
    zwischen = ziel.with_name(ziel.name + ".neu")
    zwischen.write_text(neu, encoding="utf-8")
    zwischen.replace(ziel)
    return True


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description="Globale Regel-Blöcke in eine CLAUDE.md schreiben"
    )
    parser.add_argument(
        "--ziel", type=Path, default=Path.home() / ".claude" / "CLAUDE.md"
    )
    parser.add_argument(
        "--pruefen",
        action="store_true",
        help="nur prüfen: Exit 1 bei fehlendem/veraltetem Block",
    )
    args = parser.parse_args(argv)
    try:
        if args.pruefen:
            if pruefe(args.ziel):
                log.info("[ok] Regel-Blöcke aktuell: %s", args.ziel)
                return 0
            log.error(
                "[!!] Regel-Block fehlt oder ist veraltet: %s — Befehl: install",
                args.ziel,
            )
            return 1
        geschrieben = schreibe(args.ziel)
    except (ValueError, OSError) as fehler:
        log.error("[!!] %s: %s", args.ziel, fehler)
        return 2
    log.info(
        "[ok] %s: %s",
        "Regel-Blöcke geschrieben" if geschrieben else "schon aktuell",
        args.ziel,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
