"""#660: Bau-Session wahlweise über ``claude`` oder ``agy`` (Gemini 4 Neon) starten.

Prüft das Modul ``to_spawn.werkzeug`` (Konfig-Schalter ``runner``, Modell-Auflösung,
Startbefehl für beide Werkzeuge, Lauf-/Still-Erkennung) sowie die Einbindung in
``skripte/bau.py``, ``skripte/sessions_stand.py``, ``to_spawn/speicher.py`` und
``to_spawn/aufpasser.py``.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
import time
import types
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

import pytest

from to_spawn import (
    aufpasser,
    aufseher_stand,
    bau_log,
    cli_parser,
    config,
    context_mode,
    leiter,
    probesitz,
    setup,
    speicher,
    werkzeug,
)

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
    assert (
        aufpasser.session_id_aus_argv(["/opt/bin/agy", "--resume", "conv-77", "--model", "gemini-4-neon-high"])
        == "conv-77"
    )
    assert aufpasser.session_id_aus_argv(["agy", "--conversation=conv-88"]) == "conv-88"


# --- Kontext-Auswertung für agy (#660) --------------------------------------


def _schreibe_agy_db(
    agy_ordner: Path,
    zeilen: list[tuple[str, list[str], str, str, str]],
) -> Path:
    db = agy_ordner / "conversation_summaries.db"
    agy_ordner.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    try:
        con.execute(
            "CREATE TABLE conversation_summaries ("
            "conversation_id TEXT PRIMARY KEY, "
            "workspace_uris TEXT NOT NULL, "
            "last_modified_time TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT '', "
            "parent_conversation_id TEXT NOT NULL DEFAULT '')"
        )
        for cid, uris, mod, status, parent in zeilen:
            con.execute(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?)",
                (cid, json.dumps(uris), mod, status, parent),
            )
        con.commit()
    finally:
        con.close()
    return db


def _schreibe_agy_transkript(agy_ordner: Path, cid: str, eintraege: list[object]) -> Path:
    pfad = agy_ordner / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    zeilen = [e if isinstance(e, str) else json.dumps(e) for e in eintraege]
    pfad.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return pfad


def test_agy_transkript_auswerten_kontext_und_tokens(tmp_path: Path) -> None:
    pfad = _schreibe_agy_transkript(
        tmp_path,
        "conv-1",
        [
            {"step_index": 0, "type": "USER_INPUT", "created_at": "2026-11-25T16:00:00Z"},
            "{kaputtes json",
            {
                "step_index": 1,
                "type": "PLANNER_RESPONSE",
                "model": "gemini-4-neon-high",
                "input_tokens": 9000,
                "cache_read_tokens": 0,
                "output_tokens": 200,
                "created_at": "2026-11-25T16:00:05Z",
            },
            {"step_index": 2, "type": "GENERIC", "created_at": "2026-11-25T16:00:10Z"},
            {
                "step_index": 3,
                "type": "PLANNER_RESPONSE",
                "model": "gemini-4-neon-high",
                "input_tokens": 2500,
                "cache_read_tokens": 182000,
                "output_tokens": 800,
                "created_at": "2026-11-25T16:02:00Z",
            },
        ],
    )
    erg = werkzeug.agy_transkript_auswerten(pfad)
    assert erg is not None
    assert erg["kontext"] == {"spitze": 184500, "aufrufe": 2}
    assert erg["spitze_k"] == 184.5
    assert erg["tokens"] == {
        "input": 11500,
        "cache_read": 182000,
        "cache_creation": 0,
        "output": 1000,
        "gesamt": 194500,
    }
    assert erg["modell"] == "gemini-4-neon-high"
    assert erg["dauer_s"] == 120
    assert werkzeug.agy_transkript_auswerten(tmp_path / "fehlt.jsonl") is None


def test_agy_json_auswerten_print_ausgabe() -> None:
    einzel = json.dumps(
        {
            "conversation_id": "conv-json-1",
            "model": "gemini-4-neon-high",
            "usage": {"input_tokens": 4000, "cache_read_tokens": 12000, "output_tokens": 350},
        }
    )
    erg = werkzeug.agy_json_auswerten(einzel)
    assert erg is not None
    assert erg["session_id"] == "conv-json-1"
    assert erg["kontext"] == {"spitze": 16000, "aufrufe": 1}
    assert erg["tokens"]["gesamt"] == 16350
    assert werkzeug.agy_json_auswerten("kein json") is None


def test_agy_gespraech_kontext_und_bau_log_nachtragen(tmp_path: Path) -> None:
    wt = tmp_path / "wt-660"
    wt.mkdir()
    anderer = tmp_path / "wt-999"
    anderer.mkdir()
    agy_ordner = tmp_path / "agy-home"

    _schreibe_agy_db(
        agy_ordner,
        [
            # Ältere Sitzung im selben Worktree
            ("conv-alt", [wt.resolve().as_uri()], "2026-11-25 10:00:00.123456789+00:00", "CASCADE_RUN_STATUS_IDLE", ""),
            # Subagent im selben Worktree (darf nicht als Hauptsession zählen)
            (
                "conv-sub",
                [wt.resolve().as_uri()],
                "2026-11-25 16:30:00.999999999+00:00",
                "CASCADE_RUN_STATUS_IDLE",
                "conv-neu",
            ),
            # Neueste Hauptsitzung im selben Worktree
            (
                "conv-neu",
                [wt.resolve().as_uri()],
                "2026-11-25 16:20:00.555555555+00:00",
                "CASCADE_RUN_STATUS_RUNNING",
                "",
            ),
            # Anderer Worktree
            (
                "conv-fremd",
                [anderer.resolve().as_uri()],
                "2026-11-25 16:40:00.000000000+00:00",
                "CASCADE_RUN_STATUS_RUNNING",
                "",
            ),
        ],
    )
    _schreibe_agy_transkript(
        agy_ordner,
        "conv-neu",
        [
            {
                "step_index": 1,
                "type": "PLANNER_RESPONSE",
                "model": "gemini-4-neon-high",
                "input_tokens": 3000,
                "cache_read_tokens": 97000,
                "output_tokens": 500,
                "created_at": "2026-11-25T16:15:00Z",
            }
        ],
    )
    assert werkzeug.agy_gespraech_fuer_ordner(wt, agy_ordner=agy_ordner) == "conv-neu"
    # Filter nach=16:25 blendet conv-neu (16:20) aus.
    grenze = datetime(2026, 11, 25, 16, 25, tzinfo=UTC).timestamp()
    assert werkzeug.agy_gespraech_fuer_ordner(wt, agy_ordner=agy_ordner, nach=grenze) is None
    assert werkzeug.agy_spitze_k(wt, agy_ordner=agy_ordner) == 100.0

    zeile = werkzeug.agy_bau_log_nachtragen(tmp_path, "660", wt, runde=1, agy_ordner=agy_ordner)
    assert zeile is not None
    assert zeile["typ"] == "session_ende"
    assert zeile["runner"] == "agy"
    assert zeile["kontext"] == {"spitze": 100000, "aufrufe": 1}
    assert zeile["tokens"]["gesamt"] == 100500
    gelesen = bau_log.lese(wt, "660")
    assert [z["typ"] for z in gelesen] == ["session_start", "session_ende"]
    assert bau_log.zusammenfassung(wt, "660")["spitze_k"] == 100.0


def test_agy_staffel_handoff_erkennt_frischen_handoff_mit_marker(tmp_path: Path) -> None:
    ordner = tmp_path / "docs" / "handoffs"
    ordner.mkdir(parents=True)
    seit = time.time() - 10
    ohne_marker = ordner / "HANDOFF_2026-11-25_660_alt.md"
    ohne_marker.write_text("# Handoff\nNur Notizen.\n", encoding="utf-8")
    assert werkzeug.agy_staffel_handoff([ordner], "660", seit=seit) is None

    mit_marker = ordner / "HANDOFF_2026-11-25_660.md"
    mit_marker.write_text("# Handoff #660\nStaffel: weiter\nRunde 1 fertig.\n", encoding="utf-8")
    treffer = werkzeug.agy_staffel_handoff([ordner], "660", seit=seit)
    assert treffer is not None
    pfad, fp = treffer
    assert pfad == mit_marker
    assert fp
    # Derselbe Fingerabdruck wird nicht ein zweites Mal ausgelöst.
    assert werkzeug.agy_staffel_handoff([ordner], "660", seit=seit, schon_uebergeben=fp) is None


def test_sessions_stand_und_aufseher_stand_lesen_agy_kontext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".to-spawn").mkdir()
    (tmp_path / ".to-spawn" / "config.json").write_text(json.dumps({"runner": "agy"}), encoding="utf-8")
    wt = tmp_path / "wt-660"
    wt.mkdir()
    monkeypatch.setattr(werkzeug, "agy_spitze_k", lambda pfad, **_k: 184.5 if Path(pfad) == wt else None)

    stand = _lade_skript("sessions_stand_660_kontext", SKRIPTE / "sessions_stand.py", monkeypatch, repo=tmp_path)
    monkeypatch.setattr(stand.config, "worktree_pfad", lambda _t: str(wt))
    assert stand.token_text("660", tmp_path) == "184,5k"

    monkeypatch.setattr(aufseher_stand.config, "worktree_pfad", lambda _t, _r=None: str(wt))
    lage = aufseher_stand._log_lage(660, tmp_path)
    assert lage == ("184,5k", "—", "—", 0)


def test_bau_cli_runner_flag_ueberschreibt_konfig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bau = _bau_fixture(tmp_path, monkeypatch, {"runner": "claude"})
    monkeypatch.setattr(bau.starter, "finde", lambda name: f"/bin/{name}")
    monkeypatch.setattr(sys, "argv", ["bau.py", "660", "--dry-run", "--runner", "agy"])
    assert bau.main() == 0
    ausgabe = capsys.readouterr().out
    assert "/bin/agy" in ausgabe
    assert "--model gemini-4-neon-high" in ausgabe
    assert "--dangerously-skip-permissions" in ausgabe


# --- --runner im Setup-Wizard (#660) ----------------------------------------


def test_setup_speichere_zeige_und_cli_mit_runner(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True, capture_output=True)

    # Ungültiger Runner: Exit 2, nichts geschrieben.
    kaputt = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "setup", "--plattform", "linux", "--runner", "fremd"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert kaputt.returncode == 2
    assert not (repo / ".to-spawn" / "config.json").exists()

    # Gültiger Runner + agy-Modell: schreibt runner und modell in .to-spawn/config.json.
    ok = subprocess.run(
        [
            sys.executable,
            str(SKILL / "to_spawn.py"),
            "setup",
            "--plattform",
            "linux",
            "--runner",
            "agy",
            "--modell-ticket",
            "gemini-4-neon-xhigh",
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    daten = json.loads((repo / ".to-spawn" / "config.json").read_text(encoding="utf-8"))
    assert daten["runner"] == "agy"
    assert daten["modelle"]["ticket"] == "gemini-4-neon-xhigh"
    assert "runner: agy" in ok.stdout

    # setup --zeigen zeigt Werkzeug (--runner) und den gespeicherten Wert.
    ansicht = setup.zeige(config.lade(repo), repo / config.KONFIG_PFAD, "linux", repo=repo)
    assert "--runner" in ansicht
    assert "runner: agy" in ansicht


# --- --runner / agy im Probesitz (#660) -------------------------------------


def test_probesitz_p1_login_agy_ok_fehler_und_fehlt() -> None:
    def _which_da(name: str) -> str | None:
        return f"/usr/local/bin/{name}"

    def _ok(argv: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
        assert argv[1:] == ["models"]
        out = "Fetching available models...\ngemini-4-neon-high\tGemini 4 Neon (High)\ngemini-4-neon-low\tGemini 4 Neon (Low)\n"
        return subprocess.CompletedProcess(argv, 0, out, "")

    erg_ok = probesitz.pruefe_login(laeufer=_ok, which=_which_da, runner="agy")
    assert erg_ok.ok and erg_ok.nummer == 1
    assert "agy" in erg_ok.grund and "gemini-4-neon-high" in erg_ok.grund

    def _fehler(argv: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "not authenticated")

    erg_rot = probesitz.pruefe_login(laeufer=_fehler, which=_which_da, runner="agy")
    assert not erg_rot.ok
    assert "agy models Exit 1" in erg_rot.grund
    assert "agy" in erg_rot.fehlt_noch

    erg_fehlt = probesitz.pruefe_login(laeufer=_ok, which=lambda _n: None, runner="agy")
    assert not erg_fehlt.ok
    assert erg_fehlt.grund == "agy nicht gefunden"


def test_probesitz_wegwerf_lauf_werte_bau_log_und_parser_mit_runner_agy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = cli_parser.baue_parser().parse_args(["probesitz", "--runner", "agy", "--zeigen"])
    assert args.runner == "agy"

    p5, p6 = probesitz.werte_bau_log([], runner="agy")
    assert not p5.ok and not p6.ok
    assert "agy" in p5.fehlt_noch and "agy" in p6.fehlt_noch

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True, capture_output=True)
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path / "wt"))

    gesehen_argv: list[str] = []

    def _laeufer(argv: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
        text = " ".join(argv)
        if "issue create" in text:
            return subprocess.CompletedProcess(argv, 0, "https://github.com/x/y/issues/4712\n", "")
        if "ls-remote" in text:
            return subprocess.CompletedProcess(argv, 0, "abc\trefs/heads/probesitz-4712\n", "")
        if "log " in text:
            return subprocess.CompletedProcess(argv, 0, "abc chore(probesitz): Runde 1 (#4712) [skip ci]\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def _starter(argv: list[str], **_k: object) -> probesitz.SessionLauf:
        gesehen_argv.extend(argv)
        return probesitz.SessionLauf(0, "Session PID 1", False, "")

    erg = probesitz.wegwerf_lauf(
        repo,
        config.lade(repo),
        runner="agy",
        laeufer=_laeufer,
        which=lambda name: f"/usr/bin/{name}",
        session_starter=_starter,
        worktree_anlegen=lambda wt, _r, _t: wt.mkdir(parents=True, exist_ok=True),
    )
    assert erg[2].ok, erg[2]
    assert "--runner" in gesehen_argv
    assert gesehen_argv[gesehen_argv.index("--runner") + 1] == "agy"
    assert gesehen_argv[gesehen_argv.index("--model") + 1] == "gemini-4-neon-high"


# --- Windows-Teil (#660): URIs, Kontext-Anzeigen, Bau-Log & Staffel-Handoff -


def test_agy_uri_und_db_pfad_windows(tmp_path: Path) -> None:
    ziel_win = PureWindowsPath(r"C:\Users\Nutzer\wt-660")
    ziel_uri = "file:///C:/Users/Nutzer/wt-660"
    assert werkzeug._uri_passt_zu_ordner("file:///C:/Users/Nutzer/wt-660", ziel_win, ziel_uri, windows=True)
    assert werkzeug._uri_passt_zu_ordner("file:///c%3A/Users/Nutzer/wt-660", ziel_win, ziel_uri, windows=True)
    assert werkzeug._uri_passt_zu_ordner("file:///c:/users/nutzer/wt-660/", ziel_win, ziel_uri, windows=True)
    assert not werkzeug._uri_passt_zu_ordner("file:///C:/Users/Nutzer/wt-999", ziel_win, ziel_uri, windows=True)

    leer_win = PureWindowsPath(r"C:\Users\Mein Profil\wt 660")
    assert werkzeug._uri_passt_zu_ordner(
        "file:///c%3A/Users/Mein%20Profil/wt%20660",
        leer_win,
        "file:///C:/Users/Mein%20Profil/wt%20660",
        windows=True,
    )
    unc_win = PureWindowsPath(r"\\server\freigabe\wt-660")
    assert werkzeug._uri_passt_zu_ordner(
        "file://server/freigabe/wt-660",
        unc_win,
        "file://server/freigabe/wt-660",
        windows=True,
    )

    # DB-Pfad mit Leerzeichen muss unter SQLite URI-Modus (file:///...%20...?mode=ro) lesbar sein.
    wt = tmp_path / "wt 660"
    wt.mkdir()
    agy_ordner = tmp_path / "agy home"
    uri = wt.resolve().as_uri()
    if sys.platform == "win32":
        # Kleinbuchstaben-Laufwerk + %3A wie bei Electron/VS Code/Antigravity unter Windows.
        uri = uri[:8] + uri[8].lower() + "%3A" + uri[10:]
    _schreibe_agy_db(
        agy_ordner,
        [("conv-win", [uri], "2026-11-26 11:00:00.000000+00:00", "CASCADE_RUN_STATUS_IDLE", "")],
    )
    assert werkzeug.agy_gespraech_fuer_ordner(wt, agy_ordner=agy_ordner) == "conv-win"


def test_kontext_anzeigen_lesen_agy_auch_unter_win32(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".to-spawn").mkdir()
    (tmp_path / ".to-spawn" / "config.json").write_text(json.dumps({"runner": "agy"}), encoding="utf-8")
    wt = tmp_path / "wt-660"
    wt.mkdir()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(werkzeug, "agy_spitze_k", lambda pfad, **_k: 184.5 if Path(pfad) == wt else None)

    stand = _lade_skript("sessions_stand_660_win", SKRIPTE / "sessions_stand.py", monkeypatch, repo=tmp_path)
    monkeypatch.setattr(stand.config, "worktree_pfad", lambda _t: str(wt))
    assert stand.token_text("660", tmp_path) == "184,5k"

    monkeypatch.setattr(aufseher_stand.config, "worktree_pfad", lambda _t, _r=None: str(wt))
    assert aufseher_stand._log_lage(660, tmp_path) == ("184,5k", "—", "—", 0)

    umwelt = leiter._EchteUmwelt(tmp_path, types.SimpleNamespace())  # type: ignore[arg-type]
    monkeypatch.setattr(leiter.leitstand, "leiter_respawn", lambda _t: None)
    assert umwelt._kontext_k(660, wt) == 184.5


def test_bau_agy_windows_bau_log_und_staffel_handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    orig_env = dict(os.environ)
    try:
        bau = _bau_fixture(tmp_path, monkeypatch, {"runner": "agy", "staffel": {"aktiv": True}})
        wt = tmp_path / "wt-660"
        wt.mkdir()
        handoff_dir = wt / "docs" / "handoffs"
        handoff_dir.mkdir(parents=True)

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(bau.starter, "finde", lambda name: str(tmp_path / f"{name}.exe"))
        monkeypatch.setattr(bau, "worktree_pfad", lambda _t: str(wt))
        monkeypatch.setattr(bau, "handoff_dirs", lambda _t: [str(handoff_dir)])
        monkeypatch.setattr(bau, "blocker_schranke", lambda *_a, **_k: None)
        monkeypatch.setattr(bau, "auf_speicher_warten", lambda *_a, **_k: None)
        monkeypatch.setattr(bau.nest, "git_hooks_einrichten", lambda _r: None)
        monkeypatch.setattr(bau.terminal_maus, "maus_ruhig", lambda: None)
        monkeypatch.setattr(bau, "umzug_anfragen_aufraeumen", lambda _t: tmp_path / "umzug-anfrage.json")
        monkeypatch.setattr(
            bau.startstelle,
            "weiter_entscheidung",
            lambda _t, _r: bau.startstelle.Entscheidung(True, 0, "", "", ""),
        )
        monkeypatch.setattr(bau.ticket_zustand, "lesen", lambda _t, _r: {"state": "open"})
        monkeypatch.setattr(bau.ticket_zustand, "fertig_gebaut", lambda _d: False)

        nachtraege: list[int] = []

        def _nachtragen(_repo: Path, _ticket: str, _wt: Path, **kw: object) -> dict[str, object]:
            nachtraege.append(int(kw["runde"]))
            return {"typ": "session_ende", "runner": "agy"}

        monkeypatch.setattr(bau.werkzeug, "agy_bau_log_nachtragen", _nachtragen)

        runden: list[list[str]] = []

        def _starte_session(cmd: list[str], _umzug: Path) -> tuple[int, None]:
            runden.append(list(cmd))
            if len(runden) == 1:
                datei = handoff_dir / "HANDOFF_2026-11-26_660.md"
                datei.write_text("# Handoff #660\nStaffel: weiter\nRunde 1 fertig.\n", encoding="utf-8")
                zukunft = time.time() + 5
                os.utime(datei, (zukunft, zukunft))
            return 0, None

        monkeypatch.setattr(bau, "starte_session", _starte_session)
        monkeypatch.setattr(sys, "argv", ["bau.py", "660"])
        assert bau.main() == 0
        assert len(runden) == 2
        assert nachtraege == [1, 2]
        eintraege = bau_log.lese(wt, "660")
        assert any(z.get("typ") == "handoff" and z.get("runner") == "agy" for z in eintraege)
    finally:
        os.environ.clear()
        os.environ.update(orig_env)
