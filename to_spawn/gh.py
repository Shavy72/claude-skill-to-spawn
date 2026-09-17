"""Schmale GitHub-Anbindung über die ``gh``-CLI.

Im Test zeigt ``TO_SPAWN_GH_STUB`` auf ein Ersatz-Skript — GitHub ist ein
externer Dienst und die einzige zweite Attrappe im Weg-Test (dokumentiert im
README). Ohne die Variable läuft echtes ``gh``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("to_spawn.gh")


def gh_befehl() -> list[str] | None:
    """Aufruf-Vorspann für ``gh`` (oder das Stub-Skript); ``None`` wenn nichts da."""
    stub = os.environ.get("TO_SPAWN_GH_STUB")
    if stub:
        return [sys.executable, stub] if stub.endswith(".py") else [stub]
    pfad = shutil.which("gh")
    return [pfad] if pfad else None


def lauf(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """``gh <args>`` ausführen; gibt (Exit-Code, stdout) zurück."""
    vorspann = gh_befehl()
    if vorspann is None:
        return 127, ""
    try:
        fertig = subprocess.run(
            [*vorspann, *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as fehler:
        log.warning("gh-Aufruf fehlgeschlagen: %s", fehler)
        return 127, ""
    return fertig.returncode, (fertig.stdout or "").strip()


def json_lauf(args: list[str], cwd: Path | None = None) -> Any:
    """Wie :func:`lauf`, aber stdout als JSON (``None`` bei Fehler)."""
    code, ausgabe = lauf(args, cwd=cwd)
    if code != 0 or not ausgabe:
        return None
    try:
        return json.loads(ausgabe)
    except ValueError:
        log.warning("gh-Ausgabe ist kein JSON: %s", ausgabe[:200])
        return None


def repo_aus_origin(cwd: Path | None = None, fallback: str = "") -> str:
    """``owner/name`` aus ``git remote get-url origin`` (https oder ssh)."""
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        ).stdout.strip()
    except OSError:
        return fallback
    treffer = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", url)
    if treffer:
        return treffer.group(1)
    treffer = re.search(r"([^/\\]+/[^/\\]+?)(?:\.git)?$", url)
    return treffer.group(1) if treffer else fallback


def blocked_by(repo_slug: str, ticket: str, cwd: Path | None = None) -> list[dict] | None:
    """Native Blocker-Kanten eines Tickets (``None`` = Abfrage fehlgeschlagen)."""
    daten = json_lauf(
        ["api", f"repos/{repo_slug}/issues/{ticket}/dependencies/blocked_by"], cwd=cwd
    )
    return daten if isinstance(daten, list) else None


def ticket_offen(ticket: str, cwd: Path | None = None) -> bool | None:
    """``True`` wenn das Issue offen ist, ``False`` wenn zu, ``None`` bei Fehler."""
    daten = json_lauf(["issue", "view", str(ticket), "--json", "state"], cwd=cwd)
    if not isinstance(daten, dict) or "state" not in daten:
        return None
    return str(daten["state"]).upper() == "OPEN"
