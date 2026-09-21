"""Echter Weg-Test OHNE Sofort-Schalter für den Wächter (duoplus-management#213).

Läuft nur mit ``TO_SPAWN_WEGTEST_GH=1`` — legt echte Issues im öffentlichen Repo
``Shavy72/claude-skill-to-spawn`` an. Anders als ``test_waechter_213_weg.py``
schaltet hier KEIN Test-Schalter die Zeitgrenzen ab (``TO_SPAWN_WAECHTER_SOFORT``
ist entfernt, das Modul steht nicht in ``conftest._SOFORT_MODULE``). Die Karenz
kommt aus der echten Repo-Konfig ``waechter.karenz_minuten: 0``; der Ausgangsstand
entsteht echt durch einen ersten Tick, solange das Kind noch offen ist.

Ablauf: Spec + Kind + Sub-Issue-Kante → Wegwerf-Repo mit Konfig und passender
Belegseite ``docs/verify-hard/<Kind>_beleg.md`` (damit nur ``commit_ohne_nummer``
greift) → erster Tick (Kind offen) → Kind schließen + Commit ohne Nummer → zweiter
Tick → Kind wieder OPEN, Kommentar nennt ``commit_ohne_nummer``, nicht
``beweis_fehlt`` → noch einmal schließen → dritter Tick → Kind wieder OPEN, zweiter
Kommentar (Entscheidung 21.09.: keine Einmal-Sperre). Zum Schluss werden beide
Issues geschlossen.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
GH_REPO = "Shavy72/claude-skill-to-spawn"
log = logging.getLogger("test_waechter_213_weg_echt")

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


def _commit_push(repo: Path, betreff: str, dateien: dict[str, str]) -> None:
    for name, inhalt in dateien.items():
        pfad = repo / name
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(inhalt, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "--allow-empty", "-m", betreff)
    _git(repo, "push", "-q", "origin", "HEAD:master")


def _tick(
    repo: Path, spec: int, tmp_path: Path, umgebung: dict[str, str]
) -> subprocess.CompletedProcess[str]:
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
        timeout=180,
        check=False,
    )
    print(ergebnis.stdout)
    print(ergebnis.stderr, file=sys.stderr)
    return ergebnis


def test_capo_echt_ohne_sofort_schalter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TO_SPAWN_WAECHTER_SOFORT", raising=False)
    umgebung = {
        k: v
        for k, v in os.environ.items()
        if k not in ("TO_SPAWN_GH_STUB", "TO_SPAWN_WAECHTER_SOFORT")
    }
    zustand_ordner = tmp_path / "waechter"
    umgebung.update(
        {"TO_SPAWN_WAECHTER_ORDNER": str(zustand_ordner), "TO_SPAWN_REPO": ""}
    )
    assert "TO_SPAWN_WAECHTER_SOFORT" not in umgebung

    spec = _neues_issue(
        "Weg-Test #213 echt — Spec (wird geschlossen)",
        "Wegwerf-Spec für den echten Wächter-Weg-Test ohne Sofort-Schalter.",
    )
    kind = _neues_issue(
        "Weg-Test #213 echt — Kind (wird geschlossen)",
        "Wegwerf-Kind für den echten Wächter-Weg-Test ohne Sofort-Schalter.",
    )
    print(f"Weg-Test #213 echt: Spec #{spec}, Kind #{kind}")
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
        konfig = {"waechter": {"karenz_minuten": 0}}  # echte Einstellung, kein Schalter
        _commit_push(
            repo,
            "chore: Wegwerf-Repo mit Konfig und Belegseite",
            {
                ".to-spawn/config.json": json.dumps(konfig) + "\n",
                f"docs/verify-hard/{kind}_beleg.md": "Beleg für den Weg-Test\n",
            },
        )
        umgebung["TO_SPAWN_REPO"] = str(repo)

        # Tick 1: Kind ist noch OFFEN → setzt den Ausgangsstand.
        erste = _tick(repo, spec, tmp_path, umgebung)
        assert erste.returncode == 0, erste.stdout + erste.stderr
        assert "SOFORT" not in erste.stdout
        zustand_dateien = list(zustand_ordner.glob(f"*_{spec}.json"))
        assert len(zustand_dateien) == 1, zustand_dateien
        zustand = json.loads(zustand_dateien[0].read_text(encoding="utf-8"))
        print(
            f"Zustand nach Tick 1: erster_tick={zustand.get('erster_tick')}, "
            f"ausgangsstand={zustand.get('ausgangsstand')}"
        )
        assert zustand["ausgangsstand"] == {str(kind): ""}  # beim ersten Tick offen

        # Kind schließen, dann ein Commit OHNE Ticket-Nummer.
        _gh(
            "issue",
            "close",
            str(kind),
            "--repo",
            GH_REPO,
            "--comment",
            "Weg-Test #213 echt: absichtlich ohne Commit-Nummer geschlossen.",
        )
        _commit_push(repo, "feat: Bauteil ohne Ticket-Nummer", {"a.txt": "x\n"})
        closed_at = _gh("api", f"repos/{GH_REPO}/issues/{kind}", "--jq", ".closed_at")
        abstand = (
            datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
            - datetime.fromisoformat(zustand["erster_tick"])
        ).total_seconds()
        print(
            f"Uhr-Vergleich: closed_at (GitHub) {closed_at} − erster_tick (lokal) "
            f"{zustand['erster_tick']} = {abstand:+.0f} s"
        )

        # Tick 2: Schließen nach dem ersten Tick, Karenz 0 → Regel greift sofort.
        zweite = _tick(repo, spec, tmp_path, umgebung)
        assert zweite.returncode == 0, zweite.stdout + zweite.stderr
        assert "prüfe später" not in zweite.stdout
        assert "Ausgangsstand" not in zweite.stdout

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
        print(f"Kind #{kind} nach Tick 2: {daten['state']}")
        assert daten["state"] == "OPEN"
        waechter = [
            c["body"] for c in daten["comments"] if c["body"].startswith("Wächter:")
        ]
        print(f"Wächter-Kommentar: {waechter}")
        assert len(waechter) == 1
        assert "commit_ohne_nummer" in waechter[0]
        assert "beweis_fehlt" not in waechter[0]

        # Entscheidung 21.09. (#213): zweites Schließen mit demselben Verstoß
        # öffnet wieder — früher gab es dafür nur einen Kommentar.
        _gh(
            "issue",
            "close",
            str(kind),
            "--repo",
            GH_REPO,
            "--comment",
            "Weg-Test #213 echt: zweites Mal geschlossen, Verstoß nicht behoben.",
        )
        dritte = _tick(repo, spec, tmp_path, umgebung)
        assert dritte.returncode == 0, dritte.stdout + dritte.stderr
        assert "schon einmal wieder geöffnet" not in dritte.stdout
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
        print(f"Kind #{kind} nach dem zweiten Schließen: {daten['state']}")
        waechter = [
            c["body"] for c in daten["comments"] if c["body"].startswith("Wächter:")
        ]
        print(f"Wächter-Kommentare: {len(waechter)}")
        assert daten["state"] == "OPEN"
        assert len(waechter) == 2
        assert all("commit_ohne_nummer" in k for k in waechter)
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
                    "Weg-Test #213 echt — aufgeräumt.",
                ],
                capture_output=True,
                check=False,
            )
