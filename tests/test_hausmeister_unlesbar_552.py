"""#552: Ein Prozess fremder Sitzung oder fremden Benutzers, dessen cwd sich nicht lesen lässt,
sperrt das Aufräumen nicht mehr; eigene unlesbare Prozesse und Prozesse im Ordner sperren weiter.

Nur auf echtem Windows: die cwd kommt dort aus dem PEB (:func:`prozessbaum.arbeitsordner_in_windows`).
"""

# ruff: noqa: F811 — ``welt`` ist ein importiertes Fixture
from __future__ import annotations

import os
import subprocess
import sys

import pytest
from test_hausmeister_1368 import Welt, welt  # noqa: F401 — Fixture
from to_spawn import prozessbaum

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="PEB-Weg nur unter Windows"
)


def _unlesbar(monkeypatch: pytest.MonkeyPatch, pid: int) -> None:
    """cwd von ``pid`` wie bei einem geschützten Prozess: offen, PEB aber nicht lesbar."""
    echt = prozessbaum._arbeitsordner_windows

    def peb(p: int) -> str | None:
        if p == pid:
            raise OSError(299, "ReadProcessMemory gescheitert")
        return echt(p)

    monkeypatch.setattr(prozessbaum, "_arbeitsordner_windows", peb)


@pytest.mark.parametrize("fremd", ["sitzung", "benutzer"])
def test_unlesbarer_fremdprozess_sperrt_nicht(
    welt: Welt, monkeypatch: pytest.MonkeyPatch, fremd: str
) -> None:
    wt = welt.worktree(552)
    welt.ticket(552, "CLOSED", 48)
    proc = subprocess.Popen(["sleep", "60"], cwd=welt.tmp)
    try:
        _unlesbar(monkeypatch, proc.pid)
        naht = "_sitzung_windows" if fremd == "sitzung" else "_benutzer_windows"
        echt = getattr(prozessbaum, naht, None)
        anders = 4242 if fremd == "sitzung" else b"fremde-sid"

        def wert(pid: int) -> object:
            return anders if pid == proc.pid else echt(pid)  # type: ignore[misc]

        monkeypatch.setattr(prozessbaum, naht, wert, raising=False)
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "gelöscht", r.grund
        assert not wt.exists()
    finally:
        proc.kill()
        proc.wait()


def test_unlesbarer_eigener_prozess_bleibt(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt = welt.worktree(553)
    welt.ticket(553, "CLOSED", 48)
    proc = subprocess.Popen(["sleep", "60"], cwd=welt.tmp)
    try:
        _unlesbar(monkeypatch, proc.pid)
        assert not prozessbaum._ist_fremd_windows(proc.pid)
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and str(proc.pid) in r.grund, r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait()


def test_eigener_prozess_im_ordner_bleibt_trotz_unlesbarem_fremdprozess(
    welt: Welt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wt = welt.worktree(554)
    welt.ticket(554, "CLOSED", 48)
    drin = subprocess.Popen(["sleep", "60"], cwd=wt)
    fremd = subprocess.Popen(["sleep", "60"], cwd=welt.tmp)
    try:
        _unlesbar(monkeypatch, fremd.pid)
        echt = prozessbaum._sitzung_windows
        monkeypatch.setattr(
            prozessbaum,
            "_sitzung_windows",
            lambda p: 4242 if p == fremd.pid else echt(p),
        )
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and str(drin.pid) in r.grund, r.grund
        assert str(fremd.pid) not in r.grund, r.grund
        assert wt.is_dir()
    finally:
        for p in (drin, fremd):
            p.kill()
            p.wait()


@pytest.mark.parametrize("seite", ["fremd", "eigen", "beide"])
def test_nicht_feststellbar_gilt_als_eigen(
    monkeypatch: pytest.MonkeyPatch, seite: str
) -> None:
    """Sitzung/Benutzer auf einer Seite unbekannt (``None``) ⇒ nie fremd, sperrt weiter."""
    eigen, anderer = os.getpid(), 999_999

    def unbekannt(echt_wert: object, anders: object):
        def wert(pid: int) -> object:
            if (pid == anderer and seite in ("fremd", "beide")) or (
                pid == eigen and seite in ("eigen", "beide")
            ):
                return None
            return anders if pid == anderer else echt_wert

        return wert

    monkeypatch.setattr(prozessbaum, "_sitzung_windows", unbekannt(1, 2))
    monkeypatch.setattr(prozessbaum, "_benutzer_windows", unbekannt(b"ich", b"du"))
    assert not prozessbaum._ist_fremd_windows(anderer)


def test_leere_cwd_eines_fremdprozesses_sperrt(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startphase (cwd im PEB noch leer) sperrt auch bei fremder Sitzung."""
    wt = welt.worktree(555)
    welt.ticket(555, "CLOSED", 48)
    proc = subprocess.Popen(["sleep", "60"], cwd=welt.tmp)
    try:
        echt = prozessbaum._arbeitsordner_windows

        def peb(p: int) -> str | None:
            if p == proc.pid:
                raise prozessbaum._LeereCwd(f"Prozess {p}: leere cwd im PEB")
            return echt(p)

        monkeypatch.setattr(prozessbaum, "_arbeitsordner_windows", peb)
        sitzung = prozessbaum._sitzung_windows
        monkeypatch.setattr(
            prozessbaum,
            "_sitzung_windows",
            lambda p: 4242 if p == proc.pid else sitzung(p),
        )
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and str(proc.pid) in r.grund, r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait()


def test_eigener_benutzer_und_sitzung_lesbar() -> None:
    """Selbstprobe der Nähte: der eigene Prozess ist nie fremd."""
    assert prozessbaum._sitzung_windows(os.getpid()) is not None
    assert prozessbaum._benutzer_windows(os.getpid())
    assert not prozessbaum._ist_fremd_windows(os.getpid())
