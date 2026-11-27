"""Repo-neutrale Minimal-Attrappe für Manifest- und Doku-Regeln der Deploy-Schlange (#872)."""

from __future__ import annotations

import json
from pathlib import Path


def eltern_specs(manifeste_ordner: Path) -> dict[str, set[str]]:
    """Ordnet jede Ticket-Nummer aus ``spec-*.json`` ihren Eltern-Spec-Nummern zu."""
    zuordnung: dict[str, set[str]] = {}
    if not manifeste_ordner.is_dir():
        return zuordnung
    for datei in sorted(manifeste_ordner.glob("spec-*.json")):
        try:
            daten = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(daten, dict):
            continue
        spec = daten.get("spec")
        tickets = daten.get("tickets")
        if spec is None or not isinstance(tickets, dict):
            continue
        spec_text = str(spec)
        for ticket in tickets:
            zuordnung.setdefault(str(ticket), set()).add(spec_text)
    return zuordnung


def ist_doku_pfad(pfad: str) -> bool:
    """Reine Doku-Pfade blockieren die Deploy-Schlange auch ohne Ticket-Nummer nicht."""
    norm = pfad.replace("\\", "/").lstrip("./")
    return norm.startswith("docs/") or norm.lower().endswith(".md")
