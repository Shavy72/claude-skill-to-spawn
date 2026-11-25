"""Hausmeister #241: Reste ohne Git-Ordner unter dem Helfer-Verzeichnis.

Ergänzt #163 (Ordner ohne Git mit Ticket-Nummer) um eine nachweislich sichere Regel
für Reste und Worktree-Waisen ohne Ticket-Nummer, die nie ungesicherte Arbeit löscht:
- Inhalt ist bytegleich einem Commit auf ``origin`` eines bekannten eigenen Hauptrepos
  (bekannte Erzeugnisse / Test-DBs dürfen daneben liegen) + Alter ≥ 24 h + kein Prozess +
  kein Geheimnis-Verdacht + keine fremde Datenbank + kein eingebettetes Repo ⇒ wegwerfbar.
- Jede lokale Abweichung (auch eine geänderte oder zusätzliche Datei), eingebettete Repos
  (z. B. ``to-spawn/``, ``basis/``), Geheimnisse, Frische, Prozesse oder nicht erreichbarer
  Server (fail-closed) ⇒ Ordner bleibt.
- Worktree-Waisen mit totem ``gitdir:``, gelöschtem Hauptklon oder fehlendem Eintrag im
  Hauptklon (kopiert/verschoben vor ``worktree prune``) folgen derselben Regel.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test.
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from test_hausmeister_196 import haupt_bekannt  # noqa: F401
from test_hausmeister_1368 import Welt, welt  # noqa: F401

from to_spawn import hausmeister_windows

TAG = 24 * 3600.0
ALT_TAGE = 30


def _altern(welt: Welt) -> None:
    welt.jetzt += ALT_TAGE * TAG


def _ordner_weg(pfad: Path) -> None:
    """Löscht ``pfad`` auch unter Windows trotz Schreibschutz in ``.git/objects``."""
    if sys.version_info >= (3, 12):
        shutil.rmtree(pfad, onexc=hausmeister_windows.schreibschutz_weg)
    else:
        shutil.rmtree(pfad, onerror=hausmeister_windows.schreibschutz_weg)


def _kopie_ohne_git(welt: Welt, name: str, quelle: Path | None = None) -> Path:
    """Kopiert ``quelle`` (Standard: Hauptklon) ohne ``.git`` nach ``~/wt/<name>``."""
    ziel = welt.heim / "wt" / name
    shutil.copytree(quelle or welt.haupt, ziel, ignore=shutil.ignore_patterns(".git"))
    return ziel


def test_kopie_ohne_git_und_ohne_nummer_gleich_origin_commit_wird_geraeumt(
    haupt_bekannt: Welt,
) -> None:
    welt = haupt_bekannt
    kopie = _kopie_ohne_git(welt, "kopie-master")
    # Bekanntes Erzeugnis + kleine Test-DB daneben sind erlaubt
    (kopie / "__pycache__").mkdir()
    (kopie / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"pyc")
    (kopie / "accounts.db").write_bytes(b"sqlite-test")
    _altern(welt)

    trocken = welt.aufraeumen(kopie, loeschen=False)
    assert trocken.art == "gelöscht" and trocken.trocken and not trocken.fehler, trocken.grund
    assert "origin" in trocken.grund
    assert kopie.is_dir()

    echt = welt.aufraeumen(kopie, loeschen=True)
    assert echt.art == "gelöscht" and not echt.trocken and not echt.fehler, echt.grund
    assert not kopie.exists()


def test_kopie_ohne_git_mit_lokaler_aenderung_oder_extra_datei_bleibt(
    haupt_bekannt: Welt,
) -> None:
    welt = haupt_bekannt
    geaendert = _kopie_ohne_git(welt, "kopie-geaendert")
    (geaendert / "README.md").write_text("lokale arbeit\n", encoding="utf-8")

    extra = _kopie_ohne_git(welt, "kopie-extra")
    (extra / "notiz.txt").write_text("ungesichert\n", encoding="utf-8")
    _altern(welt)

    for ordner in (geaendert, extra):
        r = welt.aufraeumen(ordner, loeschen=True)
        assert r.art == "bleibt" and r.fuer_david and not r.fehler, r
        assert ordner.is_dir()


def test_kopie_ohne_git_mit_eingebettetem_repo_bleibt(haupt_bekannt: Welt) -> None:
    """Unterordner wie ``to-spawn/`` (normales Repo) oder ``basis/`` (bare Repo) halten den Ordner."""
    welt = haupt_bekannt
    mit_repo = _kopie_ohne_git(welt, "rest-mit-repo")
    subprocess.run(["git", "init", "-q", str(mit_repo / "to-spawn")], check=True)

    mit_bare = _kopie_ohne_git(welt, "rest-mit-bare")
    subprocess.run(["git", "init", "-q", "--bare", str(mit_bare / "basis")], check=True)
    _altern(welt)

    for ordner, unter in ((mit_repo, "to-spawn"), (mit_bare, "basis")):
        r = welt.aufraeumen(ordner, loeschen=True)
        assert r.art == "bleibt" and r.fuer_david and not r.fehler, r
        assert "eingebettetes Repo" in r.grund and unter in r.grund, r.grund
        assert ordner.is_dir()


def test_kopie_ohne_git_mit_geheimnis_oder_frisch_bleibt(
    haupt_bekannt: Welt,
) -> None:
    welt = haupt_bekannt
    # Frisch (< 24 h)
    frisch = _kopie_ohne_git(welt, "kopie-frisch")
    r_frisch = welt.aufraeumen(frisch, loeschen=True)
    assert r_frisch.art == "bleibt" and "frisch" in r_frisch.grund, r_frisch.grund
    assert frisch.is_dir()

    # Geheimnis
    geheim = _kopie_ohne_git(welt, "kopie-geheim")
    (geheim / ".env").write_text("TOKEN=geheim\n", encoding="utf-8")
    _altern(welt)

    r_geheim = welt.aufraeumen(geheim, loeschen=True)
    assert r_geheim.art == "bleibt" and r_geheim.fuer_david, r_geheim.grund
    assert (geheim / ".env").is_file()


def test_kopie_ohne_git_mit_laufendem_prozess_bleibt(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Echter Prozess im Ordner hält die Kopie (ohne Prozess-Naht, damit auch Windows echt prüft)."""
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", str(welt.haupt))
    aktiv = _kopie_ohne_git(welt, "kopie-aktiv")
    _altern(welt)

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=aktiv)
    try:
        r_aktiv = welt.aufraeumen(aktiv, loeschen=True)
        assert r_aktiv.art == "bleibt" and "Prozess" in r_aktiv.grund, r_aktiv.grund
        assert aktiv.is_dir()
    finally:
        proc.kill()
        proc.wait()


