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
import urllib.parse
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


def ticket_daten(ticket: str, cwd: Path | None = None) -> dict[str, Any] | None:
    """Zustand, Labels, Text und Zuständige eines Issues (``None`` bei Fehler)."""
    daten = json_lauf(
        ["issue", "view", str(ticket), "--json", "state,labels,body,assignees"], cwd=cwd
    )
    if not isinstance(daten, dict) or "state" not in daten:
        return None
    return daten


def _git_ausgabe(args: list[str], cwd: Path) -> tuple[int, str]:
    """``git <args>`` im Ordner ``cwd``; (Exit-Code, stdout) — 127 wenn git fehlt."""
    try:
        fertig = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as fehler:
        log.warning("git-Aufruf fehlgeschlagen: %s", fehler)
        return 127, ""
    return fertig.returncode, (fertig.stdout or "").strip()


def hauptzweig(repo: Path | None = None) -> str:
    """Name des Hauptzweigs (``master``/``main``/…) — nie hart verdrahtet (#257).

    Reihenfolge: 1. ``hauptzweig`` in ``.to-spawn/config.json`` · 2. ``origin/HEAD``
    · 3. erster vorhandener von ``origin/main``, ``origin/master`` (``main`` zuerst —
    Fremd-Repos heißen heute meist so; DuoPlus hat kein ``origin/main``) · 4. ``master``.
    """
    from . import config

    wurzel = config.repo_wurzel(repo)
    eigen = str(config.lade(wurzel).get("hauptzweig") or "").strip()
    if eigen:
        return eigen
    code, kopf = _git_ausgabe(["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], wurzel)
    if code == 0 and kopf.startswith("origin/") and len(kopf) > len("origin/"):
        return kopf[len("origin/") :]
    for name in ("main", "master"):
        if _git_ausgabe(["rev-parse", "--verify", "-q", f"origin/{name}"], wurzel)[0] == 0:
            return name
    return "master"


def label_sicherstellen(
    repo_pfad: Path,
    label: str,
    farbe: str = "d93f0b",
    beschreibung: str = "Ticket wartet auf menschliche Abnahme (to-spawn)",
) -> bool:
    """Sorgt dafür, dass das Label im GitHub-Repo existiert — erst prüfen, dann anlegen (#257).

    Reihenfolge: ``gh api repos/{owner}/{repo}/labels/<label>`` (``gh`` löst die
    Platzhalter aus dem origin des ``repo_pfad``) → Exit 0 = vorhanden, nichts wird
    geschrieben (Farbe/Beschreibung bleiben, wie sie sind). Nur wenn das Label fehlt,
    ``gh label create`` ohne ``--force``.

    ``False`` mit Warnung, wenn ``gh`` fehlt oder das Anlegen scheitert (kein origin,
    nicht eingeloggt) — der Aufrufer läuft weiter, das Label holt man von Hand nach.
    """
    pfad = "repos/{owner}/{repo}/labels/" + urllib.parse.quote(label, safe="")
    code, _ausgabe = lauf(["api", pfad], cwd=repo_pfad)
    if code == 127:
        log.warning("gh fehlt — Label %s nicht angelegt (gh label create %s).", label, label)
        return False
    if code == 0:
        return True
    code, _ausgabe = lauf(
        ["label", "create", label, "--color", farbe, "--description", beschreibung],
        cwd=repo_pfad,
    )
    if code == 127:
        log.warning("gh fehlt — Label %s nicht angelegt (gh label create %s).", label, label)
        return False
    if code != 0:
        log.warning(
            "Label %s nicht angelegt (gh Exit %s) — von Hand: gh label create %s", label, code, label
        )
        return False
    return True
