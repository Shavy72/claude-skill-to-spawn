"""Schlankes MCP-Profil für Aufseher-, Takt- und Leitstand-Sessions (duoplus-management#1428).

Ohne ``--strict-mcp-config`` startet jede Session alle MCP-Server des Benutzer-Profils
(graphify-mcp, chrome-devtools, firecrawl, perplexity …) — Messung 21.11.2026 auf dem
Bau-Server: rund 0,7–0,9 GB RSS + Swap je Session, genutzt von keinem Prompt. Diese
Sessions bekommen deshalb nur den Pflicht-Server context-mode (#237). Bau-Sessions
baut ``bau.py`` weiter selbst (Manifest-MCPs + context-mode).

Schnittstelle: :func:`flags` liefert ``--mcp-config <datei> --strict-mcp-config``;
:func:`schlank` setzt sie in einen fertigen ``claude``-Befehl direkt hinter ``cmd[0]``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from . import context_mode

log = logging.getLogger(__name__)

SPERRE = "--strict-mcp-config"


def server() -> dict[str, dict[str, Any]]:
    """Erlaubte Server: nur context-mode. Fehlt das Plugin, lud es vorher auch nicht → leer."""
    try:
        return context_mode.server_definition()
    except context_mode.ContextModeFehlt as fehler:
        log.warning("context-mode fehlt, Session startet ohne MCP-Server: %s", fehler)
        return {}


def _datei(inhalt: str) -> Path:
    """Konfig-Datei je Inhalt (Hash im Namen) — Datei statt JSON-String, weil der
    Windows-Shell-Rückfall in ``waechter_lauf._starte`` Anführungszeichen nicht übersteht."""
    kennung = hashlib.sha256(inhalt.encode("utf-8")).hexdigest()[:12]
    ziel = Path(tempfile.gettempdir()) / f"to-spawn-mcp-schlank-{kennung}.json"
    fd, roh = tempfile.mkstemp(prefix=ziel.stem + "-", suffix=".tmp", dir=ziel.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as aus:
        aus.write(inhalt)
    os.replace(roh, ziel)  # atomar; ersetzt auch einen fremd angelegten Link statt ihm zu folgen
    return ziel


def flags() -> list[str]:
    """``--mcp-config <datei> --strict-mcp-config`` — variadisch, darum die Sperre direkt dahinter."""
    inhalt = json.dumps({"mcpServers": server()}, ensure_ascii=False, sort_keys=True)
    return ["--mcp-config", str(_datei(inhalt)), SPERRE]


def schlank(cmd: list[str]) -> list[str]:
    """``cmd`` mit :func:`flags` direkt hinter ``cmd[0]``; schon gesperrte Befehle bleiben unverändert."""
    if SPERRE in cmd:
        return list(cmd)
    return [cmd[0], *flags(), *cmd[1:]]
