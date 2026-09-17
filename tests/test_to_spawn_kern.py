"""Weg-Tests für den Kern von ``to-spawn`` (Version 2).

Echt laufen: CLI, Manifest-Prüfung, Bau-Log, Hooks, Staffel-Schleife, Git-Repo mit
Bare-Remote. Gestellt sind nur zwei Dinge — das ``claude``-Programm (Session) und
die ``gh``-CLI (GitHub ist ein externer Dienst, per ``TO_SPAWN_GH_STUB``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
FAKE_CLAUDE = Path(__file__).resolve().parent / "hilfen" / "fake_claude.py"
GH_STUB = Path(__file__).resolve().parent / "hilfen" / "gh_stub.py"

sys.path.insert(0, str(SKILL))

from to_spawn import bau_log, bau_loop, config  # noqa: E402

SPEC = 900
TICKETS = ("901", "902")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


def _manifest_schreiben(repo: Path, eintraege: dict[str, dict]) -> None:
    ordner = repo / "docs" / "agents" / "manifests"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / f"spec-{SPEC}.json").write_text(
        json.dumps({"spec": SPEC, "feature": "wegwerf", "tickets": eintraege}, ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Wegwerf-Repo mit Bare-Remote, Manifest und gestellten Programmen."""
    fern = tmp_path / "fern.git"
    subprocess.run(["git", "init", "--bare", str(fern)], check=True, capture_output=True)
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    _git(arbeit, "init")
    _git(arbeit, "remote", "add", "origin", str(fern))
    _git(arbeit, "config", "user.email", "test@example.invalid")
    _git(arbeit, "config", "user.name", "Test")
    _manifest_schreiben(
        arbeit,
        {
            t: {
                "title": f"Wegwerf-Ticket {t}",
                "schaetzung_k": 120,
                "umfang": "Kern bauen, Test schreiben, Beweis führen.",
            }
            for t in TICKETS
        },
    )
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(GH_STUB))
    monkeypatch.setenv("GH_STUB_BLOCKER", f"{TICKETS[1]}:{TICKETS[0]}")
    monkeypatch.setenv("FAKE_CLAUDE_AUSGABE", str(tmp_path / "lauf"))
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    monkeypatch.chdir(arbeit)
    return arbeit


def _cli(
    repo: Path,
    *args: str,
    eingabe: str | None = None,
    zusatz_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    umgebung = {**os.environ, **(zusatz_env or {})}
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(repo),
        input=eingabe,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=umgebung,
        check=False,
    )


def _transkript(pfad: Path, zeilen: list[dict]) -> Path:
    pfad.write_text(
        "\n".join(json.dumps(z, ensure_ascii=False) for z in zeilen) + "\n",
        encoding="utf-8",
    )
    return pfad


def _assistant(usage: dict, sidechain: bool = False) -> dict:
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "timestamp": "2026-09-18T10:00:00+00:00",
        "message": {"model": "fake-modell", "usage": usage},
    }


def test_pruefen_weigert_sich_ohne_schaetzung(repo: Path) -> None:
    """(a) Fehlt ``schaetzung_k``, startet nichts — Exit 3 mit klarer Anweisung."""
    _manifest_schreiben(repo, {TICKETS[0]: {"title": "ohne Schätzung", "umfang": "irgendwas"}})
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, ergebnis.stdout + ergebnis.stderr
    assert "schaetzung_k" in ergebnis.stdout
    assert "/to-tickets" in ergebnis.stdout


def test_pruefen_weigert_sich_bei_zu_grossem_ticket(repo: Path) -> None:
    """(b) 250k über der Grenze 200k → Weigerung."""
    _manifest_schreiben(
        repo,
        {TICKETS[0]: {"title": "zu groß", "schaetzung_k": 250, "umfang": "riesig"}},
    )
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, ergebnis.stdout
    assert "über der Grenze" in ergebnis.stdout


def test_pruefen_geht_durch_bei_vollstaendigem_manifest(repo: Path) -> None:
    """Gegenprobe: vollständiges Manifest → Exit 0."""
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr


