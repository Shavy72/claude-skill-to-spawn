"""Fixrunde #204: Token-Doppelzählung, unversionierte Laufdatei, Zusammenfassung lesen,
Hook-Protokoll, Subagenten-Kennung und Subagenten-Dauer.

Weg-Test wie in ``test_bau_log_204``: echter ``skripte/bau.py``-Aufruf gegen ein
Wegwerf-Repo mit Git-Worktree; gestellt ist nur ``claude`` (``hilfen/fake_claude.py``).
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pytest

from test_bau_log_204 import (  # noqa: F401 — ``repo`` ist ein Fixture
    CLI,
    HAUPT_SESSION,
    HAUPT_TOKENS,
    SKILL,
    SUBAGENT_TOKENS,
    TICKET,
    _bau,
    _git,
    _hook_cli,
    _lauf,
    _umgebung,
    repo,
)

sys.path.insert(0, str(SKILL))

from to_spawn import bau_log, hooks  # noqa: E402


def _laufdatei(ort: Path, ticket: str = TICKET) -> Path:
    return ort / ".to-spawn" / "bau_log" / f"{ticket}.jsonl"


def _roh(datei: Path) -> list[str]:
    if not datei.is_file():
        return []
    return [z for z in datei.read_text(encoding="utf-8").splitlines() if z.strip()]


# --- Fix 1: eine Antwort in mehreren Zeilen zählt einmal -------------------------


def _antwort(
    usage: dict, nachricht_id: str | None = None, anfrage_id: str | None = None
) -> dict:
    zeile: dict = {"type": "assistant", "message": {"usage": usage}}
    if nachricht_id:
        zeile["message"]["id"] = nachricht_id
    if anfrage_id:
        zeile["requestId"] = anfrage_id
    return zeile


def test_summiere_zaehlt_gleiche_nachricht_einmal() -> None:
    u = {"input_tokens": 10, "output_tokens": 5}
    eintraege = [
        _antwort(u, "msg_a", "req_a"),
        _antwort(u, "msg_a", "req_a"),  # Teilzeile derselben Antwort
        _antwort(u, "msg_a", "req_a"),
        _antwort(u, None, "req_b"),  # nur requestId
        _antwort(u, None, "req_b"),
        _antwort(u),  # ohne Kennung → einzeln
        _antwort(u),
    ]
    summe = hooks.summiere(eintraege)
    assert summe["input"] == 40  # msg_a, req_b, zwei ohne Kennung
    assert summe["output"] == 20
    assert summe["gesamt"] == 60


# --- Fix 2: Hooks schreiben unversioniert, ``eintrag`` überträgt -----------------


def _repo_mit_gitignore(repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    (repo / ".gitignore").write_text(".to-spawn/*\n!.to-spawn/config.json\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "gitignore wie DuoPlus")
    wt_basis = tmp_path / "wt"
    worktree = wt_basis / f"wt-{TICKET}"
    _git(repo, "worktree", "add", "-b", f"wt-{TICKET}", str(worktree))
    return wt_basis, worktree


def _status(worktree: Path) -> str:
    import subprocess

    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_weg_hooks_lassen_worktree_sauber_eintrag_uebertraegt(
    repo: Path, tmp_path: Path
) -> None:
    wt_basis, worktree = _repo_mit_gitignore(repo, tmp_path)

    ergebnis = _bau(repo, tmp_path, wt_basis)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr

    assert _status(worktree) == "", "Hooks dürfen den Worktree nie dirty machen"
    versioniert = bau_log.log_pfad(worktree, TICKET)
    assert not versioniert.exists(), "nur eintrag schreibt die versionierte Datei"
    lauf = _roh(_laufdatei(worktree))
    assert lauf, "Hooks schreiben in die Laufdatei"

    # Token-Summen trotz Teilzeilen mit gleicher message.id/requestId (Fix 1).
    zeilen = bau_log.lese(worktree, TICKET)
    enden = [z for z in zeilen if z["typ"] == "session_ende"]
    assert enden[-1]["tokens"] == HAUPT_TOKENS
    subs = [z for z in zeilen if z["typ"] == "subagent_ende"]
    assert subs[0]["tokens"] == SUBAGENT_TOKENS
    assert bau_log.zusammenfassung(worktree, TICKET)["ist_k"] == 62.6

    umgebung = {**_umgebung(tmp_path, wt_basis), "TO_SPAWN_LOG_REPO": str(worktree)}
    eintrag = [
        sys.executable,
        str(CLI),
        "eintrag",
        "--ticket",
        TICKET,
        "--typ",
        "zusammenfassung",
        "--umfang",
        "Laufdatei gebaut.",
        "--schwierigkeiten",
        "Worktree wurde dirty.",
        "--entscheidungen",
        "Nur eintrag schreibt versioniert.",
    ]
    lauf1 = _lauf(eintrag, worktree, umgebung)
    assert lauf1.returncode == 0, lauf1.stderr
    fest = _roh(versioniert)
    for zeile in lauf:
        assert zeile in fest, f"Laufdatei-Zeile fehlt in der versionierten Datei: {zeile}"
    assert json.loads(fest[-1])["typ"] == "zusammenfassung"
    assert len(fest) == len(lauf) + 1

    # Zweiter eintrag überträgt nichts doppelt; lese() zählt jede Zeile einmal.
    lauf2 = _lauf([*eintrag[:-1], "Zweite Runde."], worktree, umgebung)
    assert lauf2.returncode == 0, lauf2.stderr
    fest2 = _roh(versioniert)
    assert len(fest2) == len(lauf) + 2
    assert len(bau_log.lese(worktree, TICKET)) == len(lauf) + 2
    assert bau_log.zusammenfassung(worktree, TICKET)["ist_k"] == 62.6

    # Nach dem Commit der versionierten Datei ist der Worktree wieder sauber.
    _git(worktree, "add", str(versioniert))
    _git(worktree, "commit", "-m", "Bau-Log")
    assert _status(worktree) == ""


def test_lese_vereint_versioniert_und_laufdatei(repo: Path) -> None:
    gemeinsam = {"ts": "2026-09-18T10:01:00+00:00", "typ": "session_ende", "session_id": "a"}
    fest = bau_log.log_pfad(repo, TICKET)
    fest.parent.mkdir(parents=True)
    fest.write_text(
        json.dumps({"ts": "2026-09-18T10:03:00+00:00", "typ": "zusammenfassung"}) + "\n"
        + json.dumps(gemeinsam) + "\n",
        encoding="utf-8",
    )
    lauf = _laufdatei(repo)
    lauf.parent.mkdir(parents=True)
    lauf.write_text(
        json.dumps(gemeinsam) + "\n"
        + json.dumps({"ts": "2026-09-18T10:00:00+00:00", "typ": "session_start"}) + "\n",
        encoding="utf-8",
    )
    zeilen = bau_log.lese(repo, TICKET)
    assert [z["typ"] for z in zeilen] == ["session_start", "session_ende", "zusammenfassung"]
    assert bau_log.alle_tickets(repo) == [TICKET]


def test_alle_tickets_findet_nur_laufdatei(repo: Path) -> None:
    lauf = _laufdatei(repo, "55")
    lauf.parent.mkdir(parents=True)
    lauf.write_text(json.dumps({"ts": "2026-09-18T10:00:00+00:00", "typ": "session_ende"}) + "\n")
    assert bau_log.alle_tickets(repo) == ["55"]


def test_prompt_eintrag_vor_handoff_commit() -> None:
    prompt = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))[
        "prompt_template"
    ]
    assert "vor jedem Handoff-Commit" in prompt
    assert "`" not in prompt


# --- Fix 3: Zusammenfassung wird gelesen -----------------------------------------


def _schreibe_fest(repo: Path, zeilen: list[dict]) -> None:
    fest = bau_log.log_pfad(repo, TICKET)
    fest.parent.mkdir(parents=True, exist_ok=True)
    with fest.open("a", encoding="utf-8") as fh:
        for zeile in zeilen:
            fh.write(json.dumps({"ticket": TICKET, **zeile}, ensure_ascii=False) + "\n")


def test_zusammenfassung_liefert_juengste_klartext_zeile(repo: Path) -> None:
    _schreibe_fest(
        repo,
        [
            {
                "ts": "2026-09-18T10:00:00+00:00",
                "typ": "zusammenfassung",
                "umfang": "alt",
                "schwierigkeiten": "alt",
                "entscheidungen": "alt",
            },
            {
                "ts": "2026-09-18T11:00:00+00:00",
                "typ": "zusammenfassung",
                "umfang": "Hooks gebaut.",
                "schwierigkeiten": "Stop feuert je Runde.",
                "entscheidungen": "Jüngste Zeile zählt.",
            },
        ],
    )
    z = bau_log.zusammenfassung(repo, TICKET)
    assert z["umfang_ist"] == "Hooks gebaut."
    assert z["schwierigkeiten"] == "Stop feuert je Runde."
    assert z["entscheidungen_text"] == "Jüngste Zeile zählt."
    text = bau_log.lernstoff(repo, grenze_k=200)
    assert "Stop feuert je Runde." in text
    zeile = next(z for z in text.splitlines() if "Stop feuert je Runde." in z)
    assert "Schwierigkeiten" in zeile
    assert "Schwierigkeiten: alt" not in text


# --- Fix 4: Hook-Protokoll in ~/.claude/to-spawn/hooks.log ------------------------


def test_hook_protokolliert_fehlenden_worktree(repo: Path, tmp_path: Path) -> None:
    heim = tmp_path / "heim"
    heim.mkdir()
    fehlt = tmp_path / "wt" / f"wt-{TICKET}"
    for befehl in ("hook-stop", "hook-subagent-stop"):
        ergebnis = _hook_cli(
            repo,
            befehl,
            json.dumps({"session_id": "s", "transcript_path": "/gibt/es/nicht.jsonl"}),
            {"TO_SPAWN_TICKET": TICKET, "TO_SPAWN_LOG_REPO": str(fehlt), "HOME": str(heim)},
        )
        assert ergebnis.returncode == 0, ergebnis.stderr
    protokoll = heim / ".claude" / "to-spawn" / "hooks.log"
    assert protokoll.is_file()
    zeilen = [
        z
        for z in protokoll.read_text(encoding="utf-8").splitlines()
        if str(fehlt) in z and f"#{TICKET}" in z
    ]
    assert any("Stop-Hook" in z and "SubagentStop" not in z for z in zeilen), zeilen
    assert any("SubagentStop-Hook" in z for z in zeilen), zeilen


def test_hook_ohne_schreibbares_heim_laeuft_weiter(repo: Path, tmp_path: Path) -> None:
    heim = tmp_path / "heim-datei"
    heim.write_text("kein Ordner", encoding="utf-8")
    ziel = tmp_path / "wt" / f"wt-{TICKET}"
    ziel.mkdir(parents=True)
    ergebnis = _hook_cli(
        repo,
        "hook-stop",
        json.dumps({"session_id": "s", "transcript_path": "/gibt/es/nicht.jsonl"}),
        {"TO_SPAWN_TICKET": TICKET, "TO_SPAWN_LOG_REPO": str(ziel), "HOME": str(heim)},
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert [z["typ"] for z in bau_log.lese(ziel, TICKET)] == ["session_start", "session_ende"]


# --- Fix 5 + 6: Subagenten-Kennung und -Dauer ------------------------------------


def _subagent_transkript(pfad: Path) -> Path:
    zeilen = [
        {
            "type": "assistant",
            "isSidechain": True,
            "timestamp": "2026-09-18T10:02:00Z",
            "message": {"id": "m1", "usage": {"input_tokens": 3}},
        },
        {
            "type": "assistant",
            "isSidechain": True,
            "timestamp": "2026-09-18T10:03:00Z",
            "message": {"id": "m2", "usage": {"input_tokens": 4}},
        },
    ]
    pfad.write_text("".join(json.dumps(z) + "\n" for z in zeilen), encoding="utf-8")
    return pfad


@pytest.fixture()
def hook_umgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ziel = tmp_path / "wt" / f"wt-{TICKET}"
    ziel.mkdir(parents=True)
    heim = tmp_path / "heim-intern"
    heim.mkdir()
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("TO_SPAWN_TICKET", TICKET)
    monkeypatch.setenv("TO_SPAWN_LOG_REPO", str(ziel))
    # Session läuft seit 1000 s — ein Subagent darf das nicht als eigene Dauer melden.
    monkeypatch.setenv("TO_SPAWN_START", str(time.time() - 1000))
    return ziel


def _subagent_stop(daten: dict) -> None:
    assert hooks.hook_subagent_stop(io.StringIO(json.dumps(daten))) == 0


def test_subagent_ohne_agent_id_nimmt_dateinamen(hook_umgebung: Path, tmp_path: Path) -> None:
    eins = _subagent_transkript(tmp_path / "agent-aaa.jsonl")
    zwei = _subagent_transkript(tmp_path / "agent-bbb.jsonl")
    _subagent_stop({"session_id": HAUPT_SESSION, "agent_transcript_path": str(eins)})
    _subagent_stop({"session_id": HAUPT_SESSION, "agent_transcript_path": str(zwei)})
    subs = [z for z in bau_log.lese(hook_umgebung, TICKET) if z["typ"] == "subagent_ende"]
    assert [z["session_id"] for z in subs] == ["agent-aaa", "agent-bbb"]
    assert bau_log.zusammenfassung(hook_umgebung, TICKET)["subagenten"] == 2


def test_subagent_ohne_agent_id_und_datei_nimmt_session_und_zeit(
    hook_umgebung: Path, tmp_path: Path
) -> None:
    haupt = tmp_path / "haupt.jsonl"
    haupt.write_text(
        json.dumps({"type": "user", "timestamp": "2026-09-18T10:00:00Z"})
        + "\n"
        + _subagent_transkript(tmp_path / "x.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _subagent_stop({"session_id": HAUPT_SESSION, "transcript_path": str(haupt)})
    subs = [z for z in bau_log.lese(hook_umgebung, TICKET) if z["typ"] == "subagent_ende"]
    assert subs[0]["session_id"] == f"{HAUPT_SESSION}:2026-09-18T10:02:00Z"


def test_subagent_dauer_nur_aus_eigener_kette(hook_umgebung: Path, tmp_path: Path) -> None:
    eins = _subagent_transkript(tmp_path / "agent-ccc.jsonl")
    _subagent_stop(
        {"session_id": HAUPT_SESSION, "agent_id": "a1", "agent_transcript_path": str(eins)}
    )
    subs = [z for z in bau_log.lese(hook_umgebung, TICKET) if z["typ"] == "subagent_ende"]
    assert subs[0]["dauer_s"] == 60
