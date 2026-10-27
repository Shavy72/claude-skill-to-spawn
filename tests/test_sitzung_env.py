"""Neue Sessions erben keine Sitzungs-Variablen (Child-Session-Schutz)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from to_spawn.sitzung_env import ist_sitzungs_variable, ohne_sitzung  # noqa: E402

ENTFERNT = [
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_BRIDGE_URL",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
]
BLEIBT = [
    "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
    "ANTHROPIC_PROMPT_CACHE_TTL",
    "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE",
    "TO_SPAWN_REPO",
    "PATH",
    "CLAUDECODE_X",
]


@pytest.mark.parametrize("name", ENTFERNT)
def test_sitzungs_variable_erkannt(name: str) -> None:
    assert ist_sitzungs_variable(name)


@pytest.mark.parametrize("name", BLEIBT)
def test_einstellung_bleibt(name: str) -> None:
    assert not ist_sitzungs_variable(name)


def test_ohne_sitzung_filtert_und_laesst_original() -> None:
    env = {n: "1" for n in ENTFERNT + BLEIBT}
    ergebnis = ohne_sitzung(env)
    assert sorted(ergebnis) == sorted(BLEIBT)
    assert len(env) == len(ENTFERNT) + len(BLEIBT)


def test_ohne_sitzung_standard_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
    monkeypatch.setenv("CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH", "4")
    ergebnis = ohne_sitzung()
    assert "CLAUDE_CODE_SESSION_ID" not in ergebnis
    assert ergebnis["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "4"
    assert os.environ["CLAUDE_CODE_SESSION_ID"] == "abc"


# Regel → Prüf-Skript: jeder Python-Starter einer neuen Claude-Session nutzt ohne_sitzung().
SKILL = Path(__file__).resolve().parent.parent
STARTER = ["skripte/bau.py", "skripte/wache.py", "skripte/leitstand_fenster.py", "to_spawn/probesitz.py"]


@pytest.mark.parametrize("rel", STARTER)
def test_starter_nutzt_ohne_sitzung(rel: str) -> None:
    datei = SKILL / rel
    if not datei.exists():
        pytest.skip(f"{rel} fehlt in diesem Stand")
    text = datei.read_text(encoding="utf-8")
    assert "sitzung_env.ohne_sitzung()" in text
    assert 'pop("CLAUDE_CODE_CHILD_SESSION"' not in text, "Einzel-pop reicht nicht, ohne_sitzung() nutzen"