def test_stop_hook_schreibt_session_ende(repo: Path, tmp_path: Path) -> None:
    """(c) Stop-Hook summiert die Token der Hauptsitzung korrekt."""
    transkript = _transkript(
        tmp_path / "haupt.jsonl",
        [
            _assistant(
                {
                    "input_tokens": 100,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 30,
                    "output_tokens": 40,
                }
            ),
            _assistant({"input_tokens": 5, "output_tokens": 7}),
            _assistant({"input_tokens": 999, "output_tokens": 999}, sidechain=True),
        ],
    )
    ergebnis = _cli(
        repo,
        "hook-stop",
        eingabe=json.dumps(
            {
                "session_id": "sess-1",
                "transcript_path": str(transkript),
                "hook_event_name": "Stop",
                "stop_hook_active": False,
                "last_assistant_message": "Fertig.",
            }
        ),
        zusatz_env={"TO_SPAWN_TICKET": TICKETS[0]},
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    enden = [z for z in bau_log.lese(repo, TICKETS[0]) if z["typ"] == "session_ende"]
    assert len(enden) == 1
    assert enden[0]["tokens"] == {
        "input": 105,
        "cache_read": 200,
        "cache_creation": 30,
        "output": 47,
        "gesamt": 382,
    }
    assert enden[0]["session_id"] == "sess-1"


def test_hook_ohne_ticket_schreibt_nichts(repo: Path, tmp_path: Path) -> None:
    """Ohne ``TO_SPAWN_TICKET`` und ohne Worktree-Pfad entsteht keine Log-Zeile."""
    transkript = _transkript(tmp_path / "leer.jsonl", [_assistant({"input_tokens": 1})])
    ergebnis = _cli(
        repo,
        "hook-stop",
        eingabe=json.dumps({"session_id": "x", "transcript_path": str(transkript)}),
    )
    assert ergebnis.returncode == 0
    assert bau_log.alle_tickets(repo) == []


def test_subagent_hook_schreibt_eltern_session(repo: Path, tmp_path: Path) -> None:
    """(d) SubagentStop zählt nur die letzte Sidechain-Kette und nennt die Eltern-Session."""
    transkript = _transkript(
        tmp_path / "mit_subagent.jsonl",
        [
            _assistant({"input_tokens": 3, "output_tokens": 11}, sidechain=True),
            _assistant({"input_tokens": 50, "output_tokens": 5}),
            _assistant({"input_tokens": 1000, "output_tokens": 20}, sidechain=True),
        ],
    )
    ergebnis = _cli(
        repo,
        "hook-subagent-stop",
        eingabe=json.dumps(
            {
                "session_id": "sess-eltern",
                "agent_id": "agent-7",
                "agent_type": "executor-sonnet",
                "transcript_path": str(transkript),
                "hook_event_name": "SubagentStop",
                "last_assistant_message": "Subagent fertig.",
            }
        ),
        zusatz_env={"TO_SPAWN_TICKET": TICKETS[0]},
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    subs = [z for z in bau_log.lese(repo, TICKETS[0]) if z["typ"] == "subagent_ende"]
    assert len(subs) == 1
    assert subs[0]["eltern_session"] == "sess-eltern"
    assert subs[0]["tokens"]["gesamt"] == 1020


def _staffel_lauf(repo: Path) -> int:
    """Eine Staffel fahren: erster Lauf schreibt Handoff, zweiter wird fertig."""
    os.environ["FAKE_CLAUDE_SZENARIO"] = "handoff"
    return bau_loop.run_ticket(
        TICKETS[0],
        [sys.executable, str(FAKE_CLAUDE), "Auftrag für Ticket 901."],
        repo=repo,
        konfig=config.lade(repo),
        takt=0.2,
    )


def test_staffel_startet_folge_session_mit_handoff(repo: Path, tmp_path: Path) -> None:
    """(e) Handoff + offenes Ticket → zweiter Start, Handoff steckt im Prompt."""
    assert _staffel_lauf(repo) == 0
    zeilen = bau_log.lese(repo, TICKETS[0])
    assert sum(1 for z in zeilen if z["typ"] == "session_start") == 2
    assert sum(1 for z in zeilen if z["typ"] == "handoff") == 1
    zweiter_prompt = (tmp_path / "lauf" / f"prompt-{TICKETS[0]}-2.txt").read_text(encoding="utf-8")
    assert "Startkontext aus dem Handoff" in zweiter_prompt
    assert "Hälfte gebaut" in zweiter_prompt


def test_log_tabelle_zeigt_staffel_zwei(repo: Path) -> None:
    """(f) Die Gesamt-Tabelle weist die zweite Staffel aus."""
    _staffel_lauf(repo)
    ergebnis = _cli(repo, "log", str(SPEC))
    assert ergebnis.returncode == 0, ergebnis.stderr
    zeile = next(z for z in ergebnis.stdout.splitlines() if z.startswith(f"#{TICKETS[0]}"))
    assert zeile.split()[4] == "2", zeile
