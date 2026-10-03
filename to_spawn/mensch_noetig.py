"""„Mensch nötig“-Einträge des Review-Stop-Hooks lesen (``~/.claude/hooks/review/stop.py``).

Der Hook hängt je roter Bau-Session nach max. Fixrunden eine JSON-Zeile an
(``session_id, repo, fingerprint, spec, ticket, zeit, fix_runde``); der Aufseher
meldet jeden Eintrag seiner Spec genau einmal.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def pfad() -> Path:
    """Gleicher Pfad wie ``mensch_noetig_pfad()`` im Review-Stop-Hook."""
    return Path(os.environ.get("REVIEW_MENSCH_NOETIG") or Path.home() / ".claude" / ".review" / "mensch_noetig.jsonl")


def schluessel(eintrag: dict[str, Any]) -> str:
    """Eine Meldung je Session und Code-Stand — Folgezeilen desselben Stands (neue Zeit) nicht."""
    return f"{eintrag.get('session_id')}|{eintrag.get('fingerprint')}"


def _alter_schluessel(eintrag: dict[str, Any]) -> str:
    """Format bis Runde 5 (mit Zeit) — schon gemerkte Einträge nicht erneut melden."""
    return f"{schluessel(eintrag)}|{eintrag.get('zeit')}"


def eintraege_fuer_spec(spec: int, gesehen: set[str]) -> list[dict[str, Any]]:
    """Noch nicht gesehene Einträge dieser Spec; kaputte Zeilen werden übersprungen."""
    datei = pfad()
    try:
        text = datei.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as fehler:
        log.warning("%s nicht lesbar: %s", datei, fehler)
        return []
    treffer: list[dict[str, Any]] = []
    for nr, zeile in enumerate(text.splitlines(), start=1):
        if not zeile.strip():
            continue
        try:
            eintrag = json.loads(zeile)
        except json.JSONDecodeError:
            log.warning("%s Zeile %d kaputt — übersprungen.", datei, nr)
            continue
        if not isinstance(eintrag, dict):
            log.warning("%s Zeile %d kein Objekt — übersprungen.", datei, nr)
            continue
        if str(eintrag.get("spec")) != str(spec):
            continue
        if schluessel(eintrag) in gesehen or _alter_schluessel(eintrag) in gesehen:
            continue
        gesehen = gesehen | {schluessel(eintrag)}  # gleiche Datei, gleicher Stand: nur erste Zeile
        treffer.append(eintrag)
    return treffer
