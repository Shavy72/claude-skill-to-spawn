"""Weg-Test ohne Attrappen für den Wächter (duoplus-management#213).

Läuft nur mit ``TO_SPAWN_WEGTEST_GH=1`` — er legt echte Issues im öffentlichen
Repo ``Shavy72/claude-skill-to-spawn`` an (Spec + Kind, echte Sub-Issue-Kante),
schließt das Kind, baut ein echtes Wegwerf-Git-Repo mit einem Commit OHNE
Ticket-Nummer und lässt ``skripte/capo.py`` echt gegen GitHub laufen. Erwartet:
das Kind ist danach wieder offen und trägt den Wächter-Kommentar. Zum Schluss
werden beide Issues mit „Weg-Test #213“ geschlossen.

Kein Mail-Versand: der Wegwerf-Repo hat keinen ``mail.befehl``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
GH_REPO = "Shavy72/claude-skill-to-spawn"
log = logging.getLogger("test_waechter_213_weg")

pytestmark = pytest.mark.skipif(
    os.environ.get("TO_SPAWN_WEGTEST_GH") != "1",
    reason="Weg-Test gegen echtes GitHub nur mit TO_SPAWN_WEGTEST_GH=1",
)


def _gh(*args: str) -> str:
    fertig = subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return fertig.stdout.strip()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


def _neues_issue(titel: str, text: str) -> int:
    url = _gh("issue", "create", "--repo", GH_REPO, "--title", titel, "--body", text)
    return int(url.rstrip("/").rsplit("/", 1)[1])


def test_capo_oeffnet_echtes_issue_wieder(tmp_path: Path) -> None:
    spec = _neues_issue(
        "Weg-Test #213 — Spec (wird geschlossen)",
        "Wegwerf-Spec für den Wächter-Weg-Test.",
    )
    kind = _neues_issue(
        "Weg-Test #213 — Kind (wird geschlossen)",
        "Wegwerf-Kind für den Wächter-Weg-Test.",
    )
    print(f"Weg-Test #213: Spec #{spec}, Kind #{kind}")
    try:
        kind_id = _gh("api", f"repos/{GH_REPO}/issues/{kind}", "--jq", ".id")
        _gh(
            "api",
            "-X",
            "POST",
            f"repos/{GH_REPO}/issues/{spec}/sub_issues",
            "-F",
            f"sub_issue_id={kind_id}",
        )
        _gh(
            "issue",
            "close",
            str(kind),
            "--repo",
            GH_REPO,
            "--comment",
            "Weg-Test #213: absichtlich ohne Commit-Nummer geschlossen.",
        )

        fern = tmp_path / "fern.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "master", str(fern)], check=True
        )
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "master")
        _git(repo, "config", "user.email", "test@example.invalid")
        _git(repo, "config", "user.name", "Test")
        _git(repo, "remote", "add", "origin", str(fern))
        (repo / "a.txt").write_text("ohne Nummer\n", encoding="utf-8")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-q", "-m", "feat: Bauteil ohne Ticket-Nummer")
        _git(repo, "push", "-q", "origin", "HEAD:master")

        umgebung = {k: v for k, v in os.environ.items() if k != "TO_SPAWN_GH_STUB"}
        umgebung.update(
            {
                "TO_SPAWN_REPO": str(repo),
                "TO_SPAWN_WAECHTER_ORDNER": str(tmp_path / "waechter"),
            }
        )
        ergebnis = subprocess.run(
            [
                sys.executable,
                str(SKILL / "skripte" / "capo.py"),
                str(spec),
                "--gh-repo",
                GH_REPO,
                "--wt-basis",
                str(tmp_path / "wt"),
            ],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=umgebung,
            timeout=120,
            check=False,
        )
        print(ergebnis.stdout)
        print(ergebnis.stderr, file=sys.stderr)
        assert ergebnis.returncode == 0

        daten = json.loads(
            _gh(
                "issue",
                "view",
                str(kind),
                "--repo",
                GH_REPO,
                "--json",
                "state,comments",
            )
        )
        print(f"Kind #{kind} nach capo: {daten['state']}")
        assert daten["state"] == "OPEN"
        waechter = [
            c["body"]
            for c in daten["comments"]
            if c["body"].startswith("Wächter: commit_ohne_nummer")
        ]
        print(f"Wächter-Kommentar: {waechter[:1]}")
        assert len(waechter) == 1
    finally:
        for nummer in (kind, spec):
            subprocess.run(
                [
                    "gh",
                    "issue",
                    "close",
                    str(nummer),
                    "--repo",
                    GH_REPO,
                    "--comment",
                    "Weg-Test #213 — aufgeräumt.",
                ],
                capture_output=True,
                check=False,
            )
