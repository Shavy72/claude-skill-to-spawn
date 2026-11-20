"""Schlankes MCP-Profil für Aufseher, Takt und Leitstand (duoplus-management#1428).

Befund 21.11.2026, Bau-Server: Aufseher- und Leitstand-Sessions starteten alle MCP-Server
des Profils (graphify-mcp, chrome-devtools, firecrawl, perplexity …) — rund 0,7–0,9 GB
RSS + Swap je Session, obwohl kein Prompt sie nutzt. Bau-Sessions (``bau.py``) laufen
schon mit ``--strict-mcp-config``. Dieser Test bricht, sobald ein Start-Weg einen
Server außerhalb der Erlaubt-Liste mitgibt oder ohne ``--strict-mcp-config`` startet.
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

from to_spawn import context_mode, waechter_lauf, waechter_takt  # noqa: E402

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
    i = cmd.index("--mcp-config")
    # ``--mcp-config`` ist variadisch: direkt danach muss ein Flag folgen, sonst frisst es den Prompt.
    assert cmd[i + 2].startswith("--"), cmd
    daten = json.loads(Path(cmd[i + 1]).read_text(encoding="utf-8"))
    return set(daten["mcpServers"])


def _leitstand() -> ModuleType:
    spec = importlib.util.spec_from_file_location("leitstand_1428", SKILL / "skripte" / "leitstand.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul
    spec.loader.exec_module(modul)
    return modul


def _gestartet(monkeypatch: pytest.MonkeyPatch, cmd: list[str], cwd: Path) -> list[str]:
    """Was ``waechter_lauf._starte`` wirklich an die Start-Tür gibt (abgefangen, kein Start)."""
    gesehen: list[list[str]] = []

    def starte(befehl: list[str], **_: Any) -> object:
        gesehen.append(list(befehl))
        return object()

    monkeypatch.setattr(waechter_lauf.starter, "starte", starte)
    waechter_lauf._starte(cmd, cwd)
    return gesehen[0]


def test_aufseher_start_nur_erlaubte_server(plugin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = waechter_lauf.befehl("claude", "m", "", True, 7, "P", session_id="sid", effort="medium")
    gestartet = _gestartet(monkeypatch, cmd, tmp_path)
    assert _server(gestartet) == ERLAUBT
    assert gestartet[-1] == "P"


def test_aufseher_resume_nur_erlaubte_server(plugin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = waechter_lauf.resume_befehl("claude", "sid", "m", "medium", True, 7, "weiter")
    gestartet = _gestartet(monkeypatch, cmd, tmp_path)
    assert _server(gestartet) == ERLAUBT
    assert gestartet[0] == "claude" and "--resume" in gestartet
    assert gestartet[-1] == "weiter"


def test_aufseher_takt_nur_erlaubte_server(plugin: Path) -> None:
    cmd = waechter_takt.claude_befehl("claude", {}, 7)
    assert _server(cmd) == ERLAUBT
    assert cmd[-1] == "-p"


def test_leitstand_nur_erlaubte_server(plugin: Path) -> None:
    befehl = _leitstand().sitzung_befehl("claude-sonnet-5", ["Read"], "/x/takt.md")
    assert befehl[:3] == ["claude", "--model", "claude-sonnet-5"]
    assert _server(befehl) == ERLAUBT
    assert befehl[-1] == "/loop 3m Lies /x/takt.md und führe genau das aus"


def test_ohne_context_mode_trotzdem_schlank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fehlt das Plugin, lädt es auch ohne Sperre nicht — die Sperre bleibt, kein Absturz."""
    monkeypatch.setattr(context_mode, "CLAUDE_DIR", tmp_path / "leer")
    assert _server(waechter_takt.claude_befehl("claude", {}, 7)) == set()
