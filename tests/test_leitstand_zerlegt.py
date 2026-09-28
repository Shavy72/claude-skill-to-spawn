"""Bau-Leitstand zerlegt: ``leitstand.py`` (Rechnen + CLI) und ``leitstand_fenster.py`` je unter 1000 Zeilen."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SKRIPTE = SKILL / "skripte"


def test_beide_dateien_unter_1000_zeilen() -> None:
    for name in ("leitstand.py", "leitstand_fenster.py"):
        zeilen = len((SKRIPTE / name).read_text(encoding="utf-8").splitlines())
        assert zeilen < 1000, f"{name}: {zeilen} Zeilen"


def test_fenster_modul_importierbar_und_skript_pfad_zeigt_auf_cli() -> None:
    for pfad in (str(SKILL), str(SKRIPTE)):
        if pfad not in sys.path:
            sys.path.append(pfad)
    fenster = importlib.import_module("leitstand_fenster")
    assert fenster.skript_pfad() == (SKRIPTE / "leitstand.py").resolve().as_posix()
