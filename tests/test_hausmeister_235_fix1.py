"""Hausmeister #235, Fix-Runde 1: fremde Remotes und Push-Ziele nie als eigenes Repo.

- H1: Nur URLs, die mit ``https://github.com/``, ``ssh://git@github.com/`` oder
  ``git@github.com:`` beginnen, zählen als GitHub-Repo. Ein fremder Host
  (``notgithub.com``) oder ein lokaler Pfad mit ``github.com/…`` im Namen ist ein
  fremdes Repo ⇒ der Ordner bleibt.
- M2: Weicht das Push-Ziel von ``origin`` ab (``pushurl``, ``pushInsteadOf``), landete
  eine Sicherung fremd ⇒ fremdes Repo ⇒ bleibt.
- M1a: Ein Ordner ohne Ticket-Nummer geht nur weg, wenn jeder lokale Kopf auf dem echten
  Server steht (``ls-remote``), nicht nur in lokalen (fälschbaren) Tracking-Refs.

Echte Git-Repos (bare-„origin“ über ``insteadOf``) wie in den #176-/#235-Tests.
"""

# ruff: noqa: F811 — ``welt``/``repos`` kommen als Fixtures aus den #1368-/#176-Tests.
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import attrappe
from test_hausmeister_1368 import Welt, git, welt  # noqa: F401 — ``welt`` ist ein Fixture
from test_hausmeister_skill_ordner_235 import _lauf, _ordner, _vorgabe
from test_hausmeister_ticket_repo_176 import SKILL, TAG, Repos, repos  # noqa: F401

from to_spawn import hausmeister


@pytest.fixture(autouse=True)
def eigenes_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))


