"""Hausmeister #235, Fix-Runde 2: Reflog, Repo-eigene Umleitung, strenges ``ls-remote``.

- R1: Ein Commit, der nur noch im Reflog liegt (abgehängter HEAD, dann weggewechselt),
  ginge mit dem Ordner verloren ⇒ Ordner bleibt („ungesicherte Commits im Verlauf“) —
  bei Klon, Worktree und Ordner mit Ticket-Nummer. Gegenprobe: ein sauberer alter Ordner
  mit gewöhnlichem Reflog (Stände längst auf dem Server) geht weiter weg.
- N1: Biegt die Repo-eigene Konfiguration ``origin`` um (``url.<x>.insteadOf``) oder stehen
  mehrere ``remote.origin.url`` da ⇒ fremdes Repo ⇒ bleibt.
- N2: ``ls-remote`` zählt nur Zeilen ``<sha>\\trefs/heads/…``; anderes ⇒ bleibt.
- Testlücke H1: ein erlaubter Anfang mitten in der URL zählt nicht (nur ``.match``).

Echte Git-Repos (bare-„origin“ über ``insteadOf`` in der Test-Git-Konfig) wie in den
#176-/#235-Tests.
"""

# ruff: noqa: F811 — ``welt``/``repos`` kommen als Fixtures aus den #1368-/#176-Tests.
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_hausmeister_1368 import Welt, git, welt  # noqa: F401 — ``welt`` ist ein Fixture
from test_hausmeister_skill_ordner_235 import _lauf, _ordner, _vorgabe
from test_hausmeister_ticket_repo_176 import SKILL, TAG, Repos, repos  # noqa: F401

from to_spawn import hausmeister

ECHT = f"https://github.com/{SKILL}"
VERLAUF = "ungesicherte Commits im Verlauf"


def _bare_kopie(welt: Welt, ziel: Path) -> Path:
    ziel.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(welt.tmp / "claude-skill-to-spawn.git"), str(ziel)], check=True)
    return ziel


def _klon(welt: Welt, name: str) -> Path:
    wt = welt.heim / "wt" / name
    git(welt.tmp, "clone", "-q", ECHT, str(wt))
    return wt


def _commit(wt: Path, datei: str = "arbeit.txt") -> str:
    (wt / datei).write_text("nur hier\n", encoding="utf-8")
    git(wt, "add", datei)
    git(wt, "commit", "-q", "-m", "arbeit")
    return git(wt, "rev-parse", "HEAD").strip()


def _alt(welt: Welt) -> None:
    welt.jetzt += 30 * TAG


