"""Bau-Sessions dürfen den Review-Skill nie abschalten (Handoff 2026-09-24, Review-System Runde 5).

Beleg: Transkript c97ddc47 (#314) — der globale Stop-Hook ``hooks/review/stop.py`` fordert
„Skill review-dirigent ausführen“, der Aufruf scheitert mit „Skill review-dirigent is
disabled for model invocation in skillOverrides settings“, weil ``bau.py`` jeden Skill
außerhalb von ``core_skills`` auf ``off`` setzt. Folge: Review nie belegt, beim nächsten
Stop „Mensch nötig“ (``mensch_noetig.jsonl``, grund ``review_offen``).

Weg-Test: echter ``bau.py --dry-run`` in einem Wegwerf-Repo, geprüft wird die geschriebene
Session-``settings.json`` (keine Attrappen).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
_REPO_ENV = os.environ.get("TO_SPAWN_REPO")
DUOPLUS_DEFAULT = (
    Path(_REPO_ENV or "C:/Users/d4veg/Desktop/DuoPlus/duoplus-management")
    / "docs"
    / "agents"
    / "manifests"
    / "_default.json"
)
#: Skills, die der Review-Stop-Hook bzw. review-dirigent per Skill-Werkzeug aufruft.
REVIEW_SKILLS = ("review-dirigent", "code-review", "security-review", "sentry-security-review")


def _repo(tmp_path: Path, default: Path, skills: list[str] | None = None) -> Path:
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=arbeit, check=True)
    manifeste = arbeit / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    ticket: dict[str, object] = {"title": "Wegwerf", "schaetzung_k": 120, "umfang": "Kern bauen."}
    if skills is not None:
        ticket["skills"] = skills
    (manifeste / "spec-900.json").write_text(
        json.dumps({"spec": 900, "tickets": {"901": ticket}}, ensure_ascii=False), encoding="utf-8"
    )
    shutil.copy2(default, manifeste / "_default.json")
    return arbeit


def _dry_run_settings_pfad(repo: Path) -> Path:
    umgebung = {k: v for k, v in os.environ.items() if k != "TO_SPAWN_REPO"}
    ergebnis = subprocess.run(
        [sys.executable, str(SKRIPTE / "bau.py"), "901", "--dry-run"],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    treffer = re.search(r"Temp: (.+)", ausgabe)
    assert treffer, ausgabe
    return Path(treffer.group(1).strip()) / "settings.json"


def _settings_aus_dry_run(repo: Path) -> dict:
    pfad = _dry_run_settings_pfad(repo)
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(pfad.parent, ignore_errors=True)


@pytest.mark.parametrize(
    "default",
    [SKILL / "repo-scripts" / "_default.json", DUOPLUS_DEFAULT],
    ids=["vorlage", "duoplus"],
)
def test_review_skills_in_bau_session_an(tmp_path: Path, default: Path) -> None:
    if not default.is_file():
        pytest.skip(f"{default} fehlt")
    overrides = _settings_aus_dry_run(_repo(tmp_path, default))["skillOverrides"]
    aus = {name: overrides.get(name) for name in REVIEW_SKILLS if overrides.get(name) != "on"}
    assert not aus, f"Review-Skills in Bau-Session nicht an: {aus}"


def test_manifest_kann_review_skills_nicht_abwaehlen(tmp_path: Path) -> None:
    """Auch ein Ticket mit schmaler Skill-Liste bekommt die Review-Skills."""
    repo = _repo(tmp_path, SKILL / "repo-scripts" / "_default.json", skills=["loop"])
    overrides = _settings_aus_dry_run(repo)["skillOverrides"]
    assert all(overrides.get(name) == "on" for name in REVIEW_SKILLS), overrides


def test_temp_ordner_je_start_eindeutig_und_erkannt(tmp_path: Path) -> None:
    """Zwei Starts desselben Tickets bekommen je einen eigenen Ordner (``mkdtemp``), und
    ``sessions_stand`` erkennt den Pfad weiter als Bau-Session von Ticket 901."""
    sys.path.insert(0, str(SKRIPTE))
    try:
        import sessions_stand
    finally:
        sys.path.remove(str(SKRIPTE))
    repo = _repo(tmp_path, SKILL / "repo-scripts" / "_default.json")
    erster, zweiter = _dry_run_settings_pfad(repo), _dry_run_settings_pfad(repo)
    try:
        assert erster.parent != zweiter.parent
        assert erster.is_file() and zweiter.is_file()
        for pfad in (erster, zweiter):
            treffer = sessions_stand.VERWAIST.search(str(pfad))
            assert treffer and treffer.group(1) == "901", pfad
    finally:
        shutil.rmtree(erster.parent, ignore_errors=True)
        shutil.rmtree(zweiter.parent, ignore_errors=True)
