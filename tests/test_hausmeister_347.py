"""Hausmeister #347: Rest-Lücken aus den unabhängigen Prüfungen zu PR #352.

Ergänzt ``tests/test_hausmeister_luecken_347_254.py`` (W1–W4 bereits auf ``main``):
- ``extensions.refStorage`` ≠ ``files`` in ``_nur_im_reflog`` ⇒ Ordner bleibt (``fuer_david``).
- Unlesbare Reflog-Datei, unlesbare Reflog-Zeile und gesperrte Unterordner unter ``logs/`` bzw.
  ``worktrees/*/logs`` ⇒ Ordner bleibt mit Fehler (auch unter Python 3.13, wo ``rglob``
  unlesbare Ordner still überspränge).
- Normaler Klon liest alle Reflogs (z. B. ``logs/refs/heads/<zweig>``), nicht nur ``logs/HEAD``.
- Konfig-Scope ``worktree`` bei ``url.<x>.insteadOf`` ⇒ gilt als fremd (``_origin_slug == ""``).
- Umleitung der Maschine auf ein fremdes URL-Schema (``<schema>://``, startet ``git-remote-<x>``)
  ⇒ gilt nicht als GitHub.
- Ordner mit Datums-Zweig (``ticket/2026-13-x``) läuft über den Weg „ohne Nummer“: ungesichert
  bleibt er stehen, sauber + alt + auf ``origin`` darf er weg.
"""

# ruff: noqa: F811 — ``welt``/``repos`` kommen als Fixtures aus den #1368-/#176-Tests.
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_hausmeister_235_fix2 import _alt
from test_hausmeister_1368 import ORIGIN, Welt, git, welt  # noqa: F401 — Fixture
from test_hausmeister_ticket_repo_176 import APP, Repos, repos  # noqa: F401

from to_spawn import hausmeister


def _commit(ordner: Path, datei: str, inhalt: str | None = None) -> str:
    (ordner / datei).write_text(inhalt if inhalt is not None else f"{datei}\n", encoding="utf-8")
    git(ordner, "add", datei)
    git(ordner, "commit", "-q", "-m", datei)
    return git(ordner, "rev-parse", "HEAD").strip()


def _git_dir(repo: Path) -> Path:
    return Path(git(repo, "rev-parse", "--absolute-git-dir").strip())


def _ohne_root() -> None:
    if sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("POSIX-Dateirechte ohne root nötig")


# --- Reflog-Schutz: unlesbare Ordner, Dateien, Zeilen, refStorage, alle Reflogs ------------


def test_347_ref_storage_nicht_files_haelt_ordner(welt: Welt) -> None:
    """``extensions.refStorage`` ≠ ``files`` (z. B. ``reftable``) ⇒ Verlauf nicht prüfbar ⇒ bleibt."""
    wt = welt.worktree(3471)
    welt.ticket(3471, "CLOSED", 48)
    # Konfig-Wert wie bei reftable-Repos (ohne dass git beim Lesen abstürzt)
    echt = hausmeister._git_oder_none

    def mit_reftable(cwd: Path, *args: str) -> str | None:
        if args == ("config", "--get", "extensions.refStorage"):
            return "reftable"
        return echt(cwd, *args)

    # Direkter Beweis auf _nur_im_reflog und über die öffentliche Tür aufraeumen()
    original = hausmeister._git_oder_none
    hausmeister._git_oder_none = mit_reftable  # type: ignore[assignment]
    try:
        with pytest.raises(hausmeister._Bleibt, match="Ref-Speicher") as exc:
            hausmeister._nur_im_reflog(wt, True)
        assert exc.value.fuer_david
        r = welt.aufraeumen(wt, loeschen=True)
    finally:
        hausmeister._git_oder_none = original  # type: ignore[assignment]

    assert r.art == "bleibt" and r.fuer_david, r
    assert "Ref-Speicher" in r.grund, r.grund
    assert wt.is_dir()


