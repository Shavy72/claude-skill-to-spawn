"""#274 P1: der Start-Text entsteht nur aus der Skill-Vorlage + dem Repo-Zusatz.

Beobachtet: ``bau.py`` las ``docs/agents/manifests/_default.json`` des Ziel-Repos. Diese
Kopie fror den Regeltext ein (z. B. alte Blocker-Regel ``blocked_by`` statt
``to_spawn.py blocker``), jede Regeländerung im Skill kam dort nie an. Jetzt gilt nur
``repo-scripts/_default.json`` des Skills; Repo-Eigenes kommt über
``start_text.zusatz_datei`` als Abschnitt ``{REPO_ZUSATZ}`` dazu. Eine liegende Repo-Kopie
wird ignoriert und laut gemeldet.

Weg-Test: ``bau.py <N> --dry-run --print-prompt`` läuft als echter Prozess gegen ein
Temp-Repo (git, Manifest, Konfig auf der Platte) — kein Mock von ``load_default``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BAU = Path(__file__).resolve().parent.parent / "skripte" / "bau.py"

ALTE_KOPIE = "ALTE KOPIE: Blocker per gh api blocked_by prüfen. Ticket #{N}."
ZUSATZ = "Staging-Grundsatz: jedes Ticket geht zuerst auf Staging."
KOPF = "## Regeln dieses Repos (ergänzen, ersetzen keine Regel oben)"


def _repo(tmp_path: Path, *, alte_kopie: bool, zusatz_datei: str | None, zusatz_anlegen: bool) -> Path:
    repo = tmp_path / "repo"
    manifeste = repo / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/test-org/test-repo.git"],
        check=True,
    )
    (manifeste / "spec-9.json").write_text(
        json.dumps({"spec": 9, "tickets": {"7": {"title": "Titel"}}}), encoding="utf-8"
    )
    if alte_kopie:
        (manifeste / "_default.json").write_text(
            json.dumps({"prompt_template": ALTE_KOPIE, "core_skills": []}), encoding="utf-8"
        )
    if zusatz_datei is not None:
        (repo / ".to-spawn").mkdir()
        (repo / ".to-spawn" / "config.json").write_text(
            json.dumps({"start_text": {"zusatz_datei": zusatz_datei}}), encoding="utf-8"
        )
        if zusatz_anlegen:
            (repo / zusatz_datei).write_text(ZUSATZ + "\n", encoding="utf-8")
    return repo


def _lauf(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "TO_SPAWN_REPO": str(repo), "TMPDIR": str(tmp_path)}
    env.pop("BAU_AUFTRAG", None)
    return subprocess.run(
        [sys.executable, str(BAU), "7", "--dry-run", "--print-prompt"],
        capture_output=True,
        text=True,
        env=env,
        cwd=repo,
        timeout=120,
    )


def test_alte_kopie_wird_ignoriert_skill_text_plus_zusatz(tmp_path: Path) -> None:
    repo = _repo(tmp_path, alte_kopie=True, zusatz_datei="docs/agents/bau_zusatz.md", zusatz_anlegen=True)
    lauf = _lauf(repo, tmp_path)
    assert lauf.returncode == 0, lauf.stderr
    assert "to_spawn.py blocker" in lauf.stdout
    assert "blocked_by" not in lauf.stdout
    assert "ALTE KOPIE" not in lauf.stdout
    assert KOPF in lauf.stdout
    assert ZUSATZ in lauf.stdout
    assert "{REPO_ZUSATZ}" not in lauf.stdout
    # Warnung genau einmal, mit Umzugs-Hinweis.
    assert lauf.stderr.count("wird ignoriert") == 1, lauf.stderr
    assert "start_text.zusatz_datei" in lauf.stderr


def test_fehlende_zusatz_datei_warnt_und_startet(tmp_path: Path) -> None:
    repo = _repo(tmp_path, alte_kopie=False, zusatz_datei="docs/agents/bau_zusatz.md", zusatz_anlegen=False)
    lauf = _lauf(repo, tmp_path)
    assert lauf.returncode == 0, lauf.stderr
    assert "to_spawn.py blocker" in lauf.stdout
    assert KOPF not in lauf.stdout
    assert "{REPO_ZUSATZ}" not in lauf.stdout
    assert "bau_zusatz.md" in lauf.stderr
    assert "wird ignoriert" not in lauf.stderr


def test_ohne_zusatz_kein_abschnittskopf(tmp_path: Path) -> None:
    repo = _repo(tmp_path, alte_kopie=False, zusatz_datei=None, zusatz_anlegen=False)
    lauf = _lauf(repo, tmp_path)
    assert lauf.returncode == 0, lauf.stderr
    assert "to_spawn.py blocker" in lauf.stdout
    assert KOPF not in lauf.stdout
    assert "Regeln dieses Repos" not in lauf.stdout
    assert "{REPO_ZUSATZ}" not in lauf.stdout
    assert "wird ignoriert" not in lauf.stderr
