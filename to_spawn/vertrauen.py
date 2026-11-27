"""Vertrauens-Dialog von Claude Code und Antigravity CLI vorab bestätigen (#257, #855).

Beim ersten Start in einem Ordner fragen Claude Code („Do you trust the files in this
folder?“) und ``agy`` („Do you trust the contents of this project?“) nach Vertrauen — eine
unbeaufsichtigte Bau-Session hängt an dieser Frage oder lässt den Dialog im tmux-Fenster
stehen.

* ``claude``: ``~/.claude.json`` unter ``projects[<Pfad>].hasTrustDialogAccepted`` (#257).
* ``agy``: ``~/.gemini/antigravity-cli/settings.json`` unter ``trustedWorkspaces`` (#855).

Mehrere Starter (``spawn_srv.sh`` startet viele Fenster kurz nacheinander) ändern die
Datei gleichzeitig — Lesen und Schreiben laufen deshalb unter der Sperrdatei
``<datei>.to-spawn.lock`` (:func:`to_spawn.sperre.halte`, wartet bis 60 s;
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
from typing import Any

from . import sperre, werkzeug

log = logging.getLogger("to_spawn.vertrauen")
#: So lange wartet ein Starter auf einen anderen, dann geht es mit Warnung ohne Sperre weiter.
SPERRE_WARTEN_S = 60.0
#: Relativer Pfad zur ``agy``-Einstellungsdatei unterhalb des Heimordners (#855).
AGY_SETTINGS_REL = werkzeug.AGY_DATEN_REL / "settings.json"


def claude_json_pfad() -> Path:
    """``~/.claude.json`` — zur Laufzeit, damit ein umgebogenes ``HOME`` (Tests) greift."""
    return Path.home() / ".claude.json"


def agy_settings_pfad() -> Path:
    """``~/.gemini/antigravity-cli/settings.json`` — zur Laufzeit für Linux und Windows (#855)."""
    return werkzeug.agy_daten_ordner() / "settings.json"


def _lade(datei: Path) -> dict[str, Any]:
    if not datei.is_file():
        return {}
    daten = json.loads(datei.read_text(encoding="utf-8"))
    if not isinstance(daten, dict):
        raise TypeError(f"{datei} ist kein JSON-Objekt")
    return daten


def _schreibe_atomar(datei: Path, daten: dict[str, Any]) -> None:
    """Erst Nachbardatei, dann ``os.replace`` — nie eine halb geschriebene Einstellungsdatei."""
    datei.parent.mkdir(parents=True, exist_ok=True)
    kennung, tmp_name = tempfile.mkstemp(prefix=f".{datei.name}.", dir=str(datei.parent))
    try:
        with os.fdopen(kennung, "w", encoding="utf-8") as strom:
            json.dump(daten, strom, ensure_ascii=False, indent=2)
            strom.write("\n")
        os.replace(tmp_name, datei)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def sperrdatei_pfad(claude_json: Path) -> Path:
    """``<claude_json>.to-spawn.lock`` neben der Datei."""
    return claude_json.parent / f"{claude_json.name}.to-spawn.lock"


@contextlib.contextmanager
def gesperrt(claude_json: Path) -> Iterator[None]:
    """Sperrdatei um Lesen + Schreiben von ``claude_json``; Fehler → Warnung, ohne Sperre weiter.

    Wartet höchstens :data:`SPERRE_WARTEN_S` auf einen anderen Starter (#95).
    """
    pfad = sperrdatei_pfad(claude_json)
    with contextlib.ExitStack() as stapel:
        try:
            gehalten = stapel.enter_context(sperre.halte(pfad, warten=SPERRE_WARTEN_S))
        except OSError as fehler:
            log.warning("Sperre auf %s nicht möglich (%s) — weiter ohne Sperre.", pfad, fehler)
            gehalten = True  # Warnung schon geschrieben
        if not gehalten:
            log.warning("Sperre auf %s nach %s s noch belegt — weiter ohne Sperre.", pfad, SPERRE_WARTEN_S)
        yield


def sicherstellen(
    *pfade: Path,
    claude_json: Path | None = None,
    runner: str = werkzeug.STANDARD_RUNNER,
    settings_json: Path | None = None,
) -> list[str]:
    """Vertrauen für jeden Ordner im passenden Werkzeug eintragen; fremde Schlüssel bleiben.

    * ``runner == "claude"`` (Standard): ``hasTrustDialogAccepted = true`` in ``~/.claude.json``.
    * ``runner == "agy"``: Ordner in ``trustedWorkspaces`` von ``~/.gemini/antigravity-cli/settings.json`` (#855).

    Rückgabe: die Pfade, für die die Datei geändert wurde (leer = alles war schon
    bestätigt, die Datei bleibt byte-gleich). Fehlt die Datei, wird sie angelegt.
    Fehler (unlesbare Datei, kein Schreibrecht) wirft der Aufrufer nicht weiter — er
    loggt eine Warnung; die Session soll trotzdem starten.
    """
    if runner == "agy":
        ziel = settings_json or agy_settings_pfad()
        with gesperrt(ziel):
            return _agy_sicherstellen_gesperrt(pfade, ziel)
    ziel = claude_json or claude_json_pfad()
    with gesperrt(ziel):
        return _sicherstellen_gesperrt(pfade, ziel)


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


def _agy_sicherstellen_gesperrt(pfade: tuple[Path, ...], settings_json: Path) -> list[str]:
    daten = _lade(settings_json)
    vertraut = daten.get("trustedWorkspaces")
    if not isinstance(vertraut, list):
        vertraut = []
        daten["trustedWorkspaces"] = vertraut
    vorhanden = {os.path.normcase(e) for e in vertraut if isinstance(e, str)}
    geaendert: list[str] = []
    for pfad in pfade:
        schluessel = str(Path(pfad).expanduser().resolve())
        norm = os.path.normcase(schluessel)
        if norm in vorhanden:
            continue
        vertraut.append(schluessel)
        vorhanden.add(norm)
        geaendert.append(schluessel)
    if geaendert or not settings_json.is_file():
        _schreibe_atomar(settings_json, daten)
    return geaendert


def still_sicherstellen(
    *pfade: Path,
    claude_json: Path | None = None,
    runner: str = werkzeug.STANDARD_RUNNER,
    settings_json: Path | None = None,
) -> list[str]:
    """Wie :func:`sicherstellen`, aber jeder Fehler wird nur als Warnung geloggt."""
    try:
        geaendert = sicherstellen(*pfade, claude_json=claude_json, runner=runner, settings_json=settings_json)
    except (OSError, ValueError, TypeError) as fehler:
        ziel = (settings_json or agy_settings_pfad()) if runner == "agy" else (claude_json or claude_json_pfad())
        log.warning("Vertrauens-Dialog nicht vorab bestätigt (%s): %s", ziel, fehler)
        return []
    for pfad in geaendert:
        log.info("Vertrauens-Dialog vorab bestätigt: %s", pfad)
    return geaendert