def test_347_echtes_reftable_repo_bleibt(tmp_path: Path) -> None:
    """Echtes ``git init --ref-format=reftable`` (ab git 2.45): ``_nur_im_reflog`` lehnt ab."""
    repo = tmp_path / "reftable-repo"
    lauf = subprocess.run(
        ["git", "init", "-q", "--ref-format=reftable", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )
    if lauf.returncode != 0:
        pytest.skip(f"git unterstützt --ref-format=reftable nicht: {lauf.stderr.strip()}")
    with pytest.raises(hausmeister._Bleibt, match="Ref-Speicher") as exc:
        hausmeister._nur_im_reflog(repo, False)
    assert exc.value.fuer_david


@pytest.mark.braucht("posix")
def test_347_reflog_datei_unlesbar_bleibt(welt: Welt) -> None:
    """Eine unlesbare Reflog-Datei (``logs/HEAD`` ohne Leserecht) ⇒ bleibt mit Fehler."""
    _ohne_root()
    wt = welt.worktree(3472)
    welt.ticket(3472, "CLOSED", 48)
    head_log = _git_dir(wt) / "logs" / "HEAD"
    assert head_log.is_file()
    head_log.chmod(0o000)
    try:
        r = welt.aufraeumen(wt, loeschen=True)
    finally:
        head_log.chmod(0o644)

    assert r.art == "bleibt" and r.fehler, r
    assert "Reflog nicht lesbar" in r.grund, r.grund
    assert wt.is_dir()


def test_347_unlesbare_reflog_zeile_bleibt(welt: Welt) -> None:
    """Kaputte Zeile in einer Reflog-Datei ⇒ bleibt mit Fehler (fail-closed)."""
    wt = welt.worktree(3473)
    welt.ticket(3473, "CLOSED", 48)
    head_log = _git_dir(wt) / "logs" / "HEAD"
    head_log.write_text("kaputte zeile ohne shas\n", encoding="utf-8")

    r = welt.aufraeumen(wt, loeschen=True)

    assert r.art == "bleibt" and r.fehler, r
    assert "unlesbare Reflog-Zeile" in r.grund, r.grund
    assert wt.is_dir()


@pytest.mark.braucht("posix")
def test_347_gesperrter_unterordner_in_logs_bleibt(welt: Welt) -> None:
    """Unter Python 3.13 überspringt ``rglob`` unlesbare Unterordner still — ein gesperrter
    Unterordner unter ``logs/`` muss trotzdem fail-closed ``_Bleibt`` mit Fehler auslösen."""
    _ohne_root()
    klon = welt.klon("duoplus-3474")
    welt.ticket(3474, "CLOSED", 48)
    gesperrt = _git_dir(klon) / "logs" / "refs" / "heads"
    assert gesperrt.is_dir()
    gesperrt.chmod(0o000)
    try:
        with pytest.raises(hausmeister._Bleibt, match="Reflog nicht lesbar") as exc:
            hausmeister._nur_im_reflog(klon, False)
        assert exc.value.fehler
        r = welt.aufraeumen(klon, loeschen=True)
    finally:
        gesperrt.chmod(0o755)

    assert r.art == "bleibt" and r.fehler, r
    assert "Reflog nicht lesbar" in r.grund, r.grund
    assert klon.is_dir()


@pytest.mark.braucht("posix")
def test_347_gesperrter_unterordner_in_worktrees_logs_bleibt(welt: Welt) -> None:
    """Gesperrter ``worktrees/<wt>/logs``-Ordner in einem Klon ⇒ fail-closed mit Fehler."""
    _ohne_root()
    klon = welt.klon("duoplus-3475")
    nebenan = welt.tmp / "nebenan-3475"
    git(klon, "worktree", "add", "-q", "--detach", str(nebenan), "origin/master")
    _commit(nebenan, "arbeit.txt")
    shutil.rmtree(nebenan)
    wt_logs = next((_git_dir(klon) / "worktrees").iterdir()) / "logs"
    assert wt_logs.is_dir()
    wt_logs.chmod(0o000)
    try:
        with pytest.raises(hausmeister._Bleibt, match="Reflog nicht lesbar") as exc:
            hausmeister._nur_im_reflog(klon, False)
        assert exc.value.fehler
    finally:
        wt_logs.chmod(0o755)


def test_347_normaler_klon_liest_alle_reflogs_nicht_nur_head(welt: Welt) -> None:
    """Commit steht nur im Zweig-Reflog (``logs/refs/heads/merk``), nie in ``logs/HEAD``:
    beim Klon stirbt ``logs/refs/`` mit dem Ordner ⇒ Klon muss bleiben."""
    klon = welt.klon("duoplus-3476")
    welt.ticket(3476, "CLOSED", 48)
    sha = git(klon, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "nur im zweig-reflog").strip()
    # Direkt per update-ref am Zweig (HEAD-Reflog sieht ``sha`` nie)
    git(klon, "update-ref", "--create-reflog", "refs/heads/merk", sha)
    git(klon, "update-ref", "refs/heads/merk", "HEAD")
    head_log_text = (_git_dir(klon) / "logs" / "HEAD").read_text(encoding="utf-8")
    assert sha not in head_log_text, "Vorbedingung: SHA darf nur im Zweig-Reflog stehen"

    assert hausmeister._nur_im_reflog(klon, False) is True
    r = welt.aufraeumen(klon, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david, r
    assert "Reflog" in r.grund, r.grund
    assert klon.is_dir()


# --- Umleitungs-Prüfung: worktree-Scope und git-remote-<x> URL-Schema ----------------------


def test_347_worktree_config_scope_instead_of_ist_fremd(welt: Welt) -> None:
    """``url.<x>.insteadOf`` im Konfig-Scope ``worktree`` ⇒ ``_origin_slug`` liefert ``""``."""
    wt = welt.worktree(3477)
    git(welt.haupt, "config", "extensions.worktreeConfig", "true")
    assert hausmeister._origin_slug(wt) == APP  # Gegenprobe vor Umleitung
    git(wt, "config", "--worktree", f"url.{welt.bare.as_posix()}.insteadOf", ORIGIN)

    assert hausmeister._origin_slug(wt) == ""


def test_347_umleitung_auf_fremdes_url_schema_ist_kein_github(welt: Welt) -> None:
    """Maschinen-Umleitung auf ein eigenes URL-Schema (``meinhelfer://…`` → ``git-remote-meinhelfer``)
    darf nicht mehr als GitHub gelten."""
    klon = welt.klon("duoplus-schema")
    git(klon, "config", "remote.origin.url", ORIGIN + ".git")
    assert hausmeister._origin_slug(klon) == APP  # Gegenprobe: Spiegel-Pfad gilt
    with (welt.tmp / "gitconfig").open("a", encoding="utf-8") as f:
        f.write(f'[url "meinhelfer://spiegel/repo"]\n\tinsteadOf = {ORIGIN}.git\n')

    assert hausmeister._origin_slug(klon) == ""


# --- Datums-Zweig im Ordner-Pfad -----------------------------------------------------------


def test_347_ordner_mit_datums_zweig_laeuft_ueber_ohne_nummer(welt: Welt) -> None:
    """Ein Ordner auf ``ticket/2026-13-x`` (Datums-Form → keine Ticket-Nummer) bleibt mit
    ungesicherten Änderungen stehen und darf erst sauber, alt und auf ``origin`` weg."""
    klon = welt.klon("duoplus-datum")
    git(klon, "checkout", "-q", "-b", "ticket/2026-13-x")
    _commit(klon, "lokal.txt")
    _alt(welt)

    # Ungesichert: bleibt mit „keine Ticket-Nummer“
    r1 = welt.aufraeumen(klon, loeschen=True)
    assert r1.art == "bleibt" and r1.ticket is None, r1
    assert hausmeister.OHNE_NUMMER in r1.grund, r1.grund
    assert klon.is_dir()

    # Auf origin gepusht, sauber und alt: Weg „ohne Nummer“ räumt ihn weg
    git(klon, "push", "-q", "-u", "origin", "ticket/2026-13-x")
    _alt(welt)
    r2 = welt.aufraeumen(klon, loeschen=True)
    assert r2.art == "gelöscht" and r2.ticket is None, r2
    assert hausmeister.OHNE_NUMMER in r2.grund, r2.grund
    assert not klon.exists()
