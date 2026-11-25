"""Hausmeister & Temp-Ordner #179: „gesichert“ immer gegen erreichbares Remote (ls-remote).

Prüft, dass weder der Hausmeister noch der Temp-Aufräumer einem gefälschten lokalen
``refs/remotes/origin/*``-Ref oder einem weggefallenen Remote vertrauen (fail-closed).
"""

# ruff: noqa: F811 — ``welt``/``repos`` kommen als Fixtures aus den #1368-/#176-Tests.
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from test_hausmeister_1368 import Welt, _commit, git, welt  # noqa: F401 — Fixture
from test_hausmeister_ticket_repo_176 import APP, SKILL, TAG, Repos, repos  # noqa: F401
from test_temp_ordner_136 import _alt_baum, _git, _markieren, _quelle_anlegen, _wurzel

from to_spawn import hausmeister, hausmeister_windows, temp_ordner


def _ref_faelschen(repo: Path, ref: str = "refs/remotes/origin/gefaelscht") -> None:
    """Verengt den Fetch-Refspec und legt einen lokalen Tracking-Ref auf ``HEAD`` an,
    sodass ``git fetch --prune origin`` ihn nicht entfernt."""
    git(repo, "config", "remote.origin.fetch", "+refs/heads/master:refs/remotes/origin/master")
    git(repo, "update-ref", ref, "HEAD")


def test_hausmeister_klon_mit_gefaelschtem_origin_ref_sichert_lokalen_commit(welt: Welt) -> None:
    """Rot-Test #179: ein gefälschter lokaler origin-Ref darf nie einen lokalen Commit
    als „schon auf origin“ durchgehen lassen — der Hausmeister muss ihn sichern."""
    klon = welt.klon("wt/wt-1791")
    git(klon, "checkout", "-q", "-b", "ticket/1791-lokal")
    _commit(klon, "nur-lokal.txt", "darf nicht verloren gehen\n", push=False)
    _ref_faelschen(klon)
    welt.ticket(1791, "CLOSED", 48)

    r = welt.aufraeumen(klon, loeschen=True)

    assert r.art == "gesichert und gelöscht" and r.sicherung, r.grund
    assert welt.im_bare("show", "sicherung/1791:nur-lokal.txt") == "darf nicht verloren gehen\n"


def test_hausmeister_gefaelschter_ref_mit_geheimnis_im_commit_bleibt(welt: Welt) -> None:
    """Ein Geheimnis in einem nur lokalen Commit darf auch mit gefälschtem origin-Ref
    weder still gelöscht noch hochgeladen werden."""
    klon = welt.klon("wt/wt-1792")
    git(klon, "checkout", "-q", "-b", "ticket/1792-geheim")
    _commit(klon, ".env", "TOKEN=geheim-179\n", push=False)
    _ref_faelschen(klon)
    welt.ticket(1792, "CLOSED", 48)

    r = welt.aufraeumen(klon, loeschen=True)

    assert r.art == "bleibt" and r.fuer_david and ".env" in r.grund, r.grund
    assert (klon / ".env").is_file()
    assert "TOKEN=geheim-179" not in welt.im_bare("log", "--all", "-p")


def test_hausmeister_fremd_ordner_mit_gefaelschtem_hauptzweig_ref_bleibt(welt: Welt, repos: Repos) -> None:
    """Auch im ``_fremd_fertig``-Weg darf ein gefälschter lokaler ``origin/main``-Ref
    keinen ungesicherten lokalen Commit als „im Hauptzweig“ gelten lassen."""
    repos.repo(SKILL)
    # Skill-Eintrag zeigt aufs App-Repo ⇒ Skill-Ordner mit Nummer läuft über _fremd_fertig
    repos.maschine([(APP, APP), (SKILL, APP)])
    klon = welt.heim / "wt" / "skill-1793"
    git(welt.tmp, "clone", "-q", f"https://github.com/{SKILL}", str(klon))
    _commit(klon, "lokal.txt", "nur lokal\n", push=False)
    # Fetch-Refspec entfernen und lokalen origin/main auf HEAD fälschen
    git(klon, "config", "--unset-all", "remote.origin.fetch")
    git(klon, "update-ref", "refs/remotes/origin/main", "HEAD")
    welt.jetzt += 3 * TAG

    r = welt.aufraeumen(klon, loeschen=True)

    assert r.art == "bleibt", r.grund
    assert (klon / "lokal.txt").is_file()


def test_hausmeister_weggefallenes_remote_in_gegenprobe_fail_closed(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fällt das Remote direkt vor dem Löschen weg, hält die Gegenprobe den Ordner fest."""
    klon = welt.klon("wt/wt-1794")
    git(klon, "checkout", "-q", "-b", "ticket/1794-weg")
    _commit(klon, "datei.txt", "stand\n", push=False)
    git(klon, "push", "-q", "-u", "origin", "ticket/1794-weg")
    welt.ticket(1794, "CLOSED", 48)

    echt_probe = hausmeister._kein_prozess

    def remote_weg(ordner: Path, *, probe: bool = False) -> None:
        echt_probe(ordner, probe=probe)
        if probe and welt.bare.exists():
            if sys.version_info >= (3, 12):
                shutil.rmtree(welt.bare, onexc=hausmeister_windows.schreibschutz_weg)
            else:
                shutil.rmtree(welt.bare, onerror=hausmeister_windows.schreibschutz_weg)

    monkeypatch.setattr(hausmeister, "_kein_prozess", remote_weg)
    r = welt.aufraeumen(klon, loeschen=True)

    assert r.art == "bleibt" and r.fehler, r.grund
    assert klon.is_dir()


def test_temp_ordner_gefaelschter_ref_auf_nebenzweig_und_transport_helfer_bleiben(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, {1795, 1796})
    quelle = _quelle_anlegen(tmp_path)

    # 1. Nebenzweig mit lokalem Commit und gefälschtem origin-Ref
    neben = wurzel / "t1795"
    _git(tmp_path, "clone", "-q", str(quelle), str(neben))
    _git(neben, "checkout", "-q", "-b", "neben")
    _git(neben, "commit", "-q", "--allow-empty", "-m", "nur-auf-neben")
    _git(neben, "update-ref", "refs/remotes/origin/neben", "HEAD")
    _git(neben, "checkout", "-q", "main")

    # 2. Transport-Helfer als origin (ext::…) darf nie ausgeführt/als gesichert gezählt werden
    helfer = wurzel / "t1796"
    _git(tmp_path, "clone", "-q", str(quelle), str(helfer))
    _git(helfer, "remote", "set-url", "origin", "ext::echo sollte-nie-laufen")

    for p, nr in ((neben, 1795), (helfer, 1796)):
        _alt_baum(_markieren(p, nr))

    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert neben.is_dir() and "ungesichert" in ergebnis[neben], ergebnis[neben]
    assert helfer.is_dir() and "ungesichert" in ergebnis[helfer], ergebnis[helfer]
