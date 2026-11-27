"""Repo-neutrale Minimal-Attrappe für Git-Bereichs- und Zettel-Abfragen (#872)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_TICKET_MUSTER = re.compile(r"#(\d+)")
_HEX_ZETTEL = re.compile(r"[0-9a-fA-F]{7,40}")


def git_ausfuehren(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Führt einen lesenden Git-Befehl im Ziel-Repo aus."""
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def spitze_ref(repo: Path, rueckfall: str) -> str:
    """Liefert ``origin/master`` bzw. ``origin/main``, sonst ``rueckfall``."""
    for ref in ("origin/master", "origin/main"):
        if git_ausfuehren(repo, "rev-parse", "--verify", "--quiet", ref).returncode == 0:
            return ref
    return rueckfall


def commits_im_bereich(repo: Path, von: str, bis: str) -> list[tuple[str, str, list[str]]] | None:
    """Liefert ``(sha, betreff, dateien)`` von ``von..bis`` (älteste zuerst) oder ``None`` bei Git-Fehler."""
    lauf = git_ausfuehren(repo, "log", "--reverse", "--format=%H\x1f%s", f"{von}..{bis}")
    if lauf.returncode != 0:
        return None
    ergebnis: list[tuple[str, str, list[str]]] = []
    for zeile in lauf.stdout.splitlines():
        if not zeile.strip():
            continue
        sha, _, betreff = zeile.partition("\x1f")
        diff = git_ausfuehren(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha)
        if diff.returncode != 0:
            return None
        dateien = [p for p in diff.stdout.splitlines() if p.strip()]
        ergebnis.append((sha.strip(), betreff.strip(), dateien))
    return ergebnis


def ticket_nummern(betreff: str) -> list[str]:
    """Liest alle ``#<N>``-Ticket-Nummern aus einer Commit-Betreffzeile."""
    return _TICKET_MUSTER.findall(betreff)


def zettel_menge(freigabe_ordner: Path) -> set[str]:
    """Liefert alle Dateinamen im Freigabe-Ordner (leer, falls Ordner fehlt)."""
    if not freigabe_ordner.is_dir():
        return set()
    return {p.name for p in freigabe_ordner.iterdir() if p.is_file()}


def sha_passt(sha: str, zettel: set[str]) -> bool:
    """Prüft, ob ein SHA-Zettel (mindestens 7 Hex-Zeichen) den Commit ``sha`` abdeckt."""
    sha_klein = sha.lower()
    return any(_HEX_ZETTEL.fullmatch(z) and sha_klein.startswith(z.lower()) for z in zettel)
