"""Frage-Sperre (#321): Bau-Sessions fragen David nie — Hooks und Rechte in ``bau.py`` und im Bau-Server-Template."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import types
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
#: DuoPlus-Repo mit ``scripts/setup_bau_server_push.sh``: ``TO_SPAWN_REPO``, sonst der Hauptbaum auf dem PC.
#: Beim Import gelesen – die autouse-Fixture in conftest.py löscht ``TO_SPAWN_*`` erst je Test.
_REPO_ENV = os.environ.get("TO_SPAWN_REPO")
DUOPLUS_REPO = Path(_REPO_ENV or "C:/Users/d4veg/Desktop/DuoPlus/duoplus-management")
SETUP_SKRIPT = DUOPLUS_REPO / "scripts" / "setup_bau_server_push.sh"
#: Hook-Datei, wie ``bau.FRAGE_SPERRE_BEFEHL`` sie aufruft (``~/.claude/hooks/…``) — am
#: Home-Ordner, nicht relativ zum Skill-Ordner (Klon unter /home/bau/t402 o. ä. bricht sonst).
CLAUDE_HEIM = Path.home() / ".claude"
HOOK_DATEI = CLAUDE_HEIM / "hooks/smart-zone/staffel/frage-sperre.mjs"


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path) -> types.ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location("bau_321", SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules["bau_321"] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop("bau_321", None)
    return modul


@pytest.fixture()
def bau(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    return _lade_bau(monkeypatch, tmp_path)


def test_frage_sperre_erster_stop_hook(bau: types.ModuleType) -> None:
    stop = bau.staffel_hooks()["Stop"]
    befehle = [h["command"] for block in stop for h in block["hooks"]]
    assert "frage-sperre.mjs" in befehle[0]
    assert befehle[0].startswith("node")
    assert any("staffel_stop.py" in b for b in befehle[1:])


def test_frage_sperre_pretooluse_askuserquestion(bau: types.ModuleType) -> None:
    pre = bau.staffel_hooks()["PreToolUse"]
    treffer = [b for b in pre if b["matcher"] == "AskUserQuestion"]
    assert len(treffer) == 1
    assert "frage-sperre.mjs" in treffer[0]["hooks"][0]["command"]


def test_frage_sperre_hook_datei_existiert() -> None:
    if not CLAUDE_HEIM.is_dir():
        pytest.skip(f"keine Claude-Installation ({CLAUDE_HEIM} fehlt) — Hook gehört nicht zum Repo")
    assert HOOK_DATEI.is_file(), f"{HOOK_DATEI} fehlt"


def test_session_settings_verbietet_askuserquestion(bau: types.ModuleType) -> None:
    settings = bau.session_settings({"x": "off"})
    assert "AskUserQuestion" in settings["permissions"]["deny"]
    assert settings["hooks"] == bau.staffel_hooks()
    assert settings["skillOverrides"] == {"x": "off"}
    json.dumps(settings)


def _template() -> str:
    if not SETUP_SKRIPT.is_file():
        pytest.skip(f"Setup-Skript {SETUP_SKRIPT} fehlt (TO_SPAWN_REPO auf das duoplus-management-Repo setzen)")
    text = SETUP_SKRIPT.read_text(encoding="utf-8")
    treffer = re.search(r"cat > \"\$TMP/settings\.json\" <<'EOF'\n(.*?)\nEOF\n", text, re.S)
    assert treffer, "settings.json-Heredoc nicht gefunden"
    return treffer.group(1)


def test_setup_template_gueltig_mit_frage_sperre() -> None:
    settings = json.loads(_template())
    pre = [b for b in settings["hooks"]["PreToolUse"] if b["matcher"] == "AskUserQuestion"]
    assert len(pre) == 1
    assert "frage-sperre.mjs" in pre[0]["hooks"][0]["command"]
    assert "frage-sperre.mjs" in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "staffel-stop.mjs" in settings["hooks"]["Stop"][1]["hooks"][0]["command"]
    # Kein globales deny: gesteuert wird allein über TO_SPAWN_TICKET im Hook.
    assert "AskUserQuestion" not in json.dumps(settings.get("permissions", {}))


def test_setup_skript_standard_nicht_auf_wegwerf_worktree() -> None:
    """Fixrunde #321: Standard zeigt auf ``TO_SPAWN_REPO`` bzw. den DuoPlus-Hauptbaum, nie auf ``C:/dev/wt-*``."""
    erwartet = Path(_REPO_ENV) if _REPO_ENV else Path("C:/Users/d4veg/Desktop/DuoPlus/duoplus-management")
    assert SETUP_SKRIPT == erwartet / "scripts" / "setup_bau_server_push.sh"


#: Befehl wie im Server-Template: identische Strings legt Claude Code zusammen (kein Doppellauf).
FRAGE_SPERRE_BEFEHL = "node ~/.claude/hooks/smart-zone/staffel/frage-sperre.mjs"


def test_frage_sperre_befehl_gleich_wie_server_template(bau: types.ModuleType) -> None:
    hooks = bau.staffel_hooks()
    assert hooks["Stop"][0]["hooks"][0]["command"] == FRAGE_SPERRE_BEFEHL
    pre = [b for b in hooks["PreToolUse"] if b["matcher"] == "AskUserQuestion"]
    assert pre[0]["hooks"][0]["command"] == FRAGE_SPERRE_BEFEHL
    if SETUP_SKRIPT.is_file() and "frage-sperre.mjs" in SETUP_SKRIPT.read_text(encoding="utf-8"):
        assert json.loads(_template())["hooks"]["Stop"][0]["hooks"][0]["command"] == FRAGE_SPERRE_BEFEHL
