"""#204: Bau-Log je Ticket — Hooks im echten Starter, Sammler, Token-Spalte.

Weg-Test: echter Aufruf ``skripte/bau.py <N> --sofort`` gegen ein Wegwerf-Repo mit
echtem Git-Worktree. Gestellt ist nur das ``claude``-Programm (``hilfen/fake_claude.py``,
Szenario ``hooks``): es liest die ``settings.json`` wie Claude Code und führt die
eingetragenen Stop-/SubagentStop-Befehle mit JSON auf stdin aus. Hooks, Bau-Log,
CLI und ``skripte/sessions_stand.py`` laufen echt.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
SKRIPTE = SKILL / "skripte"
FAKE_CLAUDE = Path(__file__).resolve().parent / "hilfen" / "fake_claude.py"
import context_mode_attrappe  # noqa: E402  (tests/hilfen, Pfad setzt conftest)

sys.path.insert(0, str(SKILL))

from to_spawn import bau_log  # noqa: E402

TICKET = "901"
SPEC = "900"
HAUPT_SESSION = "sess-haupt-204"
SUBAGENT_ID = "agent-204"

#: Endstand der Hauptsitzung nach Runde 2 (kumuliert, siehe fake_claude „hooks“).
HAUPT_TOKENS = {
    "input": 1500,
    "cache_read": 50000,
    "cache_creation": 4000,
    "output": 1000,
    "gesamt": 56500,
}
SUBAGENT_TOKENS = {
    "input": 200,
    "cache_read": 5000,
    "cache_creation": 800,
    "output": 100,
    "gesamt": 6100,
}

#: Pfad der Repo-Kopie des Loop-Prompts (duoplus-management, Ticket-Worktree).
REPO_DEFAULT = Path(
    os.environ.get(
        "TO_SPAWN_TEST_REPO_DEFAULT",
        "/home/bau/wt/wt-204/docs/agents/manifests/_default.json",
    )
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Wegwerf-Repo mit Manifest, Konfig (Effort „high“) und einem Commit."""
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    _git(arbeit, "init", "-b", "master")
    _git(arbeit, "config", "user.email", "test@example.invalid")
    _git(arbeit, "config", "user.name", "Test")
    manifeste = arbeit / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    (manifeste / f"spec-{SPEC}.json").write_text(
        json.dumps(
            {
                "spec": int(SPEC),
                "feature": "wegwerf",
                "tickets": {TICKET: {"title": "Wegwerf-Ticket", "schaetzung_k": 120, "umfang": "Kern bauen."}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (manifeste / "_default.json").write_text(
        (SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    konfig = arbeit / ".to-spawn" / "config.json"
    konfig.parent.mkdir()
    konfig.write_text(json.dumps({"effort": {"ticket": "high"}}), encoding="utf-8")
    _git(arbeit, "add", "-A")
    _git(arbeit, "commit", "-m", "Start")
    for name in ("TO_SPAWN_REPO", "TO_SPAWN_TICKET", "TO_SPAWN_LOG_REPO", "TO_SPAWN_START"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(arbeit)
    return arbeit


def _umgebung(tmp_path: Path, wt_basis: Path) -> dict[str, str]:
    binaer = tmp_path / "bin"
    binaer.mkdir(exist_ok=True)
    claude = binaer / "claude"
    claude.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLAUDE}" "$@"\n', encoding="utf-8")
    claude.chmod(0o755)
    heim = tmp_path / "heim"
    heim.mkdir(exist_ok=True)
    context_mode_attrappe.plugin_anlegen(heim)  # Pflicht-Plugin seit #237
    temp = tmp_path / "tmp"
    temp.mkdir(exist_ok=True)
    umgebung = {
        **os.environ,
        "PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(heim),
        "TMPDIR": str(temp),
        "BAU_WT_DIR": str(wt_basis),
        "FAKE_CLAUDE_SZENARIO": "hooks",
        "FAKE_CLAUDE_AUSGABE": str(tmp_path / "lauf"),
    }
    for name in ("LOCALAPPDATA", "TO_SPAWN_REPO", "TO_SPAWN_TICKET", "TO_SPAWN_LOG_REPO"):
        umgebung.pop(name, None)
    return umgebung


def _lauf(befehl: list[str], cwd: Path, umgebung: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        befehl,
        cwd=str(cwd),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def _bau(repo: Path, tmp_path: Path, wt_basis: Path) -> subprocess.CompletedProcess[str]:
    return _lauf(
        [sys.executable, str(SKRIPTE / "bau.py"), TICKET, "--sofort"],
        repo,
        _umgebung(tmp_path, wt_basis),
    )


def _hook_ergebnisse(tmp_path: Path) -> list[dict]:
    datei = tmp_path / "lauf" / f"hooks-{TICKET}.jsonl"
    if not datei.is_file():
        return []
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z.strip()]


def _to_spawn_hooks(tmp_path: Path) -> list[dict]:
    return [h for h in _hook_ergebnisse(tmp_path) if "to_spawn.py" in h["befehl"]]


# --- Weg-Test: bau.py → Hooks → Bau-Log im Worktree → sessions_stand ----------


def test_weg_bau_schreibt_bau_log_im_worktree(repo: Path, tmp_path: Path) -> None:
    wt_basis = tmp_path / "wt"
    worktree = wt_basis / f"wt-{TICKET}"
    _git(repo, "worktree", "add", "-b", f"wt-{TICKET}", str(worktree))

    ergebnis = _bau(repo, tmp_path, wt_basis)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr

    hooks = _to_spawn_hooks(tmp_path)
    ereignisse = sorted(h["ereignis"] for h in hooks)
    assert ereignisse == ["Stop", "Stop", "SubagentStop"], _hook_ergebnisse(tmp_path)
    assert all(h["code"] == 0 for h in hooks), hooks
    stop_befehle = [h["befehl"] for h in _hook_ergebnisse(tmp_path) if h["ereignis"] == "Stop"]
    assert "staffel_stop.py" in stop_befehle[0], "Staffel-Hook muss zuerst laufen"

    assert not (repo / bau_log.LOG_ORDNER).exists(), "nie in den Hauptbaum schreiben"
    zeilen = bau_log.lese(worktree, TICKET)
    starts = [z for z in zeilen if z["typ"] == "session_start"]
    assert len(starts) == 1, zeilen
    assert starts[0]["session_id"] == HAUPT_SESSION
    assert starts[0]["modell"] == "claude-opus-5"
    assert starts[0]["staffel"] == 1
    assert str(starts[0]["beginn"]).startswith("2026-09-18T10:00:00")

    enden = [z for z in zeilen if z["typ"] == "session_ende"]
    assert len(enden) == 2  # Stop feuert je Runde
    letzte = enden[-1]
    assert letzte["tokens"] == HAUPT_TOKENS
    assert letzte["modell"] == "claude-opus-5"
    assert letzte["effort"] == "high"
    assert letzte["dauer_s"] > 0
    assert letzte["session_id"] == HAUPT_SESSION

    subs = [z for z in zeilen if z["typ"] == "subagent_ende"]
    assert len(subs) == 1
    assert subs[0]["tokens"] == SUBAGENT_TOKENS
    assert subs[0]["eltern_session"] == HAUPT_SESSION
    assert subs[0]["vermerk"] == f"Subagent von Session {HAUPT_SESSION}"

    zusammen = bau_log.zusammenfassung(worktree, TICKET)
    # Seit #238: Summe heißt ehrlich ``verbrauch_k``; ``ist_k`` = Spitzen-Kontext.
    assert zusammen["verbrauch_k"] == 62.6
    assert zusammen["ist_k"] == 31.5
    assert zusammen["sessions"] == 1
    assert zusammen["subagenten"] == 1

    stand = _lauf(
        [sys.executable, str(SKRIPTE / "sessions_stand.py"), SPEC],
        repo,
        _umgebung(tmp_path, wt_basis),
    )
    assert stand.returncode == 0, stand.stdout + stand.stderr
    kopf = next(z for z in stand.stdout.splitlines() if z.startswith("Ticket"))
    assert "Token" in kopf
    zeile = next(z for z in stand.stdout.splitlines() if z.startswith(f"#{TICKET}"))
    assert "31,5k" in zeile, stand.stdout  # Spitzen-Kontext statt Summe (#238)


def test_weg_worktree_fehlt_keine_datei_im_repo(repo: Path, tmp_path: Path) -> None:
    wt_basis = tmp_path / "wt-fehlt"
    ergebnis = _bau(repo, tmp_path, wt_basis)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    hooks = _to_spawn_hooks(tmp_path)
    assert len(hooks) == 3, _hook_ergebnisse(tmp_path)
    assert all(h["code"] == 0 for h in hooks), hooks
    assert not (repo / bau_log.LOG_ORDNER).exists()
    assert not wt_basis.exists()


# --- Hooks stören nie ---------------------------------------------------------


def _hook_cli(repo: Path, befehl: str, eingabe: str, zusatz_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), befehl],
        cwd=str(repo),
        input=eingabe,
        env={**os.environ, **zusatz_env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize("befehl", ["hook-stop", "hook-subagent-stop"])
@pytest.mark.parametrize(
    "eingabe",
    [
        "{kaputt",
        json.dumps({"session_id": "s", "transcript_path": "/gibt/es/nicht.jsonl"}),
        json.dumps({"session_id": "s", "last_assistant_message": None}),
        "",
    ],
    ids=["kaputtes-json", "transkript-fehlt", "nachricht-null", "leer"],
)
def test_hook_endet_immer_mit_exit_0(repo: Path, befehl: str, eingabe: str) -> None:
    ergebnis = _hook_cli(repo, befehl, eingabe, {"TO_SPAWN_TICKET": TICKET})
    assert ergebnis.returncode == 0, ergebnis.stderr


@pytest.mark.parametrize("befehl", ["hook-stop", "hook-subagent-stop"])
def test_hook_mit_kaputtem_transkript_endet_mit_exit_0(repo: Path, tmp_path: Path, befehl: str) -> None:
    transkript = tmp_path / "kaputt.jsonl"
    transkript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "isSidechain": befehl == "hook-subagent-stop",
                "message": {"usage": {"input_tokens": "viel"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ergebnis = _hook_cli(
        repo,
        befehl,
        json.dumps({"session_id": "s", "transcript_path": str(transkript)}),
        {"TO_SPAWN_TICKET": TICKET},
    )
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_hook_mit_fehlendem_log_repo_schreibt_nichts(repo: Path, tmp_path: Path) -> None:
    transkript = tmp_path / "t.jsonl"
    transkript.write_text(
        json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 5}}}) + "\n",
        encoding="utf-8",
    )
    fehlt = tmp_path / "wt" / f"wt-{TICKET}"
    ergebnis = _hook_cli(
        repo,
        "hook-stop",
        json.dumps({"session_id": "s", "transcript_path": str(transkript)}),
        {"TO_SPAWN_TICKET": TICKET, "TO_SPAWN_LOG_REPO": str(fehlt)},
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert not (repo / bau_log.LOG_ORDNER).exists()
    assert not fehlt.exists()


# --- Sammler: Mehrfach-Runden zählen einmal -----------------------------------


def _log(repo: Path, zeilen: list[dict]) -> None:
    ordner = repo / bau_log.LOG_ORDNER
    ordner.mkdir(parents=True, exist_ok=True)
    with (ordner / f"{TICKET}.jsonl").open("a", encoding="utf-8") as fh:
        for zeile in zeilen:
            fh.write(json.dumps({"ticket": TICKET, **zeile}, ensure_ascii=False) + "\n")


def test_zusammenfassung_dedupliziert_je_session(repo: Path) -> None:
    _log(
        repo,
        [
            {"typ": "session_start", "session_id": "a"},
            {"typ": "session_ende", "session_id": "a", "tokens": {"gesamt": 1000}, "dauer_s": 60},
            {"typ": "session_ende", "session_id": "a", "tokens": {"gesamt": 3000}, "dauer_s": 120},
            {"typ": "subagent_ende", "session_id": "x", "tokens": {"gesamt": 500}, "dauer_s": 10},
            {"typ": "subagent_ende", "session_id": "x", "tokens": {"gesamt": 700}, "dauer_s": 20},
            {"typ": "session_ende", "session_id": "b", "tokens": {"gesamt": 2000}, "dauer_s": 30},
            {"typ": "session_ende", "tokens": {"gesamt": 100}, "dauer_s": 5},
            {"typ": "session_ende", "tokens": {"gesamt": 100}, "dauer_s": 5},
        ],
    )
    z = bau_log.zusammenfassung(repo, TICKET)
    # Seit #238 heißt die Summe ``verbrauch_k``; ohne ``kontext`` kein ``ist_k``.
    assert z["verbrauch_k"] == 5.9  # 3000 + 2000 + 100 + 100 + 700
    assert z["ist_k"] is None
    assert z["dauer_s"] == 160  # 120 + 30 + 5 + 5
    assert z["sessions"] == 4  # a, b und zwei Zeilen ohne Kennung
    assert z["subagenten"] == 1


# --- CLI ``eintrag`` ----------------------------------------------------------


def test_eintrag_cli_schreibt_zusammenfassung(repo: Path, tmp_path: Path) -> None:
    ziel = tmp_path / "wt" / f"wt-{TICKET}"
    ziel.mkdir(parents=True)
    ergebnis = _lauf(
        [
            sys.executable,
            str(CLI),
            "eintrag",
            "--typ",
            "zusammenfassung",
            "--umfang",
            "Hooks verdrahtet, Sammler gebaut.",
            "--schwierigkeiten",
            "Stop feuert je Runde.",
            "--entscheidungen",
            "Jüngste Zeile je Session zählt.",
        ],
        repo,
        {**os.environ, "TO_SPAWN_TICKET": TICKET, "TO_SPAWN_LOG_REPO": str(ziel)},
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert "zusammenfassung" in bau_log.TYPEN
    zeilen = bau_log.lese(ziel, TICKET)
    assert len(zeilen) == 1
    assert zeilen[0]["typ"] == "zusammenfassung"
    assert zeilen[0]["umfang"] == "Hooks verdrahtet, Sammler gebaut."
    assert zeilen[0]["schwierigkeiten"] == "Stop feuert je Runde."
    assert zeilen[0]["entscheidungen"] == "Jüngste Zeile je Session zählt."
    assert not (repo / bau_log.LOG_ORDNER).exists()


def test_eintrag_cli_mit_repo_und_entscheidung(repo: Path, tmp_path: Path) -> None:
    ziel = tmp_path / "anderswo"
    ziel.mkdir()
    ergebnis = _lauf(
        [
            sys.executable,
            str(CLI),
            "eintrag",
            "--ticket",
            "77",
            "--typ",
            "entscheidung",
            "--text",
            "Kein Ausweichen in den Hauptbaum.",
            "--repo",
            str(ziel),
        ],
        repo,
        dict(os.environ),
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    zeilen = bau_log.lese(ziel, "77")
    assert [z["typ"] for z in zeilen] == ["entscheidung"]
    assert zeilen[0]["text"] == "Kein Ausweichen in den Hauptbaum."


def test_eintrag_cli_ohne_log_repo_scheitert_laut(repo: Path, tmp_path: Path) -> None:
    ergebnis = _lauf(
        [sys.executable, str(CLI), "eintrag", "--ticket", TICKET, "--typ", "entscheidung", "--text", "x"],
        repo,
        {**os.environ, "TO_SPAWN_LOG_REPO": str(tmp_path / "gibt-es-nicht")},
    )
    assert ergebnis.returncode != 0
    assert not (repo / bau_log.LOG_ORDNER).exists()


# --- Loop-Prompt ---------------------------------------------------------------

PROMPT_KERN = "to_spawn.py eintrag --ticket {N} --typ zusammenfassung"


def _prompt(datei: Path) -> str:
    return json.loads(datei.read_text(encoding="utf-8"))["prompt_template"]


def test_prompt_verlangt_zusammenfassung_vor_dem_schliessen() -> None:
    prompt = _prompt(SKILL / "repo-scripts" / "_default.json")
    assert PROMPT_KERN in prompt
    assert "docs/agents/bau_log/{N}.jsonl" in prompt
    assert prompt.index(PROMPT_KERN) < prompt.index("Issue mit Beweis schließen.")
    assert "`" not in prompt


_PLATZHALTER = re.compile(r"\{[A-Z_]+\}")


@pytest.mark.skipif(not REPO_DEFAULT.is_file(), reason="Repo-Kopie des Loop-Prompts fehlt")
def test_prompt_repo_kopie_platzhalter_teilmenge_skill_bleibt_neutral() -> None:
    """seit #257 nicht mehr identisch: Skill-Vorlage repo-neutral (B1), DuoPlus-Kopie
    behält ihre Deploy-Sätze. Beide teilen ``PROMPT_KERN``, die Platzhalter der
    Repo-Kopie sind eine Teilmenge der Skill-Platzhalter, die Skill-Vorlage
    nennt kein DuoPlus-Wort."""
    skill_prompt = _prompt(SKILL / "repo-scripts" / "_default.json")
    repo_prompt = _prompt(REPO_DEFAULT)
    assert PROMPT_KERN in skill_prompt
    assert PROMPT_KERN in repo_prompt
    skill_platzhalter = set(_PLATZHALTER.findall(skill_prompt))
    repo_platzhalter = set(_PLATZHALTER.findall(repo_prompt))
    assert repo_platzhalter <= skill_platzhalter, (repo_platzhalter, skill_platzhalter)
    assert "duoplus" not in skill_prompt.lower()
    assert "clawy-vps" not in skill_prompt.lower()
