"""#386: Pflicht-Zeilen „Selbst entschieden“ und „Offene Befunde“ im Skill-Start-Text.

Aus A1 (#274): Die Pflicht-Zeilen im Schluss-Kommentar eines Tickets gehören als
allgemeine Bau-Regel in die Skill-Vorlage ``repo-scripts/_default.json``, nicht
in den Repo-Zusatz eines einzelnen Repos.

Weg-Test: ``bau.py <N> --dry-run --print-prompt`` läuft als echter Prozess gegen
ein Temp-Repo ohne Repo-Zusatz und liefert beide Pflicht-Zeilen im fertigen Prompt.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
VORLAGE = SKILL / "repo-scripts" / "_default.json"
BAU = SKILL / "skripte" / "bau.py"

ZEILE_ENTSCHIEDEN = "Selbst entschieden: …"
ZEILE_BEFUNDE = "Offene Befunde: …"


def _repo_ohne_zusatz(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    manifeste = repo / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            "https://github.com/test-org/test-repo.git",
        ],
        check=True,
    )
    (manifeste / "spec-9.json").write_text(
        json.dumps({"spec": 9, "tickets": {"7": {"title": "Titel"}}}),
        encoding="utf-8",
    )
    return repo


def test_vorlage_verlangt_pflichtzeilen_im_schluss_kommentar() -> None:
    """Die eine Skill-Vorlage nennt beide Pflicht-Zeilen direkt am Issue-Schluss."""
    vorlage = json.loads(VORLAGE.read_text(encoding="utf-8"))
    text: str = vorlage["prompt_template"]

    assert text.count(ZEILE_ENTSCHIEDEN) == 1
    assert text.count(ZEILE_BEFUNDE) == 1
    assert "Schluss-Kommentar" in text
    assert "eintrag --entscheidungen" in text
    assert "`" not in text

    zusammenfassung = text.index("to_spawn.py eintrag --ticket {N} --typ zusammenfassung")
    schliessen = text.index("Issue mit Beweis schließen.")
    entschieden = text.index(ZEILE_ENTSCHIEDEN)
    befunde = text.index(ZEILE_BEFUNDE)
    aufraeumen = text.index("to_spawn.py aufraeumen")
    assert zusammenfassung < schliessen < entschieden < befunde < aufraeumen


def test_bau_print_prompt_enthaelt_schluss_kommentar_ohne_repo_zusatz(
    tmp_path: Path,
) -> None:
    """Ein fremdes Repo ohne ``start_text.zusatz_datei`` erhält die Pflicht-Zeilen aus dem Skill."""
    repo = _repo_ohne_zusatz(tmp_path)
    env = {**os.environ, "TO_SPAWN_REPO": str(repo), "TMPDIR": str(tmp_path)}
    env.pop("BAU_AUFTRAG", None)
    lauf = subprocess.run(
        [sys.executable, str(BAU), "7", "--dry-run", "--print-prompt"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=repo,
        timeout=120,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stderr
    assert ZEILE_ENTSCHIEDEN in lauf.stdout
    assert ZEILE_BEFUNDE in lauf.stdout
    assert "eintrag --entscheidungen" in lauf.stdout
    assert "Regeln dieses Repos" not in lauf.stdout
