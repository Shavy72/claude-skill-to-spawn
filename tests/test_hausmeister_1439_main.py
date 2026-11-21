"""Hausmeister #1439 Fixrunde 2: Repos mit Hauptzweig ``main`` (claude-skill-to-spawn).

Weg-Tests mit echtem Wegwerf-Git: ein zweites Bare-Remote mit ``-b main`` neben der
``welt`` aus #1368. Gestellt sind nur gh (Ticket-Stand) und die Repo-Listen
(``TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS``, ``..._HAUPTREPOS``).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    Welt,
    git,
    welt,
)

from to_spawn import hausmeister

SKILL_URL = "https://github.com/Shavy72/claude-skill-to-spawn"


@pytest.fixture
def mw(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> tuple[Welt, Path, Path]:
    """(welt, Bare-Remote mit ``main``, lokales Hauptrepo davon)."""
    bare = welt.tmp / "skill.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    gitconfig = welt.tmp / "gitconfig"
    with gitconfig.open("a", encoding="utf-8") as f:
        f.write(f'[url "{bare}"]\n\tinsteadOf = {SKILL_URL}\n')
    saat = welt.tmp / "skill-saat"
    subprocess.run(["git", "init", "-q", "-b", "main", str(saat)], check=True)
    (saat / "README.md").write_text("start\n", encoding="utf-8")
    git(saat, "add", "README.md")
    git(saat, "commit", "-q", "-m", "start")
    git(saat, "push", "-q", SKILL_URL, "HEAD:refs/heads/main")
    haupt = welt.tmp / "haupt" / "claude-skill-to-spawn"
    git(welt.tmp, "clone", "-q", SKILL_URL, str(haupt))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", SKILL_URL)
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", str(haupt))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN", "1")  # #1491: Löschen nur mit Schalter
    return welt, bare, haupt


def _commit(ordner: Path, datei: str) -> str:
    (ordner / datei).write_text("x\n", encoding="utf-8")
    git(ordner, "add", datei)
    git(ordner, "commit", "-q", "-m", f"c {datei}")
    return git(ordner, "rev-parse", "HEAD").strip()


def _heads(repo: Path) -> list[str]:
    return git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").split()


def test_remote_main_gemeldeter_stand_zweig_weg(mw: tuple[Welt, Path, Path]) -> None:
    """Remote mit ``main``: Ticket-Zweig mit gemeldetem Stand ist danach weg."""
    w, bare, haupt = mw
    git(haupt, "checkout", "-q", "-b", "ticket/70-test")
    tip = _commit(haupt, "a.txt")
    git(haupt, "push", "-q", "origin", "ticket/70-test")
    git(haupt, "checkout", "-q", "main")
    w.ticket(70, "OPEN")
    r = hausmeister.zweig_aufraeumen(
        SKILL_URL, "ticket/70-test", uebernommen_tip=tip, loeschen=True
    )
    assert r.art == "gelöscht" and not r.fehler, r.grund
    assert "ticket/70-test" not in _heads(bare)


def test_tageslauf_main_remote_und_lokal_richtig(mw: tuple[Welt, Path, Path]) -> None:
    """Tageslauf: Remote-Zweig (Ticket zu, auf main) und lokaler Zweig auf main gehen weg."""
    w, bare, haupt = mw
    git(haupt, "checkout", "-q", "-b", "ticket/71-test")
    _commit(haupt, "b.txt")
    git(haupt, "push", "-q", "origin", "ticket/71-test")
    git(haupt, "checkout", "-q", "main")
    git(haupt, "merge", "-q", "--ff-only", "ticket/71-test")
    git(haupt, "checkout", "-q", "-b", "ticket/72-test")
    _commit(haupt, "c.txt")
    git(haupt, "checkout", "-q", "main")
    git(haupt, "merge", "-q", "--ff-only", "ticket/72-test")
    git(haupt, "push", "-q", "origin", "main")
    w.ticket(71, "CLOSED", vor_stunden=48)
    w.ticket(72, "CLOSED", vor_stunden=48)
    erg = hausmeister.zweige_aufraeumen(loeschen=True, jetzt=w.jetzt)
    assert not [e for e in erg if e.fehler], [(e.zweig, e.grund) for e in erg]
    assert {(e.ort, e.zweig, e.art) for e in erg} == {
        ("lokal", "ticket/71-test", "gelöscht"),
        ("lokal", "ticket/72-test", "gelöscht"),
        ("remote", "ticket/71-test", "gelöscht"),
    }
    assert "ticket/71-test" not in _heads(bare)
    assert not [h for h in _heads(haupt) if h.startswith("ticket/")]


def test_git_fehler_grund_einzeilig(mw: tuple[Welt, Path, Path]) -> None:
    """git-Fehler: bleibt, fehler=True, Grund nur eine Zeile (kein Hilfetext)."""
    w, _, haupt = mw
    git(haupt, "branch", "ticket/73-test")
    git(haupt, "remote", "set-url", "origin", str(w.tmp / "gibt-es-nicht.git"))
    w.ticket(73, "CLOSED", vor_stunden=48)
    r = hausmeister.zweig_aufraeumen(
        haupt, "ticket/73-test", ort="lokal", loeschen=True
    )
    assert r.art == "bleibt" and r.fehler
    assert r.grund and "\n" not in r.grund, r.grund
