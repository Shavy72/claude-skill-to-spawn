"""Hilfe für Tests, die ``abschluss_paket.py ablegen --stand abnahme`` rufen (#529).

Die Abnahme verlangt ein gültiges Rundschau-Deck; hier entsteht es echt aus den Entscheidungen
des Test-Repos (Erzeuger aus ``to_spawn/rundschau_deck.py``), nichts ist gestellt.
"""

from __future__ import annotations

import importlib.util
import struct
import sys
import zlib
from pathlib import Path

from to_spawn import rundschau_deck as rd

_SKRIPT = Path(__file__).resolve().parent.parent / "skripte" / "abschluss_paket.py"


def png(breite: int = 400, hoehe: int = 240) -> bytes:
    """Echte PNG-Datei (graue Fläche) in gewünschter Größe."""

    def chunk(art: bytes, daten: bytes) -> bytes:
        return (
            struct.pack(">I", len(daten))
            + art
            + daten
            + struct.pack(">I", zlib.crc32(art + daten))
        )

    zeile = b"\0" + b"\x80" * breite
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", breite, hoehe, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(zeile * hoehe))
        + chunk(b"IEND", b"")
    )


def rundschau_args(repo: Path, spec: int | str) -> list[str]:
    """``--rundschau-datei <deck>``: gültiges Deck im Repo für Entscheidungen + Bau-Tickets von ``repo``."""
    modul = importlib.util.spec_from_file_location("abschluss_paket_529", _SKRIPT)
    assert modul and modul.loader
    ap = importlib.util.module_from_spec(modul)
    sys.modules["abschluss_paket_529"] = ap  # Dataclasses brauchen den Modul-Eintrag
    modul.loader.exec_module(ap)
    daten = ap.entscheidungs_daten(repo, int(spec))
    bau = ap._bau_tickets(repo, int(spec))
    bild = repo.parent / "rundschau_bild.png"
    bild.write_bytes(png())
    teil2 = [
        {
            "titel": f"Ticket #{nr} — {titel or 'Beleg'}",
            "kern": "Auf Staging geprüft.",
            "bild": str(bild),
            "ticket": nr,
            "klickschritte": ["Stage öffnen", f"Ticket #{nr} durchklicken"],
        }
        for nr, titel in bau
    ] or [{"titel": "Belege", "kern": "Siehe Belegseiten.", "bild": str(bild)}]
    teil2.append({"titel": "Offenes", "kern": "Nichts.", "art": "offenes"})
    texte = {
        "titel": f"Spec {spec}",
        "teil1": [{"titel": "Neu", "kern": "Alles läuft.", "bild": str(bild)}],
        "teil2": teil2,
    }
    deck = repo / "docs" / "agents" / f"rundschau_{spec}.html"
    deck.parent.mkdir(parents=True, exist_ok=True)
    deck.write_text(rd.erzeuge(texte, daten), encoding="utf-8")
    return ["--rundschau-datei", str(deck)]
