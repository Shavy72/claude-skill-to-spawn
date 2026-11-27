"""Schlankes MCP-Profil für alle Startwege: Bau, Aufseher, Takt und Leitstand (#869, App #1428).

Ohne ``--strict-mcp-config`` startet jede Claude-Session alle MCP-Server des Profils
(z. B. graphify-mcp, chrome-devtools, firecrawl, perplexity) mit hohem Speicher- und
Auslagerungsbedarf. Dieser Prüf-Test bricht, sobald einer der vier Startwege (Bau,
Aufseher, Takt, Leitstand) ohne ``--mcp-config`` + ``--strict-mcp-config`` startet oder
einen Server außerhalb der Erlaubt-Liste mitgibt.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import context_mode, sitzung_filter, waechter_lauf, waechter_takt, werkzeug  # noqa: E402

ERLAUBT = {context_mode.SERVER_NAME}


@pytest.fixture
def plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Wegwerf-``~/.claude`` mit installiertem context-mode im Plugin-Cache."""
    wurzel = tmp_path / ".claude" / "plugins" / "cache" / "context-mode" / "context-mode" / "1.0.0"
    wurzel.mkdir(parents=True)
    (wurzel / context_mode.START).write_text("", encoding="utf-8")
    monkeypatch.setattr(context_mode, "CLAUDE_DIR", tmp_path / ".claude")
    return wurzel


def _server(cmd: list[str]) -> set[str]:
    """MCP-Server, die dieser Befehl startet — bricht ohne ``--strict-mcp-config``."""
    assert "--strict-mcp-config" in cmd, cmd
    assert "--mcp-config" in cmd, cmd
    i = cmd.index("--mcp-config")
    # ``--mcp-config`` ist variadisch: direkt danach muss ein Flag folgen, sonst frisst es den Prompt.
    assert i + 2 < len(cmd) and cmd[i + 2].startswith("--"), cmd
    daten = json.loads(Path(cmd[i + 1]).read_text(encoding="utf-8"))
    return set(daten["mcpServers"])


def _leitstand_fenster() -> ModuleType:
    name = "leitstand_fenster_869"
    vorhanden = sys.modules.get(name)
    if vorhanden is not None:
        return vorhanden
    spec = importlib.util.spec_from_file_location(name, SKILL / "skripte" / "leitstand_fenster.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    spec.loader.exec_module(modul)
    return modul


def test_bau_start_mit_strict_mcp_config(plugin: Path, tmp_path: Path) -> None:
    mcp_servers, chrome = sitzung_filter.mcp_auswahl([], {})
    mcp_servers.update(context_mode.server_definition(tmp_path / ".claude"))
    mcp_pfad = tmp_path / "bau-mcp.json"
    mcp_pfad.write_text(json.dumps({"mcpServers": mcp_servers}, ensure_ascii=False), encoding="utf-8")
    cmd = werkzeug.startbefehl(
        "claude",
        programm="claude",
        modell="claude-sonnet-5",
        prompt="Baue Ticket #1",
        mcp_pfad=mcp_pfad,
        chrome=chrome,
    )
    assert _server(cmd) == ERLAUBT
    assert cmd[-1] == "Baue Ticket #1"


def test_aufseher_start_und_resume_mit_strict_mcp_config(plugin: Path, tmp_path: Path) -> None:
    mcp_servers, chrome = sitzung_filter.aufseher_mcp_auswahl(
        {}, repo=tmp_path, home=tmp_path, claude_dir=tmp_path / ".claude"
    )
    mcp_servers.update(context_mode.server_eintrag(plugin))
    mcp_pfad = tmp_path / "wache-mcp.json"
    mcp_pfad.write_text(json.dumps({"mcpServers": mcp_servers}, ensure_ascii=False), encoding="utf-8")

    start = waechter_lauf.befehl(
        "claude", "m", "", True, 7, "Prompt", session_id="sid", effort="medium", mcp_pfad=mcp_pfad, chrome=chrome
    )
    assert _server(start) == ERLAUBT
    assert start[-1] == "Prompt"

    weiter = waechter_lauf.resume_befehl(
        "claude", "sid", "m", "medium", True, 7, "Weiter", mcp_pfad=mcp_pfad, chrome=chrome
    )
    assert _server(weiter) == ERLAUBT
    assert "--resume" in weiter and weiter[-1] == "Weiter"


def test_aufseher_takt_nur_erlaubte_server(plugin: Path) -> None:
    cmd = waechter_takt.claude_befehl("claude", {}, 7)
    assert _server(cmd) == ERLAUBT
    assert cmd[-1] == "-p"


def test_leitstand_befehl_nur_erlaubte_server(plugin: Path) -> None:
    lf = _leitstand_fenster()
    befehl = lf.sitzung_befehl("claude-sonnet-5", ["Read"], "/x/takt.md")
    assert befehl[:3] == ["claude", "--model", "claude-sonnet-5"]
    assert _server(befehl) == ERLAUBT
    assert befehl[-1] == "/loop 3m Lies /x/takt.md und führe genau das aus"


def test_leitstand_sitzung_startet_mit_strict_mcp_config(
    plugin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lf = _leitstand_fenster()
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("", encoding="utf-8")
    gesehen: list[list[str]] = []

    class _FakeProc:
        pid = 12345

        def wait(self) -> int:
            return 0

    def _fake_starte(befehl: list[str], **_: Any) -> _FakeProc:
        gesehen.append(list(befehl))
        return _FakeProc()

    monkeypatch.setattr(lf.starter, "finde", lambda _name: str(fake_claude))
    monkeypatch.setattr(lf.starter, "starte", _fake_starte)
    monkeypatch.setattr(lf.sitzungsjob, "gebunden", lambda _proc: _NullCtx())

    ablage = lf.Ablage(repo=tmp_path, spec="869")
    code = lf.sitzung(ablage, probe=False)
    assert code == 0
    assert len(gesehen) == 1
    assert _server(gesehen[0]) == ERLAUBT


class _NullCtx:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: Any) -> bool:
        return False


def test_ohne_context_mode_trotzdem_strict_mcp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fehlt das Plugin, startet weder Takt noch Leitstand ohne Sperre — kein Absturz, leere Liste."""
    monkeypatch.setattr(context_mode, "CLAUDE_DIR", tmp_path / "leer")
    assert _server(waechter_takt.claude_befehl("claude", {}, 7)) == set()
    lf = _leitstand_fenster()
    assert _server(lf.sitzung_befehl("claude-sonnet-5", ["Read"], "/x/takt.md")) == set()
