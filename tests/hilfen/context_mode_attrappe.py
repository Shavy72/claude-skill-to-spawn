"""Gestelltes context-mode-Plugin für Tests mit eigenem HOME (duoplus-management#237).

Seit #237 startet kein Launcher ohne installiertes Plugin. Tests, die ein leeres
HOME stellen, legen darin diese Ordner an — der Pfad zählt, nicht der Inhalt.
"""

from __future__ import annotations

import json
from pathlib import Path

VERSION = "1.0.169"


def plugin_anlegen(heim: Path, version: str = VERSION, registrieren: bool = True) -> Path:
    """``<heim>/.claude/plugins/…/<version>/`` mit start.mjs + plugin.json (+ Registry)."""
    claude_dir = heim / ".claude"
    wurzel = claude_dir / "plugins" / "cache" / "context-mode" / "context-mode" / version
    (wurzel / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (wurzel / "start.mjs").write_text("// Attrappe, nur der Pfad zählt (#237)\n", encoding="utf-8")
    (wurzel / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "context-mode",
                "version": version,
                "mcpServers": {
                    "context-mode": {
                        "command": "node",
                        "args": ["${CLAUDE_PLUGIN_ROOT}/start.mjs"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    if registrieren:
        (claude_dir / "plugins" / "installed_plugins.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "plugins": {
                        "context-mode@context-mode": [
                            {"scope": "user", "installPath": str(wurzel), "version": version}
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
    return wurzel
