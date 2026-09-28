"""Frage-Sperre (#321) — echter Weg ohne Attrappe: echte ``claude -p``-Session mit den Hooks aus ``bau.staffel_hooks()``.

Fall 1 (``TO_SPAWN_TICKET`` gesetzt): die Session endet mit einer Frage, der Stop-Hook
antwortet „Nimm deinen Vorschlag …“ → sie baut weiter und legt die Datei an.
Fall 2 (ohne ``TO_SPAWN_TICKET``): nichts ändert sich — die Session endet mit der Frage.
Übersprungen nur, wenn ``claude`` fehlt.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
#: Bei Modell-Abkündigung anpassen (billigstes aktuelles Modell).
MODELL = "claude-haiku-4-5-20251001"
PROMPT = (
    "Aufgabe: lege im aktuellen Ordner eine Datei an. Du weißt nicht, ob sie rot.txt oder blau.txt "
    "heißen soll. Lege JETZT noch nichts an und rufe kein Werkzeug auf. Beende deine Antwort jetzt NUR "
    "mit genau dieser Frage als letzter Zeile: Soll die Datei rot.txt oder blau.txt heißen? "
    "Bekommst du danach die Anweisung, deinen Vorschlag zu nehmen, dann lege die Datei mit deinem "
    "Vorschlag im aktuellen Ordner mit dem Write-Werkzeug an, Inhalt OK. Den Eintrag in die "
    "Entscheidungs-Liste überspringst du in diesem Test."
)


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path) -> types.ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location("bau_321_weg", SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules["bau_321_weg"] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop("bau_321_weg", None)
    return modul


def _lauf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ticket: str | None) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    arbeit = tmp_path / "arbeit"
    repo.mkdir()
    arbeit.mkdir()
    bau = _lade_bau(monkeypatch, repo)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": bau.staffel_hooks()}, ensure_ascii=False), encoding="utf-8")
    umgebung = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    umgebung.update(
        {
            "TO_SPAWN_LOG_REPO": str(tmp_path / "log"),
            "TO_SPAWN_LOG_RUECKFALL": str(tmp_path / "log"),
            "TO_SPAWN_WAECHTER_ORDNER": str(tmp_path / "zustand"),
        }
    )
    if ticket:
        umgebung["TO_SPAWN_TICKET"] = ticket
    erg = subprocess.run(
        [
            shutil.which("claude") or "claude",
            "-p",
            "--settings",
            str(settings),
            "--model",
            MODELL,
            "--allowedTools",
            "Write",
            "Bash(python *)",
            "--output-format",
            "json",
            PROMPT,
        ],
        cwd=str(arbeit),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        check=False,
    )
    return arbeit, f"exit={erg.returncode}\nstdout={erg.stdout[-1500:]}\nstderr={erg.stderr[-800:]}"


def _dateien(arbeit: Path) -> list[str]:
    return [n for n in ("rot.txt", "blau.txt") if (arbeit / n).is_file()]


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude nicht installiert")
def test_weg_bau_session_frage_wird_beantwortet_und_baut_weiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arbeit, protokoll = _lauf(tmp_path, monkeypatch, "321")
    assert _dateien(arbeit), f"Session hat nach der Frage nicht weitergebaut:\n{protokoll}"


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude nicht installiert")
def test_weg_ohne_ticket_bleibt_frage_stehen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    arbeit, protokoll = _lauf(tmp_path, monkeypatch, None)
    assert not _dateien(arbeit), f"ohne TO_SPAWN_TICKET darf nichts weiterbauen:\n{protokoll}"
