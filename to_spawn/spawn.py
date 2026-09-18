"""Spawn-Befehl: Regularien prüfen, Ziel erfragen, an die Terminal-Skripte geben.

Die Terminal-Arbeit machen weiterhin ``spawn_local.ps1`` (Windows Terminal) und
``spawn_srv.ps1`` (tmux auf dem Bau-Server) — hier wird nur delegiert.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence, TextIO

from . import manifest

log = logging.getLogger("to_spawn.spawn")

SKILL_ORDNER = Path(__file__).resolve().parent.parent
SKRIPTE = {"local": "spawn_local.ps1", "srv": "spawn_srv.ps1"}


def frage_ziel(vorgabe: str, eingabe: TextIO | None = None) -> str:
    """„lokal (1) oder Server (2)?" — leere Antwort nimmt die Vorgabe."""
    vorbelegt = "1" if vorgabe == "local" else "2"
    print(f"lokal (1) oder Server (2)? [{vorbelegt}] ", end="", flush=True)
    strom = eingabe or sys.stdin
    antwort = (strom.readline() or "").strip()
    if not antwort:
        return vorgabe
    if antwort in ("1", "l", "lokal", "local"):
        return "local"
    if antwort in ("2", "s", "server", "srv"):
        return "srv"
    log.warning("Antwort %r nicht verstanden — Vorgabe %s gilt.", antwort, vorgabe)
    return vorgabe


def baue_befehl(
    ziel: str,
    spec: int | str,
    tickets: Sequence[str] | None,
    konfig: dict[str, Any],
    dry_run: bool,
) -> list[str]:
    skript = SKILL_ORDNER / SKRIPTE[ziel]
    pwsh = shutil.which("pwsh") or shutil.which("powershell") or "pwsh"
    befehl = [pwsh, "-File", str(skript), "-Spec", str(spec)]
    if tickets:
        befehl += ["-Tickets", ",".join(str(t) for t in tickets)]
    if ziel == "srv":
        befehl += ["-Zielserver", str(konfig.get("ssh_ziel", "bau-server"))]
    if dry_run:
        befehl.append("-DryRun")
    return befehl


def spawn(
    repo: Path,
    spec: int | str,
    konfig: dict[str, Any],
    *,
    ziel: str | None = None,
    tickets: Sequence[str] | None = None,
    dry_run: bool = False,
    eingabe: TextIO | None = None,
) -> int:
    """Regularien prüfen, dann das passende Terminal-Skript starten."""
    bericht = manifest.pruefe(repo, spec, konfig, auswahl=tickets)
    print(bericht.text())
    if not bericht.sauber:
        return manifest.EXIT_WEIGERUNG

    gewaehlt = ziel or frage_ziel(str(konfig.get("ziel_default", "srv")), eingabe)
    if gewaehlt not in SKRIPTE:
        log.error("Unbekanntes Ziel: %s", gewaehlt)
        return 2
    befehl = baue_befehl(gewaehlt, spec, tickets, konfig, dry_run)
    skript = Path(befehl[2])
    if not skript.is_file():
        log.error("Terminal-Skript fehlt: %s", skript)
        return 2
    log.info("Ziel %s · %s", gewaehlt, " ".join(befehl))
    if dry_run:
        print(" ".join(befehl))
        return 0
    return subprocess.run(befehl, cwd=str(repo), check=False).returncode
