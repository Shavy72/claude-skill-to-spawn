"""Spawn-Befehl: Regularien prüfen, Ziel erfragen, an die Terminal-Skripte geben.

Die Terminal-Arbeit machen die Skripte — hier wird nur delegiert. Windows:
``spawn_local.ps1`` (Windows Terminal) und ``spawn_srv.ps1`` (SSH zum Bau-Server).
Linux/macOS (#257, kein ``pwsh`` nötig): ``skripte/spawn_srv.sh`` (tmux) für ``local``,
``ssh <ssh_ziel> bash scripts/spawn_srv.sh`` für ``srv``.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence, TextIO

from . import manifest

log = logging.getLogger("to_spawn.spawn")

SKILL_ORDNER = Path(__file__).resolve().parent.parent
SKRIPTE = {"local": "spawn_local.ps1", "srv": "spawn_srv.ps1"}
SPAWN_SH = SKILL_ORDNER / "skripte" / "spawn_srv.sh"


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
    """argv des Terminal-Skripts; auf Nicht-Windows ohne ``pwsh`` (#257).

    ``srv`` braucht dort ``server_repo`` aus der Konfig (Repo-Ordner auf dem Bau-Server) —
    fehlt er, ``ValueError`` mit klarer Meldung statt eines blinden SSH-Aufrufs.
    """
    if sys.platform != "win32":
        return _befehl_unix(ziel, spec, tickets, konfig, dry_run)
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


def _befehl_unix(
    ziel: str,
    spec: int | str,
    tickets: Sequence[str] | None,
    konfig: dict[str, Any],
    dry_run: bool,
) -> list[str]:
    argumente = [str(spec)]
    if tickets:
        argumente += ["--tickets", ",".join(str(t) for t in tickets)]
    if dry_run:
        argumente.append("--dry-run")
    if ziel == "local":
        return ["bash", str(SPAWN_SH), *argumente]
    server_repo = str(konfig.get("server_repo") or "").strip()
    if not server_repo:
        raise ValueError(
            "Ziel Server braucht `server_repo` in .to-spawn/config.json "
            "(Repo-Ordner auf dem Bau-Server) — oder lokal starten (Ziel 1)."
        )
    ssh_ziel = str(konfig.get("ssh_ziel") or "bau-server")
    fern = f"cd {shlex.quote(server_repo)} && bash scripts/spawn_srv.sh {shlex.join(argumente)}"
    return ["ssh", ssh_ziel, fern]


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
    try:
        befehl = baue_befehl(gewaehlt, spec, tickets, konfig, dry_run)
    except ValueError as fehler:
        log.error("%s", fehler)
        return 2
    # Lokales Skript (pwsh -File <Skript> bzw. bash <Skript>): muss auf der Platte liegen.
    if befehl[0] != "ssh":
        skript = Path(befehl[2] if befehl[0] != "bash" else befehl[1])
        if not skript.is_file():
            log.error("Terminal-Skript fehlt: %s", skript)
            return 2
    log.info("Ziel %s · %s", gewaehlt, " ".join(befehl))
    if dry_run:
        print(" ".join(befehl))
        return 0
    # Das Skript arbeitet im Repo des Aufrufs, nicht im Repo aus ~/.bashrc (#212/#257).
    umgebung = {**os.environ, "TO_SPAWN_REPO": str(repo)}
    return subprocess.run(befehl, cwd=str(repo), check=False, env=umgebung).returncode
