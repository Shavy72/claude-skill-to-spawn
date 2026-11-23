"""#281: der Start-Text entsteht im Moment des Loslassens, nicht beim Start von ``bau.py``.

Beobachtet: ``bau.py`` las die Vorlage (``_default.json`` → ``prompt_template``) beim eigenen
Start und reichte nach stundenlanger Blocker-Wartezeit den alten Text weiter. Hier läuft
``main()`` echt samt ``load_default``, ``build_prompt``, ``blocker_schranke`` und
Warteschleife; gestellt sind nur die Blocker-Abfrage (``blocker_offen``, GitHub), das
Schlafen und der Session-Start. Während des Wartens wird die Vorlage auf der Platte geändert.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

SKRIPTE = Path(__file__).resolve().parent.parent / "skripte"

ALT = "ALTE REGEL: nur geschlossene Blocker zählen als erledigt. Ticket #{N}."
NEU = "NEUE REGEL: geschlossen ODER staging:fertig. Ticket #{N}."


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path, name: str) -> types.ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location(name, SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(name, None)
    return modul


def _vorlage_schreiben(bau: types.ModuleType, text: str) -> Path:
    pfad = Path(bau.MANIFEST_DIR) / "_default.json"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps({"prompt_template": text}, ensure_ascii=False), encoding="utf-8")
    return pfad


def _wartender_lauf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ticket: str, name: str
) -> tuple[types.ModuleType, list[list[str]], list[str]]:
    """``bau.py <ticket>`` mit offenem Blocker; beim Schlafen kommt die neue Vorlage."""
    repo = tmp_path / f"repo-{ticket}"
    repo.mkdir()
    bau = _lade_bau(monkeypatch, repo, name)
    _vorlage_schreiben(bau, ALT)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    monkeypatch.setattr(bau, "gh_repo_ermitteln", lambda _repo: "test-org/test-repo")
    monkeypatch.setattr(bau.config, "sicherstellen", lambda _repo: None)
    monkeypatch.setattr(bau.gh, "hauptzweig", lambda _repo=None: "main")
    monkeypatch.setattr(bau, "find_manifest", lambda _t: ({"spec": "9"}, {"title": "Titel"}))
    monkeypatch.setattr(bau, "known_skill_names", lambda: set())
    monkeypatch.setattr(bau, "mcp_catalog", dict)
    monkeypatch.setattr(bau.context_mode, "server_definition", lambda _d: {})
    monkeypatch.setattr(bau.nest, "sandbox_start", lambda *_a, **_k: [])
    monkeypatch.setattr(bau.nest, "git_hooks_einrichten", lambda _r: None)
    monkeypatch.setattr(bau, "auf_speicher_warten", lambda *_a, **_k: None)
    monkeypatch.setattr(bau.speicher, "auf_platte_warten", lambda **_k: None)
    monkeypatch.setattr(bau.vertrauen, "still_sicherstellen", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "session_id_setzen", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "bau_log_umgebung", lambda *_a, **_k: {})
    monkeypatch.setattr(bau, "staffel_uebergabe", lambda _d: None)
    monkeypatch.setattr(bau, "umzug_anfragen_aufraeumen", lambda _t: tmp_path / f"anfrage-{ticket}.json")
    monkeypatch.setattr(bau.terminal_maus, "maus_ruhig", lambda: None)

    # Blocker-Abfrage (GitHub) gestellt: offen, bis einmal geschlafen wurde.
    geschlafen: list[int] = []
    monkeypatch.setattr(bau, "blocker_offen", lambda _t, nur_offene=False: [] if geschlafen else ["#1 offen"])

    def schlafen(takt: int) -> None:
        # In der Wartezeit landet die neue Regel auf dem Hauptzweig.
        _vorlage_schreiben(bau, NEU)
        geschlafen.append(takt)

    echtes_warten = bau.auf_blocker_warten
    monkeypatch.setattr(
        bau,
        "auf_blocker_warten",
        lambda t, takt, **k: echtes_warten(t, takt, schlafen=schlafen, **k),
    )

    befehle: list[list[str]] = []
    prompt_dateien: list[str] = []

    def starte_session(cmd: list[str], umzug_datei: Path) -> tuple[int, None]:
        befehle.append(list(cmd))
        prompt_dateien.append((umzug_datei.parent / "prompt.txt").read_text(encoding="utf-8"))
        return 0, None

    monkeypatch.setattr(bau, "starte_session", starte_session)
    monkeypatch.setattr(sys, "argv", ["bau.py", ticket])
    assert bau.main() == 0
    assert geschlafen, "bau.py hat nicht auf den Blocker gewartet"
    return bau, befehle, prompt_dateien


def test_start_text_traegt_vorlage_vom_loslassen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bau, befehle, prompt_dateien = _wartender_lauf(tmp_path, monkeypatch, "1480", "bau_281_eins")
    assert len(befehle) == 1
    start_text = befehle[0][-1]
    assert "NEUE REGEL" in start_text and "#1480" in start_text
    assert "ALTE REGEL" not in start_text
    assert prompt_dateien == [start_text]


def test_drei_wartende_tickets_bekommen_neue_regel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nachgestellte Probe (Abnahme 3): drei wartende Fenster, Regel ändert sich, 0 Neustarts."""
    for ticket in ("1480", "1481", "1482"):
        _bau, befehle, _ = _wartender_lauf(tmp_path, monkeypatch, ticket, f"bau_281_{ticket}")
        assert len(befehle) == 1, f"#{ticket}: {len(befehle)} Starts statt genau einem"
        assert "NEUE REGEL" in befehle[0][-1] and f"#{ticket}" in befehle[0][-1]
        assert "ALTE REGEL" not in befehle[0][-1]
