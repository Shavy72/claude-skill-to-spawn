"""#552: Ein Prozess eines fremden Benutzers, dessen cwd sich nicht lesen lässt,
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

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PEB-Weg nur unter Windows")


def _unlesbar(monkeypatch: pytest.MonkeyPatch, pid: int) -> None:
    """cwd von ``pid`` wie bei einem geschützten Prozess: offen, PEB aber nicht lesbar."""
    echt = prozessbaum._arbeitsordner_windows

    def peb(p: int) -> str | None:
        if p == pid:
            raise OSError(299, "ReadProcessMemory gescheitert")
        return echt(p)

    monkeypatch.setattr(prozessbaum, "_arbeitsordner_windows", peb)


def _fremder_benutzer(monkeypatch: pytest.MonkeyPatch, pid: int) -> None:
    """``pid`` gehört einem anderen Benutzer (andere SID)."""
    echt = prozessbaum._benutzer_windows
    monkeypatch.setattr(
        prozessbaum,
        "_benutzer_windows",
        lambda p: b"fremde-sid" if p == pid else echt(p),
    )


def test_unlesbarer_prozess_fremden_benutzers_sperrt_nicht(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = welt.worktree(552)
    welt.ticket(552, "CLOSED", 48)
    proc = subprocess.Popen(["sleep", "60"], cwd=welt.tmp)
    try:
        _unlesbar(monkeypatch, proc.pid)
        _fremder_benutzer(monkeypatch, proc.pid)
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "gelöscht", r.grund
        assert not wt.exists()
    finally:
        proc.kill()
        proc.wait()


def test_gleicher_benutzer_andere_sitzung_unlesbar_bleibt(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Eigener Benutzer ist nie fremd, auch in anderer Sitzung (ssh, Aufgabenplanung)."""
    wt = welt.worktree(556)
    welt.ticket(556, "CLOSED", 48)
    proc = subprocess.Popen(["sleep", "60"], cwd=welt.tmp)
    try:
        _unlesbar(monkeypatch, proc.pid)
        # eine Sitzungs-Naht darf nie entscheiden; gäbe es sie, meldete sie hier „andere Sitzung“
        monkeypatch.setattr(
            prozessbaum,
            "_sitzung_windows",
            lambda p: 4242 if p == proc.pid else 1,
            raising=False,
        )
        assert prozessbaum._benutzer_windows(proc.pid) == prozessbaum._benutzer_windows(os.getpid())
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and str(proc.pid) in r.grund, r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait()


def test_unlesbarer_eigener_prozess_bleibt(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
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
        _fremder_benutzer(monkeypatch, fremd.pid)
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and str(drin.pid) in r.grund, r.grund
        assert str(fremd.pid) not in r.grund, r.grund
        assert wt.is_dir()
    finally:
        for p in (drin, fremd):
            p.kill()
            p.wait()


@pytest.mark.parametrize("seite", ["fremd", "eigen", "beide"])
def test_nicht_feststellbar_gilt_als_eigen(monkeypatch: pytest.MonkeyPatch, seite: str) -> None:
    """Benutzer auf einer Seite unbekannt (``None``) ⇒ nie fremd, sperrt weiter."""
    eigen, anderer = os.getpid(), 999_999

    def unbekannt(echt_wert: object, anders: object):
        def wert(pid: int) -> object:
            if (pid == anderer and seite in ("fremd", "beide")) or (pid == eigen and seite in ("eigen", "beide")):
                return None
            return anders if pid == anderer else echt_wert

        return wert

    monkeypatch.setattr(prozessbaum, "_benutzer_windows", unbekannt(b"ich", b"du"))
    assert not prozessbaum._ist_fremd_windows(anderer)


def test_leere_cwd_eines_fremdprozesses_sperrt(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Startphase (cwd im PEB noch leer) sperrt auch bei fremdem Benutzer."""
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
        _fremder_benutzer(monkeypatch, proc.pid)
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and str(proc.pid) in r.grund, r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait()


def test_msys_zugriff_verweigert_eines_fremdprozesses_sperrt(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """#243 × #552: MSYS/Cygwin-Prozess mit verweigertem Zugriff sperrt auch bei fremdem Benutzer."""
    wt = welt.worktree(557)
    welt.ticket(557, "CLOSED", 48)
    proc = subprocess.Popen(["sleep", "60"], cwd=welt.tmp, **prozessbaum.ohne_fenster())
    try:
        echt = prozessbaum._arbeitsordner_windows

        def peb(p: int) -> str | None:
            if p == proc.pid:
                raise prozessbaum._MsysZugriffVerweigert(
                    prozessbaum._FEHLER_ZUGRIFF_VERWEIGERT,
                    f"Prozess {p}: Zugriff verweigert, MSYS/Cygwin-cwd unlesbar",
                )
            return echt(p)

        monkeypatch.setattr(prozessbaum, "_arbeitsordner_windows", peb)
        _fremder_benutzer(monkeypatch, proc.pid)
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and str(proc.pid) in r.grund, r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait()


def test_eigener_benutzer_lesbar() -> None:
    """Selbstprobe der Naht: der eigene Prozess ist nie fremd."""
    assert prozessbaum._benutzer_windows(os.getpid())
    assert not prozessbaum._ist_fremd_windows(os.getpid())
