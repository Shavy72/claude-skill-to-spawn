"""Auslassungs-Liste des CI-Laufs lesen und gegen die gesammelten Tests prüfen (#537).

Aufruf: ``auslassungen.py <auslassungen.txt> <collect-only-Ausgabe>`` — schreibt je Auslassung
``--deselect`` und die ID als eigene Zeile (für ``mapfile``); fehlt eine ID beim Sammeln,
nennt es sie auf stderr und endet mit 1, damit die Liste nicht still veraltet.
"""

import sys
from pathlib import Path


def lesen(pfad: Path) -> list[tuple[str, str]]:
    """(ID, Grund) je Zeile ``<ID>  # <Grund>``; Leer- und Kommentarzeilen zählen nicht."""
    eintraege = []
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#"):
            continue
        test_id, _, grund = zeile.partition("  # ")
        eintraege.append((test_id.strip(), grund.strip()))
    return eintraege


def fehlende(eintraege: list[tuple[str, str]], gesammelt: str, wurzel: Path = Path(".")) -> list[str]:
    """IDs der Liste, die beim Sammeln (``--collect-only -q``-Ausgabe) nicht vorkommen.

    Eine vorhandene Datei ganz ohne gesammelte Tests gilt als auf diesem Betriebssystem
    übersprungen (Modul-Skip) und macht ihre IDs nicht veraltet.
    """
    bekannt = {zeile.strip().replace("\\", "/") for zeile in gesammelt.splitlines()}
    dateien = {b.split("::", 1)[0] for b in bekannt if "::" in b}
    weg = []
    for test_id, _ in eintraege:
        datei = test_id.split("::", 1)[0]
        modul_skip = datei not in dateien and (wurzel / datei).is_file()
        if test_id not in bekannt and not modul_skip:
            weg.append(test_id)
    return weg


if __name__ == "__main__":
    liste = lesen(Path(sys.argv[1]))
    weg = fehlende(liste, Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace"))
    if weg:
        sys.stderr.write("Auslassung ohne gesammelten Test (Liste veraltet):\n" + "".join(f"  {t}\n" for t in weg))
        sys.exit(1)
    sys.stdout.buffer.write("".join(f"--deselect\n{t}\n" for t, _ in liste).encode("utf-8"))
