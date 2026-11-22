"""Hausmeister #176: das Ticket-Repo gilt je Ordner, nicht je Maschine.

Lastenheft L4/M14: Sicher Wegwerfbares (Ticket zu, kein Prozess, nichts Frisches) räumt der
Hausmeister von selbst — auch wenn auf der Maschine mehrere Repos mit eigenen Tickets
eingetragen sind. Ein Ordner, dessen Ticket-Repo sich nicht bestimmen lässt, bleibt mit Grund
(fail-closed, nie über den „fremd, aber fertig“-Weg weg); alle anderen werden trotzdem geprüft.
Eine gleiche Nummer im anderen Repo darf nie zur Verwechslung führen.

Echte Git-Repos (bare-„origin“ je Repo über ``insteadOf``), echte Prozesse; gestellt ist nur
``gh`` — hier über die Test-Tür ``TO_SPAWN_GH_STUB`` mit einer Attrappe, die ``-R <repo>``
beachtet (Ticket-Zustand je Repo und Nummer, offene Pull-Requests).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    ORIGIN,
    Welt,
    git,
    iso,
    welt,
)

from to_spawn import hausmeister

APP = "Shavy72/duoplus-management"
SKILL = "Shavy72/claude-skill-to-spawn"
WEGWERF = "Shavy72/wegwerf-probe"
TAG = 24 * 3600.0
STUNDE = 3600.0

#: gh-Attrappe mit Repo: ``gh issue view <nr> -R <repo>`` liest ``GH_TICKETS_JE_REPO``
#: (``{"<repo>": {"<nr>": {...}}}``); ohne ``-R`` oder unbekannt ⇒ Exit 1 (nicht abfragbar).
GH_JE_REPO_PY = """
import json, os, sys
a = sys.argv[1:]
if a[:2] == ["issue", "view"]:
    repo = a[a.index("-R") + 1] if "-R" in a else ""
    daten = json.load(open(os.environ["GH_TICKETS_JE_REPO"], encoding="utf-8"))
    e = daten.get(repo.lower(), {}).get(a[2])
    if e is None:
        sys.exit(1)
    print(json.dumps(e))
    sys.exit(0)
if a[:2] == ["pr", "list"]:
    print("[]")
    sys.exit(0)
