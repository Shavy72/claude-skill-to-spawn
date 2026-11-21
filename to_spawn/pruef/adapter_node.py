"""Node-Adapter: erkennt ein Node-Repo und prüft es voll über ``npm test`` (Paket-intern, #99)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from to_spawn.pruef import ausfuehren
from to_spawn.pruef.leiter import Plan


class NodeAdapter:
    name = "node"

    def erkennt(self, repo: Path) -> bool:
        return (repo / "package.json").is_file()

    def voll(self, repo: Path) -> Plan:
        try:
            paket = json.loads((repo / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as fehler:
            return Plan(
                werkzeug="npm@?", befehl=[], fehler=f"package.json unlesbar: {fehler}"
            )
        # which() findet unter Windows npm.cmd — subprocess braucht den vollen Pfad.
        npm = shutil.which("npm")
        if not npm:
            return Plan(werkzeug="npm@?", befehl=[], fehler="npm fehlt im PATH")
        probe = ausfuehren.lauf([npm, "--version"], repo)
        werkzeug = f"npm@{probe.ausgabe.strip() or '?'}" if probe.code == 0 else "npm@?"
        if not isinstance(paket, dict) or not (paket.get("scripts") or {}).get("test"):
            return Plan(
                werkzeug=werkzeug, befehl=[], fehler="package.json ohne scripts.test"
            )
        abhaengig = paket.get("dependencies") or paket.get("devDependencies")
        if abhaengig and not (repo / "node_modules").is_dir():
            return Plan(
                werkzeug=werkzeug, befehl=[], fehler="node_modules fehlt (npm install)"
            )
        return Plan(werkzeug=werkzeug, befehl=[npm, "test"], fehler="")

    def deute(self, code: int) -> int:
        # 127 = Test-Werkzeug im Skript nicht gefunden (Shell) → Werkzeug, nicht Code.
        if code == 0:
            return 0
        if code == ausfuehren.NICHT_GESTARTET:
            return 2
        return 1


ADAPTER = NodeAdapter()
