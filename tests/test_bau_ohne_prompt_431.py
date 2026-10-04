"""#431: ``bau.main`` mit ``--ohne-prompt`` — Probelauf ohne ``<prompt>`` und Staffel-Lauf.

Runde 1 startet ohne Prompt-Argument (``prompt-runde1.txt`` leer), ab Runde 2 trägt der
Befehl den Staffel-Prompt. Außengrenzen (gh, Manifest, Sandbox, Speicher-Sperre, Session-Start)
sind gestellt; ``main()`` und die Staffel-Schleife laufen echt.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

SKRIPTE = Path(__file__).resolve().parent.parent / "skripte"


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path) -> types.ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location(
        "bau_431_ohne_prompt", SKRIPTE / "bau.py"
    )
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules["bau_431_ohne_prompt"] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop("bau_431_ohne_prompt", None)
    return modul


@pytest.fixture()
def bau(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    modul = _lade_bau(monkeypatch, tmp_path)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    monkeypatch.setattr(modul, "gh_repo_ermitteln", lambda _repo: "test/ohne-prompt")
    monkeypatch.setattr(modul.config, "sicherstellen", lambda _repo: None)
    monkeypatch.setattr(
        modul,
        "load_default",
        lambda *_a, **_k: {"prompt_template": "TEMPLATE {ticket}"},
    )
    monkeypatch.setattr(
        modul, "find_manifest", lambda _t: ({"spec": "9"}, {"title": "Titel"})
    )
    monkeypatch.setattr(modul, "build_prompt", lambda *_a, **_k: "GRUND-PROMPT")
    monkeypatch.setattr(modul, "known_skill_names", lambda: set())
    monkeypatch.setattr(modul, "mcp_catalog", dict)
    monkeypatch.setattr(modul.context_mode, "server_definition", lambda _d: {})
    monkeypatch.setattr(modul.nest, "sandbox_start", lambda *_a, **_k: [])
    monkeypatch.setattr(modul, "auf_speicher_warten", lambda *_a, **_k: None)
    monkeypatch.setattr(modul.vertrauen, "still_sicherstellen", lambda *_a, **_k: None)
    monkeypatch.setattr(
        modul, "umzug_anfragen_aufraeumen", lambda _t: tmp_path / "anfrage.json"
    )
    return modul


def test_dry_run_ohne_prompt_laeuft_ohne_prompt_argument(
    bau: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["bau.py", "431", "--dry-run", "--ohne-prompt"])
    assert bau.main() == 0
    ausgabe = capsys.readouterr().out
    assert "Befehl:" in ausgabe
    assert "<prompt>" not in ausgabe
    assert "GRUND-PROMPT" not in ausgabe


def test_dry_run_mit_prompt_zeigt_platzhalter(
    bau: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Gegenstück: ohne ``--ohne-prompt`` bleibt der Platzhalter."""
    monkeypatch.setattr(sys, "argv", ["bau.py", "431", "--dry-run"])
    assert bau.main() == 0
    assert "<prompt>" in capsys.readouterr().out


def test_staffel_ohne_prompt_runde1_leer_ab_runde2_mit_prompt(
    bau: types.ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handoff = tmp_path / "handoff.md"
    handoff.write_text("HANDOFF-INHALT", encoding="utf-8")
    befehle: list[list[str]] = []
    prompt_dateien: dict[int, str] = {}

    monkeypatch.setattr(bau, "session_id_setzen", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "bau_log_umgebung", lambda *_a, **_k: {})
    monkeypatch.setattr(bau, "staffel_ziel", lambda uebergabe: handoff)
    monkeypatch.setattr(
        bau,
        "staffel_prompt",
        lambda prompt, h, runde: f"STAFFEL-RUNDE-{runde}:{prompt}",
    )

    def fake_uebergabe(staffel_datei: Path) -> dict | None:
        # Nach Runde 1 eine Übergabe, nach Runde 2 keine mehr.
        return {"handoff": str(handoff)} if len(befehle) == 1 else None

    monkeypatch.setattr(bau, "staffel_uebergabe", fake_uebergabe)

    def fake_start(cmd: list[str], umzug_datei: Path) -> tuple[int, dict | None]:
        befehle.append(list(cmd))
        for datei in umzug_datei.parent.glob("prompt-runde*.txt"):
            prompt_dateien[int(datei.stem.removeprefix("prompt-runde"))] = (
                datei.read_text(encoding="utf-8")
            )
        return 0, None

    monkeypatch.setattr(bau, "starte_session", fake_start)
    monkeypatch.setattr(sys, "argv", ["bau.py", "431", "--sofort", "--ohne-prompt"])

    assert bau.main() == 0
    assert len(befehle) == 2
    # Runde 1: kein Prompt als letztes Argument, Datei leer.
    assert not any("GRUND-PROMPT" in teil for teil in befehle[0])
    assert prompt_dateien[1] == ""
    # Runde 2: Staffel-Prompt als letztes Argument.
    assert befehle[1][-1] == "STAFFEL-RUNDE-2:GRUND-PROMPT"
    assert prompt_dateien[2] == "STAFFEL-RUNDE-2:GRUND-PROMPT"
    assert json.dumps(befehle[1])  # serialisierbar, keine Objekte im Befehl
