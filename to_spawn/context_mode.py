"""context-mode — Pflicht-MCP in jeder to-spawn-Session (duoplus-management#237).

Claude Code lässt mit ``--strict-mcp-config`` nur die Server aus ``--mcp-config``
zu; das Plugin-MCP ``context-mode`` fällt dann weg, obwohl das Plugin installiert
ist (Befund 19.09.2026, Bau-Server). ``bau.py`` trägt den Server deshalb selbst in
seine ``mcp.json`` ein — unter demselben Namen, den Claude Code dem Plugin-Server
gibt, damit die Werkzeuge weiter ``mcp__plugin_context-mode_context-mode__ctx_*``
heißen (die Plugin-Hooks nennen genau diese Namen).

Der Pfad kommt aus der Installation (``installed_plugins.json``, sonst neueste
Version im Plugin-Cache), nie aus einer festen Versionsnummer. Fehlt das Plugin,
meldet jeder Launcher das laut und startet nicht ohne.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

#: Plugin-Kennung in Claude Codes Registry (``<plugin>@<marketplace>``).
PLUGIN = "context-mode@context-mode"
#: Server-Name, den Claude Code dem Plugin-MCP gibt → gleiche Werkzeug-Namen. Pflicht, nicht Kür:
#: die Plugin-Hooks bilden und matchen genau ``mcp__plugin_context-mode_context-mode__``
#: (``hooks/core/tool-naming.mjs``, ``routing.mjs``); ein anderer Name lenkt ctx-Ausgaben falsch.
SERVER_NAME = "plugin_context-mode_context-mode"
#: Einstiegsdatei des MCP-Servers im Plugin-Ordner.
START = "start.mjs"
INSTALL_BEFEHL = "claude plugin install context-mode@context-mode"
#: Claude Codes Konfig-Ordner — ``CLAUDE_CONFIG_DIR`` gilt dort wie hier.
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


class ContextModeFehlt(RuntimeError):
    """Plugin nicht installiert oder ohne ``start.mjs`` — Session darf nicht still ohne starten."""


def _lies_json(pfad: Path) -> dict[str, Any]:
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return daten if isinstance(daten, dict) else {}


def registry_unlesbar(claude_dir: Path) -> bool:
    """Registry-Datei da, aber kein JSON (NUL-Bytes 11.–17.09.2026) — Claude lädt dann KEIN Plugin."""
    datei = claude_dir / "plugins" / "installed_plugins.json"
    if not datei.is_file():
        return False
    try:
        json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return False


def aktiviert(claude_dir: Path) -> bool:
    """``enabledPlugins`` in ``settings.json``: nur ein ausdrückliches ``false`` schaltet ab."""
    einstellungen = _lies_json(claude_dir / "settings.json")
    wert = (einstellungen.get("enabledPlugins") or {}).get(PLUGIN, True)
    return wert is not False


def _aus_registry(claude_dir: Path) -> Path | None:
    """``installPath`` aus ``plugins/installed_plugins.json`` (Liste je Scope oder ein Objekt)."""
    registry = _lies_json(claude_dir / "plugins" / "installed_plugins.json")
    plugins = registry.get("plugins")
    eintraege = plugins.get(PLUGIN) if isinstance(plugins, dict) else None
    if isinstance(eintraege, dict):
        eintraege = [eintraege]
    for eintrag in eintraege or []:
        pfad = eintrag.get("installPath") if isinstance(eintrag, dict) else None
        if pfad and (Path(pfad) / START).is_file():
            return Path(pfad)
    return None


def _versions_schluessel(ordner: Path) -> tuple[tuple[int, ...], str]:
    teile = tuple(int(t) if t.isdigit() else 0 for t in ordner.name.split("."))
    return teile, ordner.name


def _aus_cache(claude_dir: Path) -> Path | None:
    """Neueste Version unter ``plugins/cache/context-mode/context-mode/<version>/`` mit start.mjs."""
    cache = claude_dir / "plugins" / "cache" / "context-mode" / "context-mode"
    if not cache.is_dir():
        return None
    kandidaten = [d for d in cache.iterdir() if d.is_dir() and (d / START).is_file()]
    if not kandidaten:
        return None
    return max(kandidaten, key=_versions_schluessel)


def plugin_wurzel(claude_dir: Path | None = None) -> Path | None:
    """Ordner des installierten Plugins (mit ``start.mjs``) oder None."""
    ordner = claude_dir or CLAUDE_DIR
    return _aus_registry(ordner) or _aus_cache(ordner)


def server_definition(claude_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """``mcpServers``-Eintrag für die ``mcp.json`` einer Session: ``node <wurzel>/start.mjs``.

    Bewusst nicht aus der ``plugin.json`` des Plugins: dort stehen nach einem Update
    absolute Pfade der Vorversion (Befund Prüfpanel 21.09.2026, das Plugin heilt sie erst
    im SessionStart-Hook — nach dem MCP-Start). Die Wurzel hier ist auf ``start.mjs`` geprüft.
    """
    wurzel = pruefen(claude_dir)
    return {SERVER_NAME: {"command": "node", "args": [str(wurzel / START)]}}


def hinweis(claude_dir: Path | None = None) -> str:
    """Eine Zeile für Setup/Startprotokoll: bereit mit Pfad oder laut FEHLT (mit Grund)."""
    try:
        wurzel = pruefen(claude_dir, streng=True)
    except ContextModeFehlt as fehler:
        return f"context-mode (Pflicht-MCP jeder Session): FEHLT — {fehler}"
    return f"context-mode (Pflicht-MCP jeder Session): bereit — {wurzel}"


def pruefen(claude_dir: Path | None = None, streng: bool = False) -> Path:
    """Wurzel liefern oder :class:`ContextModeFehlt`.

    ``streng`` = der Launcher verlässt sich darauf, dass Claude Code das Plugin selbst lädt
    (Wächter, ohne eigene mcp.json): dann müssen Registry lesbar und das Plugin in
    ``enabledPlugins`` nicht abgeschaltet sein. ``bau.py`` schreibt den Server selbst und
    braucht nur die Dateien.
    """
    ordner = claude_dir or CLAUDE_DIR
    wurzel = plugin_wurzel(ordner)
    if wurzel is None:
        raise ContextModeFehlt(
            f"Plugin context-mode fehlt unter {ordner / 'plugins'} — Pflicht in jeder "
            f"to-spawn-Session (Token-Sparer, #237). Installieren: {INSTALL_BEFEHL}"
        )
    if streng and registry_unlesbar(ordner):
        raise ContextModeFehlt(
            f"Plugin-Registry {ordner / 'plugins' / 'installed_plugins.json'} ist kein JSON — "
            f"Claude Code lädt so kein Plugin, context-mode fiele still weg. Neu installieren: {INSTALL_BEFEHL}"
        )
    if streng and not aktiviert(ordner):
        raise ContextModeFehlt(
            f"context-mode ist in {ordner / 'settings.json'} unter enabledPlugins abgeschaltet — "
            "Pflicht in jeder to-spawn-Session (#237): claude plugin enable context-mode@context-mode"
        )
    return wurzel
