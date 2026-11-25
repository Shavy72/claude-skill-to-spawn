"""Windows-Räumer für ``pt-*``-Temp-Ordner mit schreibgeschützten ``.git/objects`` (#655).

Unter Windows setzt Git das Schreibschutz-Bit auf Dateien unter ``.git/objects``. Sowohl
``pytest_sessionfinish`` (mit ``ignore_errors=True``) als auch ``_reste_raeumen`` scheiterten
daran mit ``WinError 5`` und ließen ``pt-*``-Ordner im Temp-Ordner zurück.

Beide Wege nutzen jetzt eine gemeinsame Lösch-Hilfe, die unter Windows den vorhandenen
Handler :func:`to_spawn.hausmeister_windows.schreibschutz_weg` an ``shutil.rmtree`` gibt.
"""

from __future__ import annotations

import errno
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_pt_reste_177 import (
    _KIND_CONFTEST,
    _env,
    _pt_ordner,
    _rest,
    _start,
    _tote_pid,
    _warten,
)

import conftest

_ALS_ROOT_POSIX = sys.platform != "win32" and getattr(os, "geteuid", lambda: 1)() == 0

_KIND_TEST_GIT = """
import subprocess


def test_git_objekte(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "datei.txt").write_text("inhalt\\n", encoding="utf-8")
    for befehl in (
        ["git", "init", "-q"],
        ["git", "add", "datei.txt"],
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
    ):
        subprocess.run(befehl, cwd=repo, check=True, capture_output=True)
    assert any((repo / ".git" / "objects").rglob("*"))
"""


def _schreibgeschuetztes_git_objekt(basis: Path) -> tuple[Path, Path]:
    """Legt eine schreibgeschützte Objekt-Datei wie ``.git/objects`` unter Windows an."""
    pack = basis / "test_x0" / "repo" / ".git" / "objects" / "pack"
    pack.mkdir(parents=True, exist_ok=True)
    datei = pack / "pack-test.idx"
    datei.write_bytes(b"git-objekt")
    datei.chmod(0o444)
    pack.chmod(0o555)
    return pack, datei


def _rechte_zurueck(pack: Path, datei: Path) -> None:
    if pack.exists():
        pack.chmod(0o755)
    if datei.exists():
        datei.chmod(0o644)


def _session_mit_basetemp(ordner: Path) -> SimpleNamespace:
    stash = pytest.Stash()
    stash[conftest._EIGENER_BASETEMP] = ordner
    return SimpleNamespace(config=SimpleNamespace(stash=stash))


@pytest.mark.skipif(_ALS_ROOT_POSIX, reason="root löscht trotz fehlender POSIX-Rechte")
def test_reste_raeumen_loescht_schreibgeschuetzte_git_objekte_unter_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rest = _rest(tmp_path, "pt-gitobj1", _tote_pid(), alt=True)
    pack, datei = _schreibgeschuetztes_git_objekt(rest)
    monkeypatch.setattr(conftest, "_WINDOWS", True)
    try:
        conftest._reste_raeumen(tmp_path)
        assert not rest.exists(), "alter pt-Rest mit schreibgeschützten .git/objects muss weg sein"
    finally:
        _rechte_zurueck(pack, datei)


@pytest.mark.skipif(_ALS_ROOT_POSIX, reason="root löscht trotz fehlender POSIX-Rechte")
def test_sessionfinish_loescht_schreibgeschuetzte_git_objekte_unter_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ordner = tmp_path / "pt-gruen01"
    pack, datei = _schreibgeschuetztes_git_objekt(ordner)
    session = _session_mit_basetemp(ordner)
    monkeypatch.setattr(conftest, "_WINDOWS", True)
    try:
        conftest.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
        assert not ordner.exists(), "grüner Lauf muss eigenen pt-Ordner trotz Schreibschutz löschen"
    finally:
        _rechte_zurueck(pack, datei)


def test_sessionfinish_loggt_echten_loeschfehler_ohne_abbruch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ordner = tmp_path / "pt-gesperrt1"
    ordner.mkdir()
    session = _session_mit_basetemp(ordner)

    def _scheitert(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EACCES, "gesperrt", str(ordner))

    monkeypatch.setattr(conftest.shutil, "rmtree", _scheitert)
    with caplog.at_level(logging.WARNING, logger=conftest._LOG.name):
        conftest.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
    assert any("pt-gesperrt1" in satz.message for satz in caplog.records), caplog.messages


def test_sessionfinish_schon_geloeschter_ordner_bleibt_still(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ordner = tmp_path / "pt-weg0001"
    session = _session_mit_basetemp(ordner)
    with caplog.at_level(logging.WARNING, logger=conftest._LOG.name):
        conftest.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
    assert caplog.records == []


@pytest.mark.braucht("git")
def test_gruener_kindlauf_mit_echtem_git_repo_hinterlaesst_keinen_pt_ordner(tmp_path: Path) -> None:
    """Echter Kind-Lauf mit ``git init`` + ``git commit`` im ``tmp_path``: danach 0 ``pt-*``."""
    temp = tmp_path / "t"
    temp.mkdir()
    welt = tmp_path / "welt"
    welt.mkdir()
    (welt / "conftest.py").write_text(_KIND_CONFTEST, encoding="utf-8")
    (welt / "test_git.py").write_text(_KIND_TEST_GIT, encoding="utf-8")
    code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "a.txt")))
    assert code == 0, ausgabe
    assert _pt_ordner(temp) == [], f"pt-Reste nach grünem Git-Lauf: {_pt_ordner(temp)}"
