"""Repo-Konfiguration ``.to-spawn/config.json`` mit Vorgabewerten.

Fehlt die Datei, gelten die Vorgaben unten — der Skill läuft also in jedem Repo
ohne Setup. Vorhandene Felder überschreiben die Vorgaben (verschachtelt).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("to_spawn.config")

#: Vorgabewerte. ``staffel.modus`` = "eltern", weil ein Stop-Hook den
#: Claude-Prozess laut Doku nicht beenden kann (siehe README, Abschnitt Staffel).
DEFAULTS: dict[str, Any] = {
    "ziel_default": "srv",
    "terminal": "wt",
    "ssh_ziel": "bau-server",
    "runner": "claude",
    "modelle": {
        "ticket": "claude-opus-5",
        "ticket_leicht": "claude-sonnet-5",
        "waechter": "claude-fable-5-1",
    },
    "effort": {
        "ticket": "medium",
        "ticket_leicht": "low",
        "waechter": "low",
    },
    "staffel": {
        "modus": "eltern",
        "grenze_k": 200,
        "max_staffeln": 3,
    },
    "mail": {
        "ziel": "",
        "nur_kritisch": True,
    },
}

KONFIG_PFAD = Path(".to-spawn") / "config.json"


def _mische(vorgabe: dict[str, Any], eigen: dict[str, Any]) -> dict[str, Any]:
    """Verschachtelte Vereinigung: Werte aus ``eigen`` gewinnen."""
    ergebnis = dict(vorgabe)
    for schluessel, wert in eigen.items():
        alt = ergebnis.get(schluessel)
        if isinstance(alt, dict) and isinstance(wert, dict):
            ergebnis[schluessel] = _mische(alt, wert)
        else:
            ergebnis[schluessel] = wert
    return ergebnis


def repo_wurzel(start: Path | None = None) -> Path:
    """Nächstgelegener Ordner mit ``.git`` ab ``start`` (sonst ``start`` selbst)."""
    pfad = (start or Path.cwd()).resolve()
    for kandidat in [pfad, *pfad.parents]:
        if (kandidat / ".git").exists():
            return kandidat
    return pfad


def lade(repo: Path | None = None) -> dict[str, Any]:
    """Konfiguration des Repos, mit Vorgaben aufgefüllt."""
    wurzel = repo_wurzel(repo)
    datei = wurzel / KONFIG_PFAD
    if not datei.is_file():
        return dict(DEFAULTS)
    try:
        eigen = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        log.warning("Konfig unlesbar (%s) — Vorgaben gelten: %s", datei, fehler)
        return dict(DEFAULTS)
    if not isinstance(eigen, dict):
        log.warning("Konfig ist kein Objekt (%s) — Vorgaben gelten.", datei)
        return dict(DEFAULTS)
    return _mische(DEFAULTS, eigen)


def staffel_modus(konfig: dict[str, Any]) -> str:
    """ "hook" oder "eltern" — Umgebungsvariable ``TO_SPAWN_STAFFEL_MODUS`` gewinnt."""
    modus = os.environ.get("TO_SPAWN_STAFFEL_MODUS") or str(
        konfig.get("staffel", {}).get("modus", "eltern")
    )
    return modus if modus in ("hook", "eltern") else "eltern"
