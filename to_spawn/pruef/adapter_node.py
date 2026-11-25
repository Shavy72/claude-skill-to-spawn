"""Node-Adapter: erkennt ein Node-Repo und prüft es voll über ``npm test`` (Paket-intern, #99)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from to_spawn.pruef import ausfuehren, leiter
from to_spawn.pruef.leiter import Plan


#: ANSI-Farbcodes, falls vitest trotz Pipe färbt.
_FARBE = re.compile(r"\x1b\[[0-9;]*m")
#: Shell-Verkettungen: danach würden angehängte Argumente nicht beim Test-Läufer landen.
_SHELL_KETTE = ("&&", "||", ";", "|")
#: Vitest als direkter Test-Befehl (optional mit ``npx``).
_VITEST = re.compile(r"^(?:npx\s+)?vitest(?:\s+run)?(?:\s|$)")
#: Läufer mit additivem ``--exclude <glob>`` auf der Kommandozeile (überschreibt keine Repo-Konfig, #446).
_MIT_EXCLUDE = re.compile(r"^(?:npx\s+)?(?:vitest|mocha)(?:\s|$)")


def _ohne_kette(skript: str) -> str:
    text = skript.strip()
    return "" if any(op in text for op in _SHELL_KETTE) else text


def _werkzeug_ausschluss(skript: str) -> list[str]:
    """Additiver Ausschluss für ``leiter.WERKZEUG_ORTE`` bei Läufern mit ``--exclude`` (#446)."""
    if not _MIT_EXCLUDE.match(_ohne_kette(skript)):
        return []
    return [arg for ort in leiter.WERKZEUG_ORTE for arg in ("--exclude", f"**/{ort}/**")]


class NodeAdapter:
    name = "node"

    def erkennt(self, repo: Path) -> bool:
        return (repo / "package.json").is_file()

    def voll(self, repo: Path) -> Plan:
        try:
            paket = json.loads((repo / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as fehler:
            return Plan(werkzeug="npm@?", befehl=[], fehler=f"package.json unlesbar: {fehler}")
        # which() findet unter Windows npm.cmd — subprocess braucht den vollen Pfad.
        npm = shutil.which("npm")
        if not npm:
            return Plan(werkzeug="npm@?", befehl=[], fehler="npm fehlt im PATH")
        probe = ausfuehren.lauf([npm, "--version"], repo)
        werkzeug = f"npm@{probe.ausgabe.strip() or '?'}" if probe.code == 0 else "npm@?"
        skripte = paket.get("scripts") if isinstance(paket, dict) else None
        skript = skripte.get("test") if isinstance(skripte, dict) else None
        if not isinstance(skript, str) or not skript.strip():
            return Plan(werkzeug=werkzeug, befehl=[], fehler="package.json ohne scripts.test")
        abhaengig = paket.get("dependencies") or paket.get("devDependencies")
        if abhaengig and not (repo / "node_modules").is_dir():
            return Plan(werkzeug=werkzeug, befehl=[], fehler="node_modules fehlt (npm install)")
        # Auswahl baut auf ``voll`` auf und erbt den Ausschluss der Werkzeug-Orte (#446).
        ausschluss = _werkzeug_ausschluss(skript)
        extra = ["--", *ausschluss] if ausschluss else []
        return Plan(werkzeug=werkzeug, befehl=[npm, "test", *extra], fehler="")

    def auswahl(self, repo: Path, basis: str) -> Plan:
        # ``vitest --changed``: git-Diff + Modul-Graph; Konfig/Abhängigkeit → alles (forceRerunTriggers).
        plan = self.voll(repo)
        if plan.fehler:
            return plan
        try:
            skript = json.loads((repo / "package.json").read_text(encoding="utf-8"))["scripts"]["test"]
        except (OSError, ValueError, KeyError, TypeError):
            skript = ""
        if not isinstance(skript, str) or not _VITEST.match(_ohne_kette(skript)):
            return Plan(werkzeug=plan.werkzeug, befehl=[], fehler="scripts.test ist nicht vitest")
        trenner = [] if "--" in plan.befehl else ["--"]
        return Plan(
            werkzeug=f"{plan.werkzeug}+vitest --changed",
            befehl=[*plan.befehl, *trenner, "--changed", basis, "--passWithNoTests"],
            fehler="",
        )

    def zaehle(self, code: int, ausgabe: str) -> tuple[int | None, int | None]:
        # Zeile ``Tests  3 passed (3)`` bzw. ``No test files found``; Gesamtzahl kennt vitest hier nicht.
        text = _FARBE.sub("", ausgabe)
        if "No test files found" in text:
            return 0, None
        treffer = re.findall(r"^\s*Tests\s+.*\((\d+)\)\s*$", text, re.MULTILINE)
        return (int(treffer[-1]), None) if treffer else (None, None)

    def deute(self, code: int) -> int:
        # 127 = Test-Werkzeug im Skript nicht gefunden (Shell) → Werkzeug, nicht Code.
        if code == 0:
            return 0
        if code == ausfuehren.NICHT_GESTARTET:
            return 2
        return 1


ADAPTER = NodeAdapter()