def test_worktree_waise_ohne_nummer_nach_prune_gleich_origin_wird_geraeumt(
    haupt_bekannt: Welt,
) -> None:
    """Worktree-Waise (``.git``-Datei da, Eintrag nach ``worktree prune`` weg): gleich origin ⇒ weg, geändert ⇒ bleibt."""
    welt = haupt_bekannt
    waise_gleich = _kopie_ohne_git(welt, "waise-gleich")
    (waise_gleich / ".git").write_text(
        f"gitdir: {welt.haupt / '.git' / 'worktrees' / 'waise-gleich'}\n",
        encoding="utf-8",
    )

    waise_geaendert = _kopie_ohne_git(welt, "waise-geaendert")
    (waise_geaendert / ".git").write_text(
        f"gitdir: {welt.tmp / 'geloeschter-klon' / '.git' / 'worktrees' / 'waise-geaendert'}\n",
        encoding="utf-8",
    )
    (waise_geaendert / "arbeit.txt").write_text("ungesichert\n", encoding="utf-8")
    _altern(welt)

    r_gleich = welt.aufraeumen(waise_gleich, loeschen=True)
    assert r_gleich.art == "gelöscht" and not r_gleich.fehler, r_gleich.grund
    assert not waise_gleich.exists()

    r_geaendert = welt.aufraeumen(waise_geaendert, loeschen=True)
    assert r_geaendert.art == "bleibt" and r_geaendert.fuer_david and not r_geaendert.fehler, r_geaendert
    assert waise_geaendert.is_dir()


def test_kopierter_worktree_nicht_im_hauptklon_eingetragen_ist_waise(
    haupt_bekannt: Welt,
) -> None:
    """Kopierter Worktree (``.git`` zeigt noch auf aktiven Nachbar-Eintrag): kein ``worktree remove``-Fehler."""
    welt = haupt_bekannt
    orig = welt.worktree(2410)
    kopie = welt.heim / "wt" / "kopie-wt-ohne-nr"
    shutil.copytree(orig, kopie)
    _altern(welt)

    r = welt.aufraeumen(kopie, loeschen=True)
    assert r.art == "gelöscht" and not r.fehler, r.grund
    assert not kopie.exists()
    assert orig.is_dir()  # Original bleibt unberührt


def test_kopie_ohne_git_origin_nicht_erreichbar_fail_closed(
    haupt_bekannt: Welt,
) -> None:
    """Server nicht erreichbar (``ls-remote`` scheitert) ⇒ fail-closed, Ordner bleibt mit Fehler."""
    welt = haupt_bekannt
    kopie = _kopie_ohne_git(welt, "kopie-offline")
    _altern(welt)
    _ordner_weg(welt.bare)  # origin weg

    r = welt.aufraeumen(kopie, loeschen=True)
    assert r.art == "bleibt" and r.fehler, r
    assert kopie.is_dir()


def test_kopie_und_origin_weg_trotz_windows_schreibschutz_in_git_objects(
    haupt_bekannt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows sperrt ``unlink`` auf schreibgeschützten ``.git/objects``-Dateien (WinError 5)."""
    welt = haupt_bekannt
    echt_unlink = os.unlink

    def unlink_wie_windows(pfad: str | os.PathLike[str], *, dir_fd: int | None = None) -> None:
        info = os.lstat(pfad) if dir_fd is None else os.lstat(pfad, dir_fd=dir_fd)
        if stat.S_ISREG(info.st_mode) and not (info.st_mode & stat.S_IWRITE):
            raise PermissionError(5, "Access is denied", str(pfad))
        if dir_fd is None:
            echt_unlink(pfad)
        else:
            echt_unlink(pfad, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", unlink_wie_windows)
    test_kopie_ohne_git_origin_nicht_erreichbar_fail_closed(welt)
