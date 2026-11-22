"""Hausmeister PR 233: Arbeitsordner eines Prozesses in anderer Pfadform hält den Ordner.

Review-Befund: Der Ordner kommt kanonisch an (``resolve()``), die cwd aus dem PEB so, wie
der Prozess sie gesetzt hat. Ein Git-Bash-``sleep`` mit cwd über eine Junction oder den
8.3-Kurzpfad galt als „frei“ — der Ordner wäre mit laufendem Prozess gelöscht worden.

Echte Git-Ordner, echte Junctions, echter MSYS-``sleep``; gestellt ist nur ``gh``.
Junction und 8.3-Kurzname gibt es nur unter Windows, darum laufen diese Tests nur dort
(unter Linux greift die cwd-Prüfung über ``/proc``, die hier nicht gemeint ist).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    Welt,
    welt,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Junction und 8.3-Kurzname gibt es nur unter Windows")


@pytest.fixture(autouse=True)
def eigenes_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))


@contextmanager
def _junction(verweis: Path, ziel: Path) -> Iterator[Path]:
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(verweis), str(ziel)],
        check=True,
        capture_output=True,
    )
    try:
        yield verweis
    finally:
        os.rmdir(verweis)  # nur der Verweis, nie das Ziel


@contextmanager
def _sleep_in(cwd: str | Path) -> Iterator[subprocess.Popen[bytes]]:
    proc = subprocess.Popen(["sleep", "60"], cwd=str(cwd))
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait()


def _kurzpfad(pfad: Path) -> str:
    import ctypes

    puffer = ctypes.create_unicode_buffer(1024)
    laenge = ctypes.windll.kernel32.GetShortPathNameW(str(pfad), puffer, 1024)  # type: ignore[attr-defined]
    return puffer.value if 0 < laenge < 1024 else ""


def test_sleep_ueber_junction_haelt_ordner(welt: Welt) -> None:
    wt = welt.worktree(2331)
    welt.ticket(2331, "CLOSED", 48)
    with _junction(welt.tmp / "verweis-auf-wt", wt) as verweis, _sleep_in(verweis):
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and "Prozess" in r.grund, r.grund
        assert wt.is_dir()


def test_sleep_ueber_kurzpfad_haelt_ordner(welt: Welt) -> None:
    wt = welt.worktree(2332)
    welt.ticket(2332, "CLOSED", 48)
    kurz = _kurzpfad(wt)
    if not kurz or os.path.normcase(kurz) == os.path.normcase(str(wt)):
        pytest.skip("Dateisystem liefert keinen 8.3-Kurznamen (8dot3name aus) — Fall hier nicht prüfbar")
    with _sleep_in(kurz):
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and "Prozess" in r.grund, r.grund
        assert wt.is_dir()


def test_sleep_ueber_junction_im_nachbarordner_mit_gleichem_praefix_haelt_nicht(
    welt: Welt,
) -> None:
    wt = welt.worktree(2333)
    welt.ticket(2333, "CLOSED", 48)
    nachbar = wt.with_name(wt.name + "0")  # wt-23330: gleiches Text-Präfix
    nachbar.mkdir()
    with _junction(welt.tmp / "verweis-auf-nachbar", nachbar) as verweis, _sleep_in(verweis):
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "gelöscht", r.grund
        assert not wt.exists()
        assert nachbar.is_dir()


def _mklink_j(verweis: Path, ziel: Path) -> None:
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(verweis), str(ziel)],
        check=True,
        capture_output=True,
    )


def _verweis_weg(verweis: Path) -> None:
    """Entfernt nur den Verweis (Junction), falls er noch da ist — nie das Ziel."""
    if os.path.lexists(verweis):
        os.rmdir(verweis)


@contextmanager
def _sleep_laeuft_in(cwd: str | Path) -> Iterator[subprocess.Popen[bytes]]:
    """Wie ``_sleep_in``, wartet aber, bis MSYS die cwd gesetzt hat.

    Ohne die Pause wäre die Junction schon weg oder umgebogen, bevor der Prozess sie
    aufgelöst hat — dann hielte er gar nicht den Ordner, den der Test meint.
    """
    with _sleep_in(cwd) as proc:
        time.sleep(1.5)
        assert proc.poll() is None, "sleep lief nicht an"
        yield proc


def _unc_loopback(pfad: Path) -> str:
    s = chr(92)
    text = str(pfad)
    return s * 2 + "localhost" + s + text[0] + "$" + s + text[3:]


def test_sleep_ueber_entfernte_junction_haelt_ordner(welt: Welt) -> None:
    wt = welt.worktree(2334)
    welt.ticket(2334, "CLOSED", 48)
    verweis = welt.tmp / "verweis-entfernt"
    _mklink_j(verweis, wt)
    try:
        with _sleep_laeuft_in(verweis):
            _verweis_weg(verweis)  # cwd-Text zeigt jetzt ins Leere, der Prozess sitzt weiter in wt
            assert wt.is_dir()
            r = welt.aufraeumen(wt, loeschen=True)
            assert r.art == "bleibt" and "Prozess" in r.grund, r.grund
            assert wt.is_dir()
    finally:
        _verweis_weg(verweis)


def test_sleep_ueber_umgebogene_junction_haelt_ordner(welt: Welt) -> None:
    wt = welt.worktree(2335)
    welt.ticket(2335, "CLOSED", 48)
    anderes = welt.tmp / "anderes-ziel"
    anderes.mkdir()
    verweis = welt.tmp / "verweis-umgebogen"
    _mklink_j(verweis, wt)
    try:
        with _sleep_laeuft_in(verweis):
            _verweis_weg(verweis)
            _mklink_j(verweis, anderes)  # cwd-Text zeigt jetzt auf einen fremden Ordner
            r = welt.aufraeumen(wt, loeschen=True)
            assert r.art == "bleibt" and "Prozess" in r.grund, r.grund
            assert wt.is_dir()
            assert anderes.is_dir()
    finally:
        _verweis_weg(verweis)


def test_sleep_ueber_unc_loopback_haelt_ordner(welt: Welt) -> None:
    if not os.path.isdir(_unc_loopback(welt.tmp)):
        pytest.skip("Admin-Freigabe des Laufwerks (z. B. C$) über localhost fehlt — Fall hier nicht prüfbar")
    wt = welt.worktree(2336)
    welt.ticket(2336, "CLOSED", 48)
    with _sleep_laeuft_in(_unc_loopback(wt)):
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and "Prozess" in r.grund, r.grund
        assert wt.is_dir()


def test_sleep_im_unterordner_haelt_ordner(welt: Welt) -> None:
    wt = welt.worktree(2337)
    welt.ticket(2337, "CLOSED", 48)
    tief = wt / "sub" / "tief"
    tief.mkdir(parents=True)
    with _sleep_laeuft_in(tief):
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and "Prozess" in r.grund, r.grund
        assert wt.is_dir()


def test_sleep_im_nachbarordner_mit_gleichem_praefix_haelt_nicht(welt: Welt) -> None:
    wt = welt.worktree(2338)
    welt.ticket(2338, "CLOSED", 48)
    nachbar = wt.with_name(wt.name + "0")  # wt-23380: gleiches Text-Präfix, echter Pfad
    nachbar.mkdir()
    with _sleep_laeuft_in(nachbar):
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "gelöscht", r.grund
        assert not wt.exists()
        assert nachbar.is_dir()


def test_textform_vorsatz_und_pfad_teile() -> None:
    from to_spawn import hausmeister_windows as hw

    s = chr(92)
    assert hw.pfad_norm(s * 2 + "?" + s + "C:" + s + "Dev" + s + "WT-12" + s) == "c:/dev/wt-12"
    assert hw.pfad_norm(s * 2 + "?" + s + "UNC" + s + "srv" + s + "f") == "//srv/f"
    assert hw._liegt_in("c:/dev/wt-12/a", "c:/dev/wt-12")
    assert not hw._liegt_in("c:/dev/wt-123", "c:/dev/wt-12")
