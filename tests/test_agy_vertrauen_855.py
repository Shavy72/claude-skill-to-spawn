"""#855: agy-Vertrauensdialog vorab bestätigen (``trustedWorkspaces``) & FUND 4 im Aufseher-Prompt.

Prüft:
- ``to_spawn.vertrauen.sicherstellen(..., runner="agy")`` trägt Ordner atomar und idempotent
  in ``~/.gemini/antigravity-cli/settings.json`` unter ``trustedWorkspaces`` ein.
- ``skripte/bau.py`` bestätigt bei ``runner == "agy"`` Worktree und Repo vor dem Start
  der Session (nicht bei ``--dry-run``) und baut den erwarteten ``agy``-Startbefehl.
- ``test_heim`` sperrt Schreibzugriffe auf die echte ``~/.gemini/antigravity-cli/settings.json``.
- Aufseher-Prompt legt Nachbesserungen eindeutig auf ``abschluss_paket.py nachbesserung`` fest (FUND 4).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

import conftest
from to_spawn import anleitung, vertrauen, waechter_takt, werkzeug

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path) -> types.ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location("bau_855_test", SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules["bau_855_test"] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop("bau_855_test", None)
    return modul


def test_agy_vertrauen_traegt_ordner_ein_bewahrt_fremde_schluessel_und_ist_idempotent(tmp_path: Path) -> None:
    settings_json = tmp_path / ".gemini" / "antigravity-cli" / "settings.json"
    settings_json.parent.mkdir(parents=True, exist_ok=True)
    settings_json.write_text(
        json.dumps(
            {
                "allowNonWorkspaceAccess": True,
                "toolPermission": "always-proceed",
                "trustedWorkspaces": ["/bereits/vertraut"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    wt = tmp_path / "wt-855"
    repo = tmp_path / "repo"
    wt.mkdir()
    repo.mkdir()

    geaendert = vertrauen.sicherstellen(wt, repo, runner="agy", settings_json=settings_json)
    assert geaendert == [str(wt.resolve()), str(repo.resolve())]

    daten = json.loads(settings_json.read_text(encoding="utf-8"))
    assert daten["allowNonWorkspaceAccess"] is True
    assert daten["toolPermission"] == "always-proceed"
    assert daten["trustedWorkspaces"] == [
        "/bereits/vertraut",
        str(wt.resolve()),
        str(repo.resolve()),
    ]

    vorher = settings_json.read_bytes()
    assert vertrauen.sicherstellen(wt, repo, runner="agy", settings_json=settings_json) == []
    assert settings_json.read_bytes() == vorher, "zweiter Aufruf darf settings.json nicht ändern"


def test_agy_vertrauen_legt_fehlende_settings_datei_an(tmp_path: Path) -> None:
    settings_json = tmp_path / "neu" / "antigravity-cli" / "settings.json"
    wt = tmp_path / "wt-neu"
    wt.mkdir()

    geaendert = vertrauen.sicherstellen(wt, runner="agy", settings_json=settings_json)
    assert geaendert == [str(wt.resolve())]
    assert settings_json.is_file()
    daten = json.loads(settings_json.read_text(encoding="utf-8"))
    assert daten == {"trustedWorkspaces": [str(wt.resolve())]}
    assert vertrauen.sperrdatei_pfad(settings_json).exists()


def test_agy_vertrauen_nutzt_test_heim_und_sperrt_echtes_heim(test_heim: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt-heim"
    wt.mkdir()

    vertrauen.sicherstellen(wt, runner="agy")
    test_settings = test_heim / werkzeug.AGY_DATEN_REL / "settings.json"
    assert test_settings.is_file()
    daten = json.loads(test_settings.read_text(encoding="utf-8"))
    assert str(wt.resolve()) in daten["trustedWorkspaces"]

    echte_settings = (conftest._ECHTES_HEIM / werkzeug.AGY_DATEN_REL / "settings.json").resolve()
    with pytest.raises(PermissionError, match="#232"):
        vertrauen.sicherstellen(wt, runner="agy", settings_json=echte_settings)


def test_bau_agy_traegt_vertrauen_vor_session_start_ein_und_baut_startbefehl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_heim: Path
) -> None:
    orig_env = dict(os.environ)
    try:
        repo = tmp_path / "repo"
        (repo / ".to-spawn").mkdir(parents=True)
        (repo / ".to-spawn" / "config.json").write_text(json.dumps({"runner": "agy"}), encoding="utf-8")
        wt = tmp_path / "wt-855"
        wt.mkdir()

        bau = _lade_bau(monkeypatch, repo)
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.delenv("BAU_AUFTRAG", raising=False)
        monkeypatch.setattr(bau, "gh_repo_ermitteln", lambda _repo: "test/agy-vertrauen")
        monkeypatch.setattr(bau.config, "sicherstellen", lambda _repo: None)
        monkeypatch.setattr(bau, "load_default", lambda *_a, **_k: {"prompt_template": "TEMPLATE"})
        monkeypatch.setattr(bau, "find_manifest", lambda _t: ({"spec": "12"}, {"title": "Ticket 855"}))
        monkeypatch.setattr(bau, "build_prompt", lambda *_a, **_k: "GRUND-PROMPT")
        monkeypatch.setattr(bau, "known_skill_names", lambda: set())
        monkeypatch.setattr(bau, "mcp_catalog", dict)
        monkeypatch.setattr(bau.nest, "sandbox_start", lambda *_a, **_k: [])
        monkeypatch.setattr(bau.nest, "git_hooks_einrichten", lambda _r: None)
        monkeypatch.setattr(bau.starter, "finde", lambda name: f"/bin/{name}")
        monkeypatch.setattr(bau, "worktree_pfad", lambda _t: str(wt))
        monkeypatch.setattr(bau, "blocker_schranke", lambda *_a, **_k: None)
        monkeypatch.setattr(bau, "auf_speicher_warten", lambda *_a, **_k: None)
        monkeypatch.setattr(bau.terminal_maus, "maus_ruhig", lambda: None)
        monkeypatch.setattr(bau, "umzug_anfragen_aufraeumen", lambda _t: tmp_path / "umzug-anfrage.json")
        monkeypatch.setattr(
            bau.startstelle,
            "betreten",
            lambda *_a, **_k: bau.startstelle.Entscheidung(True, 0, "", "", ""),
        )
        monkeypatch.setattr(
            bau.startstelle,
            "weiter_entscheidung",
            lambda _t, _r: bau.startstelle.Entscheidung(True, 0, "", "", ""),
        )
        monkeypatch.setattr(bau.werkzeug, "agy_bau_log_nachtragen", lambda *_a, **_k: None)

        settings_datei = test_heim / werkzeug.AGY_DATEN_REL / "settings.json"

        # --dry-run darf keine Vertrauens-Datei schreiben.
        monkeypatch.setattr(sys, "argv", ["bau.py", "855", "--dry-run"])
        assert bau.main() == 0
        assert not settings_datei.exists()

        # Echter Start: Vertrauen muss VOR starte_session eingetragen sein.
        gesehen_cmd: list[str] = []
        vertraut_beim_start: list[str] = []

        def _starte_session(cmd: list[str], _umzug: Path) -> tuple[int, None]:
            gesehen_cmd.extend(cmd)
            if settings_datei.is_file():
                daten = json.loads(settings_datei.read_text(encoding="utf-8"))
                vertraut_beim_start.extend(daten.get("trustedWorkspaces") or [])
            return 0, None

        monkeypatch.setattr(bau, "starte_session", _starte_session)
        monkeypatch.setattr(sys, "argv", ["bau.py", "855"])
        assert bau.main() == 0

        assert str(wt.resolve()) in vertraut_beim_start
        assert str(repo.resolve()) in vertraut_beim_start
        assert gesehen_cmd[0] == "/bin/agy"
        assert "-i" in gesehen_cmd
        assert "--model" in gesehen_cmd
        assert "--dangerously-skip-permissions" in gesehen_cmd
    finally:
        os.environ.clear()
        os.environ.update(orig_env)


def test_aufseher_prompt_legt_nachbesserung_auf_abschluss_paket_fest_855() -> None:
    """FUND 4 (#855): Aufseher-Prompt schließt ``befund --stufe gelb`` für Nachbesserungen aus."""
    for text in (anleitung.ABSCHLUSS_ANWEISUNG, waechter_takt.PROMPT):
        assert "abschluss_paket.py nachbesserung {S}" in text
        assert "befund --stufe gelb" in text
