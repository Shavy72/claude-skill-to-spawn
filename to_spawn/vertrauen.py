"""Vertrauens-Dialog von Claude Code vorab bestätigen (#257).

Beim ersten Start in einem Ordner fragt Claude Code „Do you trust the files in this
folder?“ — eine unbeaufsichtigte Bau-Session hängt an dieser Frage. Die Antwort steht
in ``~/.claude.json`` unter ``projects[<Pfad>].hasTrustDialogAccepted``; dieses Modul
setzt sie für Worktree und Repo-Wurzel, bevor ``claude`` startet.

Mehrere Starter (``spawn_srv.sh`` startet viele Fenster kurz nacheinander) ändern die
Datei gleichzeitig — Lesen und Schreiben laufen deshalb unter der Sperrdatei
``<claude_json>.to-spawn.lock`` (``fcntl.flock``, auf Windows ``msvcrt.locking``;
klappt die Sperre nicht, geht es mit Warnung ohne sie weiter) (#257 F6).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

log = logging.getLogger("to_spawn.vertrauen")


def claude_json_pfad() -> Path:
    """``~/.claude.json`` — zur Laufzeit, damit ein umgebogenes ``HOME`` (Tests) greift."""
    return Path.home() / ".claude.json"


def _lade(claude_json: Path) -> dict[str, Any]:
    if not claude_json.is_file():
        return {}
    daten = json.loads(claude_json.read_text(encoding="utf-8"))
    if not isinstance(daten, dict):
        raise TypeError(f"{claude_json} ist kein JSON-Objekt")
    return daten


def _schreibe_atomar(claude_json: Path, daten: dict[str, Any]) -> None:
    """Erst Nachbardatei, dann ``os.replace`` — nie eine halb geschriebene ``~/.claude.json``."""
    claude_json.parent.mkdir(parents=True, exist_ok=True)
    kennung, tmp_name = tempfile.mkstemp(prefix=".claude.json.", dir=str(claude_json.parent))
    try:
        with os.fdopen(kennung, "w", encoding="utf-8") as strom:
            json.dump(daten, strom, ensure_ascii=False, indent=2)
            strom.write("\n")
        os.replace(tmp_name, claude_json)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def sperrdatei_pfad(claude_json: Path) -> Path:
    """``<claude_json>.to-spawn.lock`` neben der Datei."""
    return claude_json.parent / f"{claude_json.name}.to-spawn.lock"


def _sperren(griff: IO[str]) -> bool:
    """Exklusive Sperre auf die offene Sperrdatei; ``False`` wenn das Betriebssystem nicht mitspielt."""
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(griff.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(griff.fileno(), fcntl.LOCK_EX)
    except (OSError, ImportError, AttributeError) as fehler:
        log.warning("Sperre auf %s nicht möglich (%s) — weiter ohne Sperre.", griff.name, fehler)
        return False
    return True


def _entsperren(griff: IO[str]) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            griff.seek(0)
            msvcrt.locking(griff.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(griff.fileno(), fcntl.LOCK_UN)
    except (OSError, ImportError, AttributeError) as fehler:
        log.warning("Sperre auf %s nicht gelöst (%s).", griff.name, fehler)


@contextlib.contextmanager
def gesperrt(claude_json: Path) -> Iterator[None]:
    """Sperrdatei um Lesen + Schreiben von ``claude_json``; Fehler → Warnung, ohne Sperre weiter."""
    sperre = sperrdatei_pfad(claude_json)
    try:
        sperre.parent.mkdir(parents=True, exist_ok=True)
        griff: IO[str] | None = open(sperre, "a+", encoding="utf-8")  # noqa: SIM115 — bleibt bis zum Ende offen
    except OSError as fehler:
        log.warning("Sperrdatei %s nicht anlegbar (%s) — weiter ohne Sperre.", sperre, fehler)
        griff = None
    gehalten = griff is not None and _sperren(griff)
    try:
        yield
    finally:
        if griff is not None:
            if gehalten:
                _entsperren(griff)
            griff.close()


def sicherstellen(*pfade: Path, claude_json: Path | None = None) -> list[str]:
    """``hasTrustDialogAccepted = true`` für jeden Ordner setzen; fremde Schlüssel bleiben.

    Rückgabe: die Pfade, für die die Datei geändert wurde (leer = alles war schon
    bestätigt, die Datei bleibt byte-gleich). Fehlt die Datei, wird sie angelegt.
    Fehler (unlesbare Datei, kein Schreibrecht) wirft der Aufrufer nicht weiter — er
    loggt eine Warnung; die Session soll trotzdem starten.
    """
    claude_json = claude_json or claude_json_pfad()
    with gesperrt(claude_json):
        return _sicherstellen_gesperrt(pfade, claude_json)


def _sicherstellen_gesperrt(pfade: tuple[Path, ...], claude_json: Path) -> list[str]:
    daten = _lade(claude_json)
    projekte = daten.get("projects")
    if not isinstance(projekte, dict):
        projekte = {}
        daten["projects"] = projekte
    geaendert: list[str] = []
    for pfad in pfade:
        schluessel = str(Path(pfad).expanduser().resolve())
        eintrag = projekte.get(schluessel)
        if not isinstance(eintrag, dict):
            eintrag = {}
            projekte[schluessel] = eintrag
        if eintrag.get("hasTrustDialogAccepted") is True:
            continue
        eintrag["hasTrustDialogAccepted"] = True
        geaendert.append(schluessel)
    if geaendert or not claude_json.is_file():
        _schreibe_atomar(claude_json, daten)
    return geaendert


def still_sicherstellen(*pfade: Path, claude_json: Path | None = None) -> list[str]:
    """Wie :func:`sicherstellen`, aber jeder Fehler wird nur als Warnung geloggt."""
    try:
        geaendert = sicherstellen(*pfade, claude_json=claude_json)
    except (OSError, ValueError, TypeError) as fehler:
        log.warning(
            "Vertrauens-Dialog nicht vorab bestätigt (%s): %s", claude_json or claude_json_pfad(), fehler
        )
        return []
    for pfad in geaendert:
        log.info("Vertrauens-Dialog vorab bestätigt: %s", pfad)
    return geaendert