def _bare_kopie(welt: Welt, ziel: Path) -> Path:
    """Nacktes Repo als Kopie des Skill-Origin (ein fremder Server mit gleichem Inhalt)."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(welt.tmp / "claude-skill-to-spawn.git"), str(ziel)], check=True)
    return ziel


def _umleiten(welt: Welt, ziel: Path, url: str, art: str = "insteadOf") -> None:
    with (welt.tmp / "gitconfig").open("a", encoding="utf-8") as f:
        f.write(f'[url "{ziel.as_posix()}"]\n\t{art} = {url}\n')


def _klon(welt: Welt, name: str, url: str) -> Path:
    wt = welt.heim / "wt" / name
    git(welt.tmp, "clone", "-q", url, str(wt))
    return wt


def _commit(wt: Path) -> str:
    (wt / "arbeit.txt").write_text("nur hier\n", encoding="utf-8")
    git(wt, "add", "arbeit.txt")
    git(wt, "commit", "-q", "-m", "arbeit")
    return git(wt, "rev-parse", "HEAD").strip()


def _hat_ref(bare: Path, ref: str) -> bool:
    lauf = subprocess.run(
        ["git", "-C", str(bare), "rev-parse", "-q", "--verify", ref], capture_output=True, check=False
    )
    return lauf.returncode == 0


# --- H1: _slug nur mit Schema + Host am Anfang ---------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Shavy72/claude-skill-to-spawn.git",
        "ssh://git@github.com/Shavy72/claude-skill-to-spawn",
        "git@github.com:Shavy72/claude-skill-to-spawn.git",
    ],
)
def test_h1_slug_erlaubte_formen(url: str) -> None:
    assert hausmeister._slug(url) == "Shavy72/claude-skill-to-spawn"


@pytest.mark.parametrize(
    "url",
    [
        "https://notgithub.com/Shavy72/claude-skill-to-spawn",
        "https://github.com.example.org/Shavy72/claude-skill-to-spawn",
        "/srv/ghq/github.com/Shavy72/claude-skill-to-spawn.git",
        "file:///srv/ghq/github.com/Shavy72/claude-skill-to-spawn.git",
        "http://github.com/Shavy72/claude-skill-to-spawn",  # unverschlüsselt: kein eigener Weg
        "https://spiegel.example.org/github.com/Shavy72/claude-skill-to-spawn",
    ],
)
def test_h1_slug_fremde_formen_sind_leer(url: str) -> None:
    assert hausmeister._slug(url) == ""


@pytest.mark.parametrize(
    ("form", "url"),
    [
        ("fremder-host", "https://notgithub.com/Shavy72/claude-skill-to-spawn"),
        ("http", "http://github.com/Shavy72/claude-skill-to-spawn"),
    ],
)
def test_h1_ohne_nummer_mit_fremdem_host_bleibt(welt: Welt, repos: Repos, form: str, url: str) -> None:
    _vorgabe(repos)
    fremd = _bare_kopie(welt, welt.tmp / "fremd" / f"{form}.git")
    _umleiten(welt, fremd, url)
    wt = _klon(welt, "skill-alt", url)
    git(wt, "checkout", "-q", "-b", "doku/stand-90")
    _commit(wt)
    git(wt, "push", "-q", "-u", "origin", "doku/stand-90")  # Commit nur auf dem fremden Server
    welt.jetzt += 30 * TAG

    r = _lauf(welt)["skill-alt"]

    assert r.art == "bleibt" and "fremdes Repo" in r.grund, r
    assert wt.is_dir()


def test_h1_ohne_nummer_lokaler_pfad_mit_github_im_namen_bleibt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    spiegel = _bare_kopie(welt, welt.tmp / "spiegel" / "github.com" / "Shavy72" / "claude-skill-to-spawn.git")
    wt = _klon(welt, "skill-spiegel", str(spiegel))
    git(wt, "checkout", "-q", "-b", "doku/stand-91")
    _commit(wt)
    git(wt, "push", "-q", "-u", "origin", "doku/stand-91")
    welt.jetzt += 30 * TAG

    r = _lauf(welt)["skill-spiegel"]

    assert r.art == "bleibt" and "fremdes Repo" in r.grund, r
    assert wt.is_dir()


# --- M2: abweichendes Push-Ziel = fremdes Repo ----------------------------------------


@pytest.mark.parametrize("art", ["pushurl", "pushInsteadOf"])
def test_m2_abweichendes_push_ziel_bleibt_ohne_sicherung(welt: Welt, repos: Repos, art: str) -> None:
    _vorgabe(repos)
    fremd = _bare_kopie(welt, welt.tmp / "pushziel.git")
    wt = _ordner(repos, SKILL, "skill-176", "l4/176-x")
    (wt / "arbeit.txt").write_text("ungesichert\n", encoding="utf-8")
    if art == "pushurl":
        git(wt, "config", "remote.origin.pushurl", str(fremd))
    else:
        _umleiten(welt, fremd, f"https://github.com/{SKILL}", "pushInsteadOf")
    repos.ticket(SKILL, 176, "CLOSED", 24 * 400)
    welt.jetzt += 30 * TAG

    r = _lauf(welt)["skill-176"]

    assert r.art == "bleibt" and "fremdes Repo" in r.grund, r
    assert wt.is_dir() and (wt / "arbeit.txt").read_text(encoding="utf-8") == "ungesichert\n"
    assert not _hat_ref(fremd, "sicherung/176")


# --- M1a: ohne Nummer zählt nur der echte Server ---------------------------------------


def test_m1a_ohne_nummer_gefaelschte_tracking_ref_bleibt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _klon(welt, "skill-falsch", f"https://github.com/{SKILL}")
    git(wt, "checkout", "-q", "-b", "doku/stand-93")
    _commit(wt)
    git(wt, "config", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main")
    git(wt, "update-ref", "refs/remotes/origin/doku/stand-93", "HEAD")  # nie gepusht, nur vorgetäuscht
    welt.jetzt += 30 * TAG

    r = _lauf(welt)["skill-falsch"]

    assert r.art == "bleibt" and "nicht auf dem Server" in r.grund, r
    assert wt.is_dir() and (wt / "arbeit.txt").is_file()


def test_m1a_ohne_nummer_server_nicht_erreichbar_bleibt(welt: Welt, repos: Repos) -> None:
    """Nach dem fetch fällt der Server weg (Netzfehler beim ls-remote) ⇒ bleibt mit Grund."""
    _vorgabe(repos)
    wt = _klon(welt, "skill-doku86", f"https://github.com/{SKILL}")
    # ls-remote läuft über upload-pack wie fetch; der Wrapper lässt den ersten Aufruf (fetch)
    # durch und fällt danach aus
    marke = welt.tmp / "upload-pack-aus-beim-ls-remote.zweiter"
    wrapper = attrappe(
        welt.tmp,
        "upload-pack-aus-beim-ls-remote",
        f"""
        import os, pathlib, sys
        marke = pathlib.Path({str(marke)!r})
        if marke.is_file():
            print("Server weg", file=sys.stderr)
            sys.exit(1)
        marke.touch()
        os.execvp("git-upload-pack", ["git-upload-pack", *sys.argv[1:]])
        """,
    )
    git(wt, "config", "remote.origin.uploadpack", str(wrapper))
    welt.jetzt += 30 * TAG

    r = _lauf(welt)["skill-doku86"]

    assert r.art == "bleibt" and r.fehler and "Server" in r.grund, r
    assert wt.is_dir()
