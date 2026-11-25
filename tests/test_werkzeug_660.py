"""#660: Bau-Session wahlweise über ``claude`` oder ``agy`` (Gemini 4 Neon) starten.

Prüft das Modul ``to_spawn.werkzeug`` (Konfig-Schalter ``runner``, Modell-Auflösung,
Startbefehl für beide Werkzeuge, Lauf-/Still-Erkennung) sowie die Einbindung in
``skripte/bau.py``, ``skripte/sessions_stand.py``, ``to_spawn/speicher.py`` und
``to_spawn/aufpasser.py``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

from to_spawn import aufpasser, config, context_mode, speicher, werkzeug

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"


def _lade_skript(name: str, pfad: Path, monkeypatch: pytest.MonkeyPatch, repo: Path | None = None) -> types.ModuleType:
    if repo is not None:
        monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location(name, pfad)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(name, None)
    return modul


# --- Konfig-Schalter & Modell-Auflösung -------------------------------------


def test_runner_von_standard_und_agy(caplog: pytest.LogCaptureFixture) -> None:
    assert werkzeug.runner_von(None) == "claude"
    assert werkzeug.runner_von({}) == "claude"
    assert werkzeug.runner_von(config.DEFAULTS) == "claude"
    assert werkzeug.runner_von({"runner": "claude"}) == "claude"
    assert werkzeug.runner_von({"runner": "agy"}) == "agy"
    assert werkzeug.runner_von({"runner": " AGY "}) == "agy"
    with caplog.at_level("WARNING"):
        assert werkzeug.runner_von({"runner": "unbekannt"}) == "claude"
    assert "unbekannt" in caplog.text


def test_modell_fuer_beide_werkzeuge() -> None:
    konfig_std = dict(config.DEFAULTS)
    assert werkzeug.modell_fuer("claude", konfig_std) == config.DEFAULTS["modelle"]["ticket"]
    assert werkzeug.modell_fuer("claude", konfig_std, cli_modell="claude-sonnet-5") == "claude-sonnet-5"
    # Ein Schalter ``runner: agy`` reicht: geerbtes Claude-Standardmodell fällt auf Gemini 4 Neon.
    konfig_agy = {**config.DEFAULTS, "runner": "agy"}
    assert werkzeug.modell_fuer("agy", konfig_agy) == "gemini-4-neon-high"
    # Eigenes Nicht-Claude-Modell in der Konfig wird übernommen.
    konfig_agy_eigen = {**config.DEFAULTS, "runner": "agy", "modelle": {"ticket": "gemini-4-neon-medium"}}
    assert werkzeug.modell_fuer("agy", konfig_agy_eigen) == "gemini-4-neon-medium"
    # Ausdrückliches --model gewinnt immer.
    assert werkzeug.modell_fuer("agy", konfig_agy, cli_modell="gemini-4-neon-xhigh") == "gemini-4-neon-xhigh"


# --- Startbefehl für beide Werkzeuge ----------------------------------------


def test_startbefehl_claude_interaktiv_und_probesitz() -> None:
    cmd = werkzeug.startbefehl(
        "claude",
        programm="/opt/bin/claude",
        modell="claude-opus-5-5",
        prompt="Baue Ticket #660",
        interaktiv=True,
        effort="medium",
        settings_pfad="/tmp/s.json",
        mcp_pfad="/tmp/m.json",
        chrome=False,
        session_id="sid-123",
    )
    assert cmd == [
        "/opt/bin/claude",
        "--settings",
        "/tmp/s.json",
        "--mcp-config",
        "/tmp/m.json",
        "--strict-mcp-config",
        "--no-chrome",
        "--model",
        "claude-opus-5-5",
        "--effort",
        "medium",
        "--session-id",
        "sid-123",
        "Baue Ticket #660",
    ]

    probe = werkzeug.startbefehl(
        "claude",
        programm="/opt/bin/claude",
        modell="claude-sonnet-5",
        prompt="Mini-Auftrag",
        interaktiv=False,
        probesitz=True,
        probesitz_rechte=("Write", "Read"),
        effort="low",
        session_id="sid-probe",
    )
    assert probe[:5] == ["/opt/bin/claude", "-p", "--allowedTools", "Write", "Read"]
    assert probe[-1] == "Mini-Auftrag"


def test_startbefehl_agy_interaktiv_print_und_ohne_prompt() -> None:
    interaktiv = werkzeug.startbefehl(
        "agy",
        programm="/opt/bin/agy",
        modell="gemini-4-neon-high",
        prompt="Baue Ticket #660",
        interaktiv=True,
        effort="medium",
        settings_pfad="/tmp/s.json",
        mcp_pfad="/tmp/m.json",
        chrome=False,
        session_id="sid-123",
    )
    assert interaktiv == [
        "/opt/bin/agy",
        "-i",
        "Baue Ticket #660",
        "--model",
        "gemini-4-neon-high",
        "--dangerously-skip-permissions",
    ]

    druck = werkzeug.startbefehl(
        "agy",
        programm="/opt/bin/agy",
        modell="gemini-4-neon-high",
        prompt="Mini-Auftrag",
        interaktiv=False,
        probesitz=True,
    )
    assert druck == [
        "/opt/bin/agy",
        "-p",
        "Mini-Auftrag",
        "--model",
        "gemini-4-neon-high",
        "--dangerously-skip-permissions",
        "--output-format",
        "json",
    ]

    ohne = werkzeug.startbefehl(
        "agy",
        programm="/opt/bin/agy",
        modell="gemini-4-neon-high",
        prompt=None,
        interaktiv=True,
    )
    assert ohne == [
        "/opt/bin/agy",
        "--model",
        "gemini-4-neon-high",
        "--dangerously-skip-permissions",
    ]

    fortsetzen = werkzeug.startbefehl(
        "agy",
        programm="/opt/bin/agy",
        modell="gemini-4-neon-high",
        prompt="Weiter",
        interaktiv=True,
        resume="conv-42",
    )
    assert "--resume" in fortsetzen and "conv-42" in fortsetzen
    assert "--session-id" not in fortsetzen


def test_startbefehl_unbekanntes_werkzeug_lehnt_ab() -> None:
    with pytest.raises(ValueError, match="Unbekanntes Werkzeug"):
        werkzeug.startbefehl("fremd", programm="/opt/bin/fremd")


def test_prompt_setzen_fuer_claude_und_agy() -> None:
    cmd_claude = ["claude", "--model", "claude-opus-5-5", "Alt"]
    werkzeug.prompt_setzen(cmd_claude, "claude", "Neu", hat_prompt=True)
    assert cmd_claude[-1] == "Neu"

    cmd_claude_ohne = ["claude", "--model", "claude-opus-5-5"]
    werkzeug.prompt_setzen(cmd_claude_ohne, "claude", "Neu", hat_prompt=False)
    assert cmd_claude_ohne[-1] == "Neu"

    cmd_agy = ["agy", "-i", "Alt", "--model", "gemini-4-neon-high", "--dangerously-skip-permissions"]
    werkzeug.prompt_setzen(cmd_agy, "agy", "Neu", hat_prompt=True)
    assert cmd_agy == ["agy", "-i", "Neu", "--model", "gemini-4-neon-high", "--dangerously-skip-permissions"]

    cmd_agy_ohne = ["agy", "--model", "gemini-4-neon-high", "--dangerously-skip-permissions"]
    werkzeug.prompt_setzen(cmd_agy_ohne, "agy", "Neu", hat_prompt=False)
    assert cmd_agy_ohne == ["agy", "-i", "Neu", "--model", "gemini-4-neon-high", "--dangerously-skip-permissions"]


# --- Prozess- und Zustands-Erkennung ----------------------------------------


def test_session_programm_und_zeile_erkennen_beide_werkzeuge() -> None:
    assert werkzeug.ist_session_programm("/usr/local/bin/claude")
    assert werkzeug.ist_session_programm("/usr/local/bin/agy")
    assert werkzeug.ist_session_programm(r"C:\Tools\agy.exe", windows=True)
    assert werkzeug.ist_session_programm(r"C:\Tools\claude.cmd", windows=True)
    assert not werkzeug.ist_session_programm("/bin/bash")
    assert not werkzeug.ist_session_programm("python3")

    assert werkzeug.ist_session_zeile("/usr/local/bin/agy -i hallo --model gemini-4-neon-high")
    assert werkzeug.ist_session_zeile("claude --model claude-opus-5-5")
    assert not werkzeug.ist_session_zeile("python3 skripte/bau.py 660")
    assert not werkzeug.ist_session_zeile("/bin/sh -c 'agy --help'")


def test_session_zustand_arbeitet_rueckfrage_still_aus() -> None:
    assert werkzeug.session_zustand("alles ruhig", laeuft=False) == "aus"
    assert werkzeug.session_zustand("alles ruhig", status="busy", laeuft=True) == "arbeitet"
    assert werkzeug.session_zustand("✻ Denkt nach…\nesc to interrupt", laeuft=True) == "arbeitet"
    assert werkzeug.session_zustand("Do you want to proceed? (y/n)", laeuft=True) == "rueckfrage"
    assert werkzeug.session_zustand("Bereit für Eingabe", status="idle", laeuft=True) == "still"


# --- Einbindung in bau.py --dry-run -----------------------------------------


def _bau_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, konfig_daten: dict | None = None) -> types.ModuleType:
    if konfig_daten is not None:
        konfig_datei = tmp_path / ".to-spawn" / "config.json"
        konfig_datei.parent.mkdir(parents=True, exist_ok=True)
        konfig_datei.write_text(json.dumps(konfig_daten), encoding="utf-8")
    modul = _lade_skript("bau_660_test", SKRIPTE / "bau.py", monkeypatch, repo=tmp_path)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    monkeypatch.setattr(modul, "gh_repo_ermitteln", lambda _repo: "test/agy-start")
    monkeypatch.setattr(modul.config, "sicherstellen", lambda _repo: None)
    monkeypatch.setattr(modul, "load_default", lambda *_a, **_k: {"prompt_template": "TEMPLATE"})
    monkeypatch.setattr(modul, "find_manifest", lambda _t: ({"spec": "12"}, {"title": "Ticket 660"}))
    monkeypatch.setattr(modul, "build_prompt", lambda *_a, **_k: "GRUND-PROMPT")
    monkeypatch.setattr(modul, "known_skill_names", lambda: set())
    monkeypatch.setattr(modul, "mcp_catalog", dict)
    monkeypatch.setattr(modul.nest, "sandbox_start", lambda *_a, **_k: [])
    return modul


def test_bau_dry_run_standard_bleibt_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bau = _bau_fixture(tmp_path, monkeypatch)
    gesucht: list[str] = []
    monkeypatch.setattr(bau.starter, "finde", lambda name: (gesucht.append(name), f"/bin/{name}")[1])
    monkeypatch.setattr(bau.context_mode, "server_definition", lambda _d: {})
    monkeypatch.setattr(sys, "argv", ["bau.py", "660", "--dry-run"])
    assert bau.main() == 0
    ausgabe = capsys.readouterr().out
    assert gesucht == ["claude"]
    assert "/bin/claude" in ausgabe
    assert "--settings" in ausgabe
    assert "--model claude-opus-5-5" in ausgabe
    assert '"<prompt>"' in ausgabe


def test_bau_dry_run_runner_agy_interaktiv_und_ohne_context_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bau = _bau_fixture(tmp_path, monkeypatch, {"runner": "agy"})
    gesucht: list[str] = []
    monkeypatch.setattr(bau.starter, "finde", lambda name: (gesucht.append(name), f"/bin/{name}")[1])

    def _fehlendes_plugin(_dir: Path) -> dict:
        raise context_mode.ContextModeFehlt("Plugin fehlt")

    # agy braucht kein Claude-Code-Plugin context-mode.
    monkeypatch.setattr(bau.context_mode, "server_definition", _fehlendes_plugin)
    monkeypatch.setattr(sys, "argv", ["bau.py", "660", "--dry-run"])
    assert bau.main() == 0
    ausgabe = capsys.readouterr().out
    assert gesucht == ["agy"]
    assert "/bin/agy" in ausgabe
    assert '-i "<prompt>"' in ausgabe
    assert "--model gemini-4-neon-high" in ausgabe
    assert "--dangerously-skip-permissions" in ausgabe
    assert "--settings" not in ausgabe
    assert "--mcp-config" not in ausgabe
    assert "--session-id" not in ausgabe


def test_bau_dry_run_runner_agy_probesitz_und_ohne_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bau = _bau_fixture(tmp_path, monkeypatch, {"runner": "agy"})
    monkeypatch.setattr(sys, "argv", ["bau.py", "660", "--dry-run", "--probesitz"])
    assert bau.main() == 0
    ausgabe_p = capsys.readouterr().out
    assert '-p "<prompt>"' in ausgabe_p
    assert "--model gemini-4-neon-high" in ausgabe_p
    assert "--dangerously-skip-permissions" in ausgabe_p
    assert "--output-format json" in ausgabe_p

    monkeypatch.setattr(sys, "argv", ["bau.py", "660", "--dry-run", "--ohne-prompt"])
    assert bau.main() == 0
    ausgabe_ohne = capsys.readouterr().out
    assert "agy" in ausgabe_ohne
    assert "-i" not in ausgabe_ohne
    assert "<prompt>" not in ausgabe_ohne


# --- Einbindung in sessions_stand, speicher, aufpasser ----------------------


def test_sessions_stand_erkennt_agy_kindprozess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stand = _lade_skript("sessions_stand_660", SKRIPTE / "sessions_stand.py", monkeypatch, repo=tmp_path)
    eintraege = {"660": stand.Eintrag("660", "ticket", "agy-Startweise")}
    jetzt = datetime(2026, 11, 25, 10, 30)
    alle = [
        stand.Prozess(100, 1, "python3", "python3 skripte/bau.py 660", jetzt, ordner=tmp_path),
        stand.Prozess(101, 100, "agy", "agy -i prompt --model gemini-4-neon-high", jetzt, ordner=tmp_path),
    ]
    stand.zuordnen(eintraege, alle)
    assert eintraege["660"].session_pid == 101
    assert eintraege["660"].zustand == "läuft seit 10:30"


def test_speicher_zaehlt_interaktive_agy_session_mit(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    (proc / "201").mkdir(parents=True)
    (proc / "201" / "cmdline").write_bytes(b"/opt/bin/agy\0-i\0prompt\0--model\0gemini-4-neon-high\0")
    (proc / "202").mkdir(parents=True)
    (proc / "202" / "cmdline").write_bytes(b"/opt/bin/agy\0-p\0einmal\0--output-format\0json\0")
    (proc / "203").mkdir(parents=True)
    (proc / "203" / "cmdline").write_bytes(b"/usr/bin/claude\0--model\0claude-opus-5-5\0")
    assert speicher.claude_sessions(proc=proc) == 2


def test_aufpasser_liest_resume_id_von_agy() -> None:
    assert aufpasser.session_id_aus_argv(["/opt/bin/agy", "--resume", "conv-77", "--model", "gemini-4-neon-high"]) == "conv-77"
    assert aufpasser.session_id_aus_argv(["agy", "--conversation=conv-88"]) == "conv-88"
