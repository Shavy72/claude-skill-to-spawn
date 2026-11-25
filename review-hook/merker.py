"""PostToolUse Edit|Write|NotebookEdit: geänderte Datei für den Sitzungs-Review merken (VERTRAG.md §8)."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from _hookbasis import (
    ausfuehren,
    befehl,
    dateien_anhaengen,
    lade_sitzung,
    speichere_sitzung,
)

PROJEKTE = Path.home() / ".claude" / "projects"


def _unter(pfad: Path, ordner: Path) -> bool:
    try:
        pfad.relative_to(ordner.resolve())
    except ValueError:
        return False
    return True


def nicht_merken(pfad: Path) -> bool:
    return ".review" in pfad.parts or _unter(pfad, PROJEKTE) or _unter(pfad, Path(tempfile.gettempdir()))


def haupt(eingabe: dict[str, Any]) -> None:
    session_id = str(eingabe["session_id"])
    tool_input = eingabe.get("tool_input") or {}
    roh = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not roh:
        return None
    pfad = Path(roh)
    if not pfad.is_absolute():
        pfad = Path(eingabe.get("cwd") or Path.cwd()) / pfad
    pfad = pfad.resolve()
    if nicht_merken(pfad):
        return None
    text = pfad.as_posix()
    dateien_anhaengen(session_id, text)  # Beleg 11: race-freies Backup, unabhängig vom JSON unten
    sitzung = lade_sitzung(session_id)
    geaendert = "start" not in sitzung
    if geaendert:
        sitzung["start"] = time.time()
    if text not in sitzung["dateien"]:
        sitzung["dateien"].append(text)
        geaendert = True
    geaendert = start_sha_merken(sitzung, pfad, str(eingabe.get("agent_id") or "")) or geaendert
    if geaendert:
        speichere_sitzung(session_id, sitzung)
    return None


def start_sha_merken(sitzung: dict[str, Any], pfad: Path, agent_id: str = "") -> bool:
    """HEAD je Repo beim ersten Eintrag festhalten: committet die Sitzung vor Zugende,
    prüft stop.py gegen diese SHA statt gegen den (dann leeren) Diff zu HEAD.
    Bei Subagenten (``agent_id`` gesetzt) zusätzlich die Zuordnung Helfer → Repo merken (#214)."""
    ordner = pfad.parent
    while not ordner.is_dir() and ordner != ordner.parent:
        ordner = ordner.parent
    ergebnis = befehl(["git", "-C", str(ordner), "rev-parse", "--show-toplevel", "HEAD"])
    zeilen = ergebnis.stdout.splitlines()
    if ergebnis.returncode != 0 or len(zeilen) != 2:
        return False  # kein Repo oder noch kein Commit: stop.py prüft wie bisher gegen den Arbeitsbaum
    wurzel = Path(zeilen[0]).resolve().as_posix()
    geaendert = False
    shas: dict[str, str] = sitzung.setdefault("start_sha", {})
    if wurzel not in shas:
        shas[wurzel] = zeilen[1]
        geaendert = True
    if agent_id:
        helfer: dict[str, list[str]] = sitzung.setdefault("helfer_repos", {})
        liste = helfer.setdefault(agent_id, [])
        if wurzel not in liste:
            liste.append(wurzel)
            geaendert = True
    return geaendert


if __name__ == "__main__":
    ausfuehren("merker", haupt)
