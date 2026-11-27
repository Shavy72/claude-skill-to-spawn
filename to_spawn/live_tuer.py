"""Live-Tür (#456): der Live-Schritt einer Spec startet nur über :func:`starte`.

Für Aufrufer (Aufseher, ``to_spawn.py live <S>``): nur :func:`starte` rufen, Exit-Code weiterreichen.
Ob live darf, fragt die Tür allein :func:`to_spawn.komplett_lauf.darf_live` — dort lebt die Regel
„Live nur nach grüner Komplett-Prüfung“ (Repo ohne Staging: läuft hier einmal; mit Staging gilt der
grüne Takt-Lauf). Der Befehl kommt aus ``live.befehl`` (:func:`to_spawn.config.live_schritt`) und
läuft in bash im Klon, Ausgabe geht durch.

Exit: 0 = Live-Befehl grün · 1 = nicht gestartet (Prüfung nicht frei) oder Live-Befehl rot ·
2 = ``live.befehl`` fehlt (Prüfung grün, Live-Schritt nach Repo-Ablauf von Hand).

Zwei Varianten erwogen: Regel im Live-Aufrufer (Tür liest Merker selbst) gegen Frage an
``komplett_lauf`` — gebaut ist die zweite, weil Merker, Einmal-Regel und Staging-Unterschied so in
genau einem Modul bleiben und die Tür nur „frei?“ fragt.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from to_spawn import bash_wahl, capo, config, komplett_lauf

log = logging.getLogger(__name__)


def starte(klon: Path, spec: int, konfig: dict[str, Any], *, gh_repo: str = "") -> tuple[int, list[str]]:
    """Live-Schritt der Spec ``spec`` starten, wenn die Komplett-Prüfung frei gibt; (Exit, Zeilen)."""
    pruefung = komplett_lauf.darf_live(klon, spec, konfig)
    if not pruefung.frei:
        return 1, [f"Live-Schritt nicht gestartet: {pruefung.grund}"]
    zeilen = [pruefung.grund]
    befehl = config.live_schritt(konfig)["befehl"]
    if not befehl:
        zeilen.append(
            "live.befehl fehlt in .to-spawn/config.json — Live-Schritt nach Repo-Ablauf von Hand "
            "(Komplett-Prüfung ist frei)."
        )
        return 2, zeilen
    try:
        erg = bash_wahl.lauf(["-c", befehl], cwd=klon, stdin=subprocess.DEVNULL)
    except OSError as fehler:
        log.error("live_tuer: Live-Befehl Spec %s nicht startbar: %s", spec, fehler)
        zeilen.append(f"Live-Befehl nicht gestartet ({fehler}). Handlung: bash prüfen, dann erneut rufen.")
        return 1, zeilen
    if erg.returncode != 0:
        zeilen.append(f"Live-Befehl rot (Exit {erg.returncode}). Handlung: Ausgabe oben lesen, beheben, erneut rufen.")
        return 1, zeilen
    log.info("live_tuer: Spec %s live-Befehl grün", spec)
    if not capo.live_merken(klon, spec, gh_repo):  # Merker für den Takt (#546); Live selbst lief
        zeilen.append("Live-Merker nicht schreibbar — Spec-Issue schließt der Takt dann nicht von selbst.")
    zeilen.append("Live-Befehl grün.")
    return 0, zeilen