sys.exit(1)
"""


@pytest.fixture(autouse=True)
def eigenes_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))


class Repos:
    """Zweites und drittes echtes Repo neben dem App-Repo der Welt, Tickets je Repo."""

    def __init__(self, welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
        self.welt = welt
        self.haupt: dict[str, Path] = {APP: welt.haupt}
        self.hauptzweig: dict[str, str] = {APP: "master"}
        self.tickets = welt.tmp / "tickets-je-repo.json"
        self.tickets.write_text("{}", encoding="utf-8")
        stub = welt.tmp / "gh_je_repo.py"
        stub.write_text(GH_JE_REPO_PY, encoding="utf-8")
        monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
        monkeypatch.setenv("GH_TICKETS_JE_REPO", str(self.tickets))
        # Platz reichlich: keine Platznot-Reihenfolge, die 24-h-Regel gilt
        monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "0.9")

    def repo(self, slug: str) -> Path:
        """Echtes Repo ``slug`` (bare + Hauptklon, Hauptzweig ``main``)."""
        name = slug.split("/")[1]
        bare = self.welt.tmp / f"{name}.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        with (self.welt.tmp / "gitconfig").open("a", encoding="utf-8") as f:
            f.write(f'[url "{bare.as_posix()}"]\n\tinsteadOf = https://github.com/{slug}\n')
        saat = self.welt.tmp / f"{name}-saat"
        subprocess.run(["git", "init", "-q", "-b", "main", str(saat)], check=True)
        (saat / "README.md").write_text(f"{name}\n", encoding="utf-8")
        git(saat, "add", "README.md")
        git(saat, "commit", "-q", "-m", "start")
        git(saat, "push", "-q", str(bare), "HEAD:refs/heads/main")
        haupt = self.welt.tmp / "haupt" / name
        git(self.welt.tmp, "clone", "-q", f"https://github.com/{slug}", str(haupt))
        self.haupt[slug] = haupt
        self.hauptzweig[slug] = "main"
        return haupt

    def maschine(self, eintraege: list[tuple[str, str]]) -> None:
        """Maschinen-Liste: je Eintrag (slug, ticket_repo); ``pfad`` = Hauptklon."""
        datei = Path(os.environ["TO_SPAWN_MASCHINE"])
        datei.parent.mkdir(parents=True, exist_ok=True)
        repos = [{"slug": s, "pfad": str(self.haupt[s]), "ticket_repo": t, "worktree_basis": ""} for s, t in eintraege]
        datei.write_text(json.dumps({"repos": repos}), encoding="utf-8")

    def ticket(self, slug: str, nr: int, state: str, vor_stunden: float | None = None) -> None:
        daten = json.loads(self.tickets.read_text(encoding="utf-8"))
        eintrag: dict = {"state": state}
        if vor_stunden is not None:
            eintrag["closedAt"] = iso(self.welt.jetzt - vor_stunden * STUNDE)
        daten.setdefault(slug.lower(), {})[str(nr)] = eintrag
        self.tickets.write_text(json.dumps(daten), encoding="utf-8")

    def worktree(self, slug: str, nr: int, *, gemergt: bool = False) -> Path:
        """Worktree ``wt-<nr>`` (Zweig ``ticket/<nr>-test``) aus dem Hauptklon von ``slug``."""
        wt = self.welt.heim / "wt" / f"wt-{nr}"
        zweig = f"ticket/{nr}-test"
        haupt = self.haupt[slug]
        git(haupt, "worktree", "add", "-q", "-b", zweig, str(wt), f"origin/{self.hauptzweig[slug]}")
        (wt / "arbeit.txt").write_text("fertig\n", encoding="utf-8")
        git(wt, "add", "arbeit.txt")
        git(wt, "commit", "-q", "-m", "arbeit")
        git(wt, "push", "-q", "-u", "origin", zweig)
        if gemergt:
            git(wt, "push", "-q", "origin", f"HEAD:refs/heads/{self.hauptzweig[slug]}")
        return wt


@pytest.fixture
def repos(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Repos:
    assert ORIGIN.endswith(APP)
    return Repos(welt, monkeypatch)


def _je_ordner(welt: Welt) -> dict[str, hausmeister.Ergebnis]:
    ergebnisse = hausmeister.aufraeumen_alles(loeschen=True, jetzt=welt.jetzt)
    return {Path(e.ordner).name: e for e in ergebnisse}


# --- (a) zwei Ticket-Repos, Tickets zu ⇒ beide geräumt ------------------------------


def test_a_zwei_ticket_repos_beide_ordner_werden_geraeumt(welt: Welt, repos: Repos) -> None:
    repos.repo(SKILL)
    repos.maschine([(APP, APP), (SKILL, SKILL)])
    app_wt = repos.worktree(APP, 201)
    skill_wt = repos.worktree(SKILL, 202)
    repos.ticket(APP, 201, "CLOSED", 48)
    repos.ticket(SKILL, 202, "CLOSED", 48)

    r = _je_ordner(welt)

    assert r["wt-201"].art == "gelöscht" and not r["wt-201"].fehler, r["wt-201"].grund
    assert r["wt-202"].art == "gelöscht" and not r["wt-202"].fehler, r["wt-202"].grund
    assert not app_wt.exists() and not skill_wt.exists()


# --- (b) nicht zuordenbarer Ordner bleibt mit Grund, der Rest wird geräumt ----------


def test_b_nicht_zuordenbar_bleibt_mit_grund_rest_wird_geraeumt(welt: Welt, repos: Repos) -> None:
    repos.repo(SKILL)
    repos.repo(WEGWERF)
    # WEGWERF ohne Ticket-Repo und von keinem Eintrag als Ticket-Repo genannt ⇒ nicht bestimmbar
    repos.maschine([(APP, APP), (SKILL, SKILL), (WEGWERF, "")])
    app_wt = repos.worktree(APP, 211)
    skill_wt = repos.worktree(SKILL, 212)
    # gemergt, sauber, alt: über den „fremd, aber fertig“-Weg wäre er heute weg
    weg_wt = repos.worktree(WEGWERF, 213, gemergt=True)
    repos.ticket(APP, 211, "CLOSED", 48)
    repos.ticket(SKILL, 212, "CLOSED", 48)
    repos.ticket(APP, 213, "CLOSED", 24 * 400)  # gleiche Nummer woanders: zählt nicht
    welt.jetzt += 30 * TAG

    r = _je_ordner(welt)

    assert r["wt-213"].art == "bleibt", r["wt-213"].grund
    assert "Ticket-Repo" in r["wt-213"].grund, r["wt-213"].grund
    assert weg_wt.is_dir()
    assert r["wt-211"].art == "gelöscht", r["wt-211"].grund
    assert r["wt-212"].art == "gelöscht", r["wt-212"].grund
    assert not app_wt.exists() and not skill_wt.exists()


# --- (c) gleiche Nummer, im eigenen Repo offen ⇒ keine Verwechslung -----------------


def test_c_gleiche_nummer_im_anderen_repo_zu_eigenes_offen_bleibt(welt: Welt, repos: Repos) -> None:
    repos.repo(SKILL)
    repos.maschine([(APP, APP), (SKILL, SKILL)])
    wt = repos.worktree(SKILL, 221)
    repos.ticket(SKILL, 221, "OPEN")
    repos.ticket(APP, 221, "CLOSED", 24 * 400)  # App-Ticket #221 uralt zu

    r = welt.aufraeumen(wt, loeschen=True)

    assert r.art == "bleibt", r.grund
    assert wt.is_dir()


def test_c_gleiche_nummer_offen_bleibt_auch_gemergt_und_alt(welt: Welt, repos: Repos) -> None:
    """Eigenes Ticket offen ⇒ bleibt, auch wenn der Kopf im Hauptzweig liegt (nicht „fremd“)."""
    repos.repo(SKILL)
    repos.maschine([(APP, APP), (SKILL, SKILL)])
    wt = repos.worktree(SKILL, 222, gemergt=True)
    repos.ticket(SKILL, 222, "OPEN")
    repos.ticket(APP, 222, "CLOSED", 24 * 400)
    welt.jetzt += 30 * TAG

    r = welt.aufraeumen(wt, loeschen=True)

    assert r.art == "bleibt", r.grund
    assert "#222" in r.grund and "offen" in r.grund, r.grund
    assert wt.is_dir()


def test_c_zweig_fragt_ticket_repo_seines_repos(welt: Welt, repos: Repos) -> None:
    """Remote-Zweig: Ticket-Zustand kommt aus dem Ticket-Repo des Zweig-Repos (SKILL offen),
    nicht aus dem gleichnamigen, uralt geschlossenen App-Ticket."""
    repos.repo(SKILL)
    repos.maschine([(APP, APP), (SKILL, SKILL)])
    repos.worktree(SKILL, 223)
    repos.ticket(SKILL, 223, "OPEN")
    repos.ticket(APP, 223, "CLOSED", 24 * 400)

    r = hausmeister.zweig_aufraeumen(f"https://github.com/{SKILL}", "ticket/223-test", loeschen=True)

    assert r.art == "bleibt", r.grund
    assert "#223" in r.grund and "offen" in r.grund, r.grund


def test_doppelter_eintrag_mit_verschiedenem_ticket_repo_ist_unklar(welt: Welt, repos: Repos) -> None:
    repos.repo(SKILL)
    repos.maschine([(APP, APP), (SKILL, SKILL), (SKILL, APP)])
    assert hausmeister._ticket_repo_fuer(SKILL) is None
    assert hausmeister._ticket_repo_fuer(APP) == APP


# --- (d) laufender Prozess ⇒ bleibt ------------------------------------------------


def test_d_prozess_im_ordner_bleibt(welt: Welt, repos: Repos) -> None:
    repos.repo(SKILL)
    repos.maschine([(APP, APP), (SKILL, SKILL)])
    wt = repos.worktree(SKILL, 231)
    repos.ticket(SKILL, 231, "CLOSED", 48)
    proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=wt, stdin=subprocess.PIPE)
    try:
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and "Prozess" in r.grund, r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait()
