"""Hausmeister #135: nie den Worktree eines laufenden Helfers löschen.

Vorfall 21.11.: ``~/wt/skill-103`` (Skill-Repo) wurde als App-Ticket #103 gelesen (uralt zu)
und gelöscht, obwohl ein Helfer darin arbeitete und sein Pull-Request offen war.
Regel an einer Stelle (:func:`hausmeister._regeln`): die Nummer aus Ordner/Zweig zählt nur,
wenn der Ordner zum Ticket-Repo gehört (``origin``); sonst darf er nur weg, wenn sein Kopf
im Hauptzweig von origin liegt, der Baum sauber ist, nichts frisch ist und kein
Pull-Request offen ist. Ein offener Pull-Request zum Zweig hält jeden Ordner.

Echte Git-Repos (bare-„origin“ je Repo über ``insteadOf``), echte Prozesse; gestellt ist nur
``gh`` (Ticket-Zustand, offene Pull-Requests).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    Welt,
    git,
    welt,
)

from to_spawn import config, hausmeister

SKILL_ORIGIN = "https://github.com/Shavy72/claude-skill-to-spawn"
TAG = 24 * 3600.0


@pytest.fixture
def skill(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Zweites echtes Repo (Skill-Repo, Hauptzweig ``main``) neben dem App-Repo der Welt."""
    bare = welt.tmp / "skill.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    gitconfig = welt.tmp / "gitconfig"
    with gitconfig.open("a", encoding="utf-8") as f:
        f.write(f'[url "{bare.as_posix()}"]\n\tinsteadOf = {SKILL_ORIGIN}\n')
    saat = welt.tmp / "skill-saat"
    subprocess.run(["git", "init", "-q", "-b", "main", str(saat)], check=True)
    (saat / "README.md").write_text("skill\n", encoding="utf-8")
    git(saat, "add", "README.md")
    git(saat, "commit", "-q", "-m", "start")
    git(saat, "push", "-q", str(bare), "HEAD:refs/heads/main")
    haupt = welt.tmp / "haupt" / "claude-skill-to-spawn"
    git(welt.tmp, "clone", "-q", SKILL_ORIGIN, str(haupt))
    prs = welt.tmp / "prs.json"
    prs.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GH_PRS", str(prs))
    # #235: die Vorgabe gibt dem Skill-Repo jetzt eigene Tickets; der #135-Fall ist eine
    # Maschinen-Liste, deren Skill-Eintrag noch auf das App-Repo zeigt (alter Server-Stand).
    datei = config.maschine_datei()  # conftest: je Test eigene Datei unter tmp_path
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(
        json.dumps(
            {
                "repos": [
                    {"slug": "Shavy72/duoplus-management", "pfad": str(welt.haupt), "ticket_repo": ""},
                    {
                        "slug": "Shavy72/claude-skill-to-spawn",
                        "pfad": str(haupt),
                        "ticket_repo": "Shavy72/duoplus-management",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return haupt


def _skill_wt(welt: Welt, haupt: Path, nr: int, *, gemergt: bool, gepusht: bool = True) -> Path:
    wt = welt.heim / "wt" / f"skill-{nr}"
    zweig = f"l4/{nr}-arbeit"
    git(haupt, "worktree", "add", "-q", "-b", zweig, str(wt), "origin/main")
    (wt / "arbeit.txt").write_text("arbeit\n", encoding="utf-8")
    git(wt, "add", "arbeit.txt")
    git(wt, "commit", "-q", "-m", "arbeit")
    if gepusht:
        git(wt, "push", "-q", "-u", "origin", zweig)
    if gemergt:
        git(wt, "push", "-q", "origin", "HEAD:refs/heads/main")
    return wt


def _pr_offen(welt: Welt, zweig: str) -> None:
    datei = welt.tmp / "prs.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten[zweig] = [{"number": 42}]
    datei.write_text(json.dumps(daten), encoding="utf-8")


# --- Vorfall: Nummer aus dem falschen Repo -----------------------------------------


def test_skill_ordner_mit_fremder_alter_nummer_bleibt_solange_nicht_gemergt(welt: Welt, skill: Path) -> None:
    wt = _skill_wt(welt, skill, 103, gemergt=False)
    welt.ticket(103, "CLOSED", 24 * 400)  # App-Ticket #103, uralt zu
    welt.jetzt += 30 * TAG
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt", r.grund
    assert wt.is_dir()


def test_skill_ordner_gemergt_sauber_alt_ohne_pr_geht_weg(welt: Welt, skill: Path) -> None:
    wt = _skill_wt(welt, skill, 104, gemergt=True)
    welt.ticket(104, "OPEN")  # App-Ticket #104 offen: gehört nicht zu diesem Ordner
    welt.jetzt += 30 * TAG
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gelöscht", r.grund
    assert not wt.exists()


def test_skill_ordner_gemergt_aber_frisch_bleibt(welt: Welt, skill: Path) -> None:
    wt = _skill_wt(welt, skill, 105, gemergt=True)
    welt.ticket(105, "CLOSED", 24 * 400)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "frisch" in r.grund, r.grund
    assert wt.is_dir()


def test_skill_ordner_mit_commit_auf_keinem_remote_bleibt(welt: Welt, skill: Path) -> None:
    wt = _skill_wt(welt, skill, 106, gemergt=False, gepusht=False)
    welt.ticket(106, "CLOSED", 24 * 400)
    welt.jetzt += 30 * TAG
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt", r.grund
    assert wt.is_dir()
    assert "sicherung/106" not in git(welt.tmp / "skill.git", "branch", "--list")


def test_skill_ordner_mit_geaenderter_datei_bleibt(welt: Welt, skill: Path) -> None:
    wt = _skill_wt(welt, skill, 107, gemergt=True)
    (wt / "arbeit.txt").write_text("noch dran\n", encoding="utf-8")
    welt.ticket(107, "CLOSED", 24 * 400)
    welt.jetzt += 30 * TAG
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt", r.grund
    assert wt.is_dir()


# --- Offener Pull-Request, laufender Prozess ---------------------------------------


def test_offener_pull_request_haelt_app_worktree_trotz_altem_ticket(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt = welt.worktree(1350)
    welt.ticket(1350, "CLOSED", 48)
    (welt.tmp / "prs.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GH_PRS", str(welt.tmp / "prs.json"))
    _pr_offen(welt, "ticket/1350-test")
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "Pull-Request" in r.grund, r.grund
    assert wt.is_dir()


def test_offener_pull_request_haelt_gemergten_skill_ordner(welt: Welt, skill: Path) -> None:
    wt = _skill_wt(welt, skill, 155, gemergt=True)
    _pr_offen(welt, "l4/155-arbeit")
    welt.ticket(155, "CLOSED", 24 * 400)
    welt.jetzt += 30 * TAG
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "Pull-Request" in r.grund, r.grund
    assert wt.is_dir()


def test_pull_request_nicht_abfragbar_bleibt(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = welt.worktree(1351)
    welt.ticket(1351, "CLOSED", 48)
    kaputt = welt.tmp / "prs-kaputt.json"
    kaputt.write_text("kein json", encoding="utf-8")
    monkeypatch.setenv("GH_PRS", str(kaputt))
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fehler, r.grund
    assert wt.is_dir()


def test_prozess_im_git_worktree_haelt_ihn(welt: Welt) -> None:
    wt = welt.worktree(1352)
    welt.ticket(1352, "CLOSED", 48)
    proc = subprocess.Popen(["sleep", "60"], cwd=wt)
    try:
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and "Prozess" in r.grund, r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait()


# --- Prüf-Test: Präfix-Kollision ----------------------------------------------------


def test_praefix_kollision_nummer_zaehlt_nur_im_ticket_repo() -> None:
    """Jedes Ordner-Präfix darf beliebige Repos treffen: die Nummer zählt nur, wenn der
    ``origin`` des Ordners selbst das Ticket-Repo ist (Vorgabe seit #235: jedes Repo sein eigenes)."""
    for eintrag in config.MASCHINE_DEFAULTS["repos"]:
        slug = eintrag["slug"]
        ticket_repo = hausmeister._ticket_repo_fuer(slug) or ""
        assert hausmeister._nummer_zaehlt(slug) is (slug.lower() == ticket_repo.lower()), slug
    assert hausmeister._nummer_zaehlt("Shavy72/claude-skill-to-spawn") is True  # #235: Skill-Issues
    assert hausmeister._nummer_zaehlt("Shavy72/fremd") is False
    assert hausmeister._nummer_zaehlt("") is False
