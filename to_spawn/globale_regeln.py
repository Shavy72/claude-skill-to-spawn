"""Globale Regel-Blöcke der Kette (``kette/global/*.md``) in eine ``CLAUDE.md`` schreiben.

Jede Quelldatei wird ein Block zwischen zwei eigenen Marker-Zeilen; ein erneuter Lauf
ersetzt den Block an Ort und Stelle statt eine Kopie anzuhängen. Eine neue Regel heißt:
eine Datei unter ``kette/global/`` anlegen — sonst nichts. Port von
``scripts/install_global_claude_blocks.py`` aus dem ersten App-Repo. Die
Start-Zeile ist absichtlich byte-gleich mit der des App-Skripts (das sucht die volle Zeile):
beide Werkzeuge schreiben so dieselbe Datei und keins findet beim anderen nur eine halbe
Marker-Zeile. Erkannt wird die Start-Zeile hier nur am Präfix ``<!-- global:<name>:start``,
damit Blöcke mit anderer Quellangabe ersetzt statt verdoppelt werden. Zeilenenden der
Zieldatei (LF/CRLF) bleiben erhalten.

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
    # Gleich mit start_marker() in scripts/install_global_claude_blocks.py des ersten App-Repos —
    # nur gemeinsam ändern (Quelle der Regeln ist inzwischen kette/global/ hier).
    return f"<!-- global:{name}:start (Quelle duoplus-management/docs/agents/global/{name}.md) -->"


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


def _setze_block(text: str, name: str, block: str, nl: str = "\n") -> str:
    """``text`` mit ersetztem Block ``name``, sonst mit angehängtem Block.

    ``nl`` ist das Zeilenende der Zieldatei. Wirft ``ValueError``, wenn die Marker nicht
    genau ein Paar (Start vor Ende) bilden — halber oder doppelter Block wird nie geraten.
    """
    start_praefix, ende_zeile = f"<!-- global:{name}:start", ende_marker(name)
    anzahl = (text.count(start_praefix), text.count(ende_zeile))
    if anzahl == (0, 0):
        block = block.replace("\n", nl)
        return block if not text else text.rstrip("\r\n") + nl + nl + block
    start, ende = text.find(start_praefix), text.find(ende_zeile)
    if anzahl != (1, 1) or ende < start:
        raise ValueError(
            f"Block {name}: Marker-Zeilen kein einzelnes Paar (Start {anzahl[0]}×, "
            f"Ende {anzahl[1]}×) — von Hand prüfen"
        )
    ende += len(ende_zeile)
    if text.startswith(nl, ende):
        ende += len(nl)
    return text[:start] + block.replace("\n", nl) + text[ende:]


def _soll(ziel: Path, ordner: Path) -> tuple[str, str]:
    # Bytes statt read_text: kein Umschreiben der Zeilenenden (Windows-CRLF bleibt CRLF).
    alt = ziel.read_bytes().decode("utf-8") if ziel.exists() else ""
    nl = "\r\n" if "\r\n" in alt else "\n"
    neu = alt
    for name, block in _bloecke(ordner).items():
        neu = _setze_block(neu, name, block, nl)
    return alt, neu


def pruefe(ziel: Path, ordner: Path = QUELL_ORDNER) -> bool:
    """True, wenn jeder Block in ``ziel`` vorhanden und aktuell ist (nichts zu schreiben)."""
    alt, neu = _soll(ziel, ordner)
    return alt == neu


def schreibe(ziel: Path, ordner: Path = QUELL_ORDNER) -> bool:
    """Bringt ``ziel`` auf Soll; True, wenn geschrieben wurde. Aktuelle Datei bleibt byte-gleich.

    Wirft ``ValueError`` bei halbem oder doppeltem Block — dann wird nichts geschrieben.
    Ist ``ziel`` eine Verknüpfung, wird ihr Ziel beschrieben; die Verknüpfung bleibt.
    """
    ziel = ziel.resolve()
    alt, neu = _soll(ziel, ordner)
    if neu == alt:
        return False
    ziel.parent.mkdir(parents=True, exist_ok=True)
    zwischen = ziel.with_name(ziel.name + ".neu")
    zwischen.write_bytes(neu.encode("utf-8"))
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