def _hat(bare: Path, sha: str) -> bool:
    lauf = subprocess.run(
        ["git", "-C", str(bare), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True, check=False
    )
    return lauf.returncode == 0


# --- Testlücke H1 ---------------------------------------------------------------------


def test_h1_erlaubter_anfang_mitten_in_der_url_ist_fremd() -> None:
    assert hausmeister._slug(f"https://evil.example/{ECHT}") == ""
    assert hausmeister._slug(f"file:///srv/git@github.com:{SKILL}") == ""


# --- R1: Commit nur im Reflog -----------------------------------------------------------


def test_r1_klon_commit_nur_im_reflog_bleibt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _klon(welt, "skill-reflog")
    git(wt, "checkout", "-q", "--detach")
    sha = _commit(wt)
    git(wt, "checkout", "-q", "-")
    _alt(welt)

    r = _lauf(welt)["skill-reflog"]

    assert r.art == "bleibt" and VERLAUF in r.grund, r
    assert wt.is_dir() and git(wt, "cat-file", "-t", sha).strip() == "commit"


def test_r1_worktree_commit_nur_im_reflog_bleibt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _ordner(repos, SKILL, "skill-wreflog", None)
    _commit(wt)
    git(wt, "checkout", "-q", "--detach", "origin/HEAD")
    _alt(welt)

    r = _lauf(welt)["skill-wreflog"]

    assert r.art == "bleibt" and VERLAUF in r.grund, r
    assert wt.is_dir()


def test_r1_mit_nummer_commit_nur_im_reflog_bleibt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _ordner(repos, SKILL, "skill-176", "l4/176-x")
    git(wt, "checkout", "-q", "--detach")
    sha = _commit(wt, "verwaist.txt")
    git(wt, "checkout", "-q", "l4/176-x")
    repos.ticket(SKILL, 176, "CLOSED", 24 * 400)
    _alt(welt)

    r = _lauf(welt)["skill-176"]

    assert r.art == "bleibt" and VERLAUF in r.grund, r
    assert wt.is_dir() and not _hat(welt.tmp / "claude-skill-to-spawn.git", sha)


def test_r1_trocken_meldet_reflog_ebenso(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _klon(welt, "skill-trocken")
    git(wt, "checkout", "-q", "--detach")
    _commit(wt)
    git(wt, "checkout", "-q", "-")
    _alt(welt)

    ergebnisse = hausmeister.aufraeumen_alles(loeschen=False, jetzt=welt.jetzt)
    r = {Path(e.ordner).name: e for e in ergebnisse}["skill-trocken"]

    assert r.art == "bleibt" and VERLAUF in r.grund, r


def test_r1_gegenprobe_klon_mit_gewoehnlichem_reflog_geht_weg(welt: Welt, repos: Repos) -> None:
    """Zweig, Commit, Push, Merge, zurück auf den Hauptzweig, Pull, abgehängt hin und her:
    jeder Reflog-Stand liegt auf dem Server ⇒ gelöscht."""
    _vorgabe(repos)
    wt = _klon(welt, "skill-gewoehnlich")
    haupt = repos.hauptzweig[SKILL]
    git(wt, "checkout", "-q", "-b", "doku/stand-99")
    _commit(wt, "a.txt")
    _commit(wt, "b.txt")
    git(wt, "push", "-q", "-u", "origin", "doku/stand-99")
    git(wt, "push", "-q", "origin", f"HEAD:refs/heads/{haupt}")
    git(wt, "checkout", "-q", haupt)
    git(wt, "pull", "-q", "--ff-only")
    git(wt, "checkout", "-q", "--detach", "HEAD~1")
    git(wt, "checkout", "-q", haupt)
    git(wt, "branch", "-q", "-D", "doku/stand-99")
    assert len(git(wt, "log", "-g", "--format=%H", "HEAD").split()) >= 5
    _alt(welt)

    r = _lauf(welt)["skill-gewoehnlich"]

    assert r.art == "gelöscht", r
    assert not wt.exists()


def test_r1_gegenprobe_sauberer_worktree_geht_weg(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _ordner(repos, SKILL, "skill-doku", "doku/stand-84", gemergt=True)
    _alt(welt)

    r = _lauf(welt)["skill-doku"]

    assert r.art == "gelöscht", r
    assert not wt.exists()


# --- N1: Repo-eigene Umleitung, mehrere URLs ----------------------------------------------


def test_n1_repo_eigene_umleitung_auf_fremdes_repo_bleibt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    fremd = _bare_kopie(welt, welt.tmp / "fremd" / "iof.git")
    wt = _klon(welt, "skill-iof")
    git(wt, "config", f"url.{fremd.as_posix()}.insteadOf", ECHT + ".git")
    git(wt, "config", "remote.origin.url", ECHT + ".git")
    git(wt, "fetch", "-q", "--prune", "origin")
    git(wt, "checkout", "-q", "-b", "doku/stand-92")
    sha = _commit(wt)
    git(wt, "push", "-q", "-u", "origin", "doku/stand-92")
    _alt(welt)

    r = _lauf(welt)["skill-iof"]

    assert r.art == "bleibt" and "fremdes Repo" in r.grund, r
    assert wt.is_dir() and _hat(fremd, sha)


def test_n1_mehrere_origin_urls_bleibt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    fremd = _bare_kopie(welt, welt.tmp / "fremd" / "multi.git")
    wt = _klon(welt, "skill-multi")
    git(wt, "config", "--replace-all", "remote.origin.url", str(fremd))
    git(wt, "config", "--add", "remote.origin.url", ECHT)
    git(wt, "fetch", "-q", "--prune", "origin")
    git(wt, "checkout", "-q", "-b", "doku/stand-93")
    _commit(wt)
    git(wt, "push", "-q", str(fremd), "doku/stand-93")
    git(wt, "fetch", "-q", "origin")
    git(wt, "branch", "-q", "-u", "origin/doku/stand-93")
    _alt(welt)

    r = _lauf(welt)["skill-multi"]

    assert r.art == "bleibt" and "fremdes Repo" in r.grund, r
    assert wt.is_dir()


# --- N2: ls-remote streng lesen ---------------------------------------------------------


@pytest.mark.parametrize("ausgabe", ["HEAD ist kein Problem\n", "--all\n", "abc\trefs/heads/x\n"])
def test_n2_ls_remote_fremde_zeile_bleibt(
    welt: Welt, repos: Repos, monkeypatch: pytest.MonkeyPatch, ausgabe: str
) -> None:
    _vorgabe(repos)
    wt = _klon(welt, "skill-lsr")
    git(wt, "checkout", "-q", "-b", "doku/stand-97")
    _commit(wt)
    haupt = git(wt, "rev-parse", "--abbrev-ref", "origin/HEAD").strip().removeprefix("origin/")
    git(wt, "config", "remote.origin.fetch", f"+refs/heads/{haupt}:refs/remotes/origin/{haupt}")
    git(wt, "update-ref", "refs/remotes/origin/faelschung", "HEAD")
    echt = hausmeister._git_ohne_frage

    def falsch(cwd: Path, *args: str) -> str:
        if args and args[0] == "ls-remote":
            return ausgabe
        return echt(cwd, *args)

    monkeypatch.setattr(hausmeister, "_git_ohne_frage", falsch)
    _alt(welt)

    r = _lauf(welt)["skill-lsr"]

    assert r.art == "bleibt" and r.fehler and "ls-remote" in r.grund, r
    assert wt.is_dir()
