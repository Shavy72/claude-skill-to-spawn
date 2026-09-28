"""Wächter-Takt fest 1800 s + neuer Wache-Ablauf im Prompt (Merge lokal → Server, 2026-09-24).

Prüft ``skripte/wache.py``: fester Takt ``TAKT_S`` (auch ``--takt``-Vorgabe), Prompt mit
Aufräumen (``aufraeumen.mjs``), capo ``--katalog`` und Abschluss-Paket — ohne 3600-s-Ausnahme.
Die Server-Eigenheiten (#236 ``--resume``, #237 context-mode, #257 Speicher) bleiben erhalten.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
WACHE = SKRIPTE / "wache.py"

sys.path.insert(0, str(SKILL))


@pytest.fixture(scope="module")
def wache() -> ModuleType:
    """``wache.py`` als Modul laden (ohne ``main()`` auszuführen)."""
    spec = importlib.util.spec_from_file_location("wache_takt_1800", WACHE)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_takt_fest_1800(wache: ModuleType) -> None:
    assert getattr(wache, "TAKT_S", None) == 1800


def test_prompt_neuer_ablauf(wache: ModuleType) -> None:
    prompt = wache.PROMPT
    assert "aufraeumen.mjs" in prompt
    assert "--katalog" in prompt
    assert "Abschluss" in prompt
    assert "3600" not in prompt


def test_prompt_formatiert_mit_skill(wache: ModuleType) -> None:
    text = wache.PROMPT.format(S=900, REPO="x/y", DATUM="2026-09-24", TAKT=1800, SKILL="/skill")
    assert "ScheduleWakeup 1800 s" in text
    assert "/skill/skripte/abschluss_paket.py 900" in text
    assert "capo.py 900 --katalog" in text


def test_server_eigenes_bleibt(wache: ModuleType) -> None:
    quelle = WACHE.read_text(encoding="utf-8")
    assert "--resume" in quelle
    assert "context_mode.pruefen" in quelle
    assert callable(getattr(wache, "auf_speicher_warten", None))
    assert callable(getattr(wache, "repo_slug_oder_abbruch", None))
    assert "waechter_lauf.fahre" in quelle
    assert "vertrauen.still_sicherstellen" in quelle


def test_takt_vorgabe_im_echten_aufruf(tmp_path: Path) -> None:
    """``wache.py 900 --dry-run --print-prompt`` ohne ``--takt`` → Wakeup 1800 s."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    umgebung = {**os.environ, "TO_SPAWN_REPO": str(tmp_path), "TO_SPAWN_GH_REPO": "x/y"}
    ergebnis = subprocess.run(
        [sys.executable, str(WACHE), "900", "--dry-run", "--print-prompt"],
        cwd=str(tmp_path),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert "ScheduleWakeup 1800 s" in ergebnis.stdout
    assert "3600" not in ergebnis.stdout
    hilfe = subprocess.run(
        [sys.executable, str(WACHE), "--help"], capture_output=True, text=True, encoding="utf-8", check=False
    ).stdout
    assert "(1800)" in hilfe


def test_wache_modell_opus(wache: ModuleType) -> None:
    """David 22.09. bestätigt 24.09.: Wächter läuft mit Opus, nicht Fable (#213-Nachtrag)."""
    assert wache.MODELL == "claude-opus-5-5"
    assert "🧭 Opus" in wache.PROMPT
    assert "Fable" not in wache.PROMPT
