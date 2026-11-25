"""#654: Verschwindender Prozess unter Windows zählt als weg statt „nicht prüfbar“.

Folge aus #649 / Pull-Request #653: Endet ein Prozess zwischen Auflisten und
PEB-/Griff-Prüfung in :func:`to_spawn.prozessbaum.arbeitsordner_in_windows`, darf er
den Hausmeister nicht blockieren. Echte Zugriffsverweigerung (``ERROR_ACCESS_DENIED``)
oder ein lebender Prozess mit unlesbarem PEB bzw. dauerhaft fehlendem cwd-Griff
bleiben auf der sicheren Seite (``drin=None`` → Ordner bleibt).

Der Kernel-Teil läuft auf Linux und Windows über die ``_win32``-Naht (wie #154);
die echten Prozess-Fälle am Ende laufen nur auf Windows.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test.
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from test_hausmeister_1368 import Welt, welt  # noqa: F401 — Fixture

from to_spawn import hausmeister_windows, prozessbaum

STILL_ACTIVE = prozessbaum._STILL_ACTIVE
ZUGRIFF_VERWEIGERT = prozessbaum._FEHLER_ZUGRIFF_VERWEIGERT
UNGUELTIGER_PARAMETER = prozessbaum._FEHLER_UNGUELTIGER_PARAMETER
TEILKOPIE = 299  # ERROR_PARTIAL_COPY
UNGUELTIGER_GRIFF = 6  # ERROR_INVALID_HANDLE

_PEB_BASIS = 0x10000
_PARAMS_BASIS = 0x20000
_CWD_PUFFER = 0x30000


def _setze(ref: Any, wert: int) -> None:
    """Schreibt ``wert`` in das Ziel eines ``ctypes.byref``-Arguments."""
    ref._obj.value = wert


@dataclass
class _FakeProzess:
    """Zustand eines gestellten Windows-Prozesses für die PEB-/Griff-Prüfung."""

    cwd: str = "C:\\anders"
    cwd_griff: int = 0x40
    exit_code: int = STILL_ACTIVE
    existiert: bool = True
    open_fehler: dict[int, int] = field(default_factory=dict)
    dup_fehler: int = 0
    speicher_fehler: int = 0
    bei_speicher: Callable[[_FakeProzess, int], None] | None = None
    bei_curdir: Callable[[_FakeProzess, int], None] | None = None
    bei_dup: Callable[[_FakeProzess, int], None] | None = None
    speicher_aufrufe: int = 0
    curdir_aufrufe: int = 0
    dup_aufrufe: int = 0


class _WinWelt:
    """Stellt ``kernel32``/``ntdll``-Aufrufe für :func:`arbeitsordner_in_windows` nach."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, prozesse: dict[int, _FakeProzess]) -> None:
        self.prozesse = prozesse
        self.letzter_fehler = 0
        self.naechster_griff = 100
        self.offene_griffe: dict[int, tuple[str, int]] = {}
        self.drin_map: dict[int, bool] = {}

        monkeypatch.setattr(ctypes, "get_last_error", self._get_last_error, raising=False)
        monkeypatch.setattr(ctypes, "set_last_error", self._set_last_error, raising=False)
        # _hausmeister_nur_im_test (#649) filtert auf Nachfahren von os.getpid();
        # diese gestellte Kernel-Welt hat keine echten Rechner-Prozesse.
        monkeypatch.setattr(
            prozessbaum,
            "arbeitsordner_in_windows",
            getattr(prozessbaum.arbeitsordner_in_windows, "__wrapped__", prozessbaum.arbeitsordner_in_windows),
        )
        monkeypatch.setattr(prozessbaum, "_win32", lambda: (self._kernel(), self._nt()))
        ich = os.getpid()
        monkeypatch.setattr(
            prozessbaum,
            "_momentaufnahme_windows",
            lambda: {pid: (ich, 1000 + pid) for pid in self.prozesse},
        )
        monkeypatch.setattr(prozessbaum, "_ordner_von", lambda pfad: ((1, 42), r"\\?\C:\ziel"))
        monkeypatch.setattr(
            prozessbaum,
            "_liegt_darin",
            lambda griff, _ziel, _kennungen: self.drin_map.get(griff, False),
        )
        monkeypatch.setattr(prozessbaum, "_ist_fremd_windows", lambda _pid: False)
        if hasattr(prozessbaum, "_GRIFF_WARTEN_S"):
            monkeypatch.setattr(prozessbaum, "_GRIFF_WARTEN_S", (0.0, 0.0))

    def _get_last_error(self) -> int:
        return self.letzter_fehler

    def _set_last_error(self, wert: int) -> None:
        self.letzter_fehler = wert

    def _neuer_griff(self, art: str, wert: int) -> int:
        griff = self.naechster_griff
        self.naechster_griff += 1
        self.offene_griffe[griff] = (art, wert)
        return griff

    def _open_process(self, recht: int, _erben: bool, pid: int) -> int:
        proc = self.prozesse.get(pid)
        if proc is None or not proc.existiert:
            self.letzter_fehler = UNGUELTIGER_PARAMETER
            return 0
        if recht in proc.open_fehler:
            self.letzter_fehler = proc.open_fehler[recht]
            return 0
        self.letzter_fehler = 0
        return self._neuer_griff("prozess", pid)

    def _close_handle(self, griff: Any) -> int:
        schluessel = griff.value if isinstance(griff, ctypes.c_void_p) else int(griff)
        assert schluessel in self.offene_griffe, f"unbekannter oder doppelt geschlossener Griff {schluessel}"
        del self.offene_griffe[schluessel]
        return 1

    def _get_exit_code(self, griff: int, out_code: Any) -> int:
        art, pid = self.offene_griffe[griff]
        assert art == "prozess"
        _setze(out_code, self.prozesse[pid].exit_code)
        return 1

    def _nt_query_info(self, griff: int, klasse: int, puffer: Any, _groesse: int, _laenge: Any) -> int:
        assert klasse == 0
        art, _pid = self.offene_griffe[griff]
        assert art == "prozess"
        puffer[1] = _PEB_BASIS
        return 0

    def _read_memory(self, griff: int, adresse: int, puffer: Any, anzahl: int, gelesen: Any) -> int:
        art, pid = self.offene_griffe[griff]
        assert art == "prozess"
        proc = self.prozesse[pid]
        proc.speicher_aufrufe += 1
        if proc.bei_speicher is not None:
            proc.bei_speicher(proc, proc.speicher_aufrufe)
        if proc.speicher_fehler:
            self.letzter_fehler = proc.speicher_fehler
            _setze(gelesen, 0)
            return 0
        if adresse == _PEB_BASIS + prozessbaum._PEB_PROCESS_PARAMETERS:
            daten = _PARAMS_BASIS.to_bytes(8, "little")
        elif adresse == _PARAMS_BASIS + prozessbaum._PARAMS_CURRENT_DIRECTORY:
            proc.curdir_aufrufe += 1
            if proc.bei_curdir is not None:
                proc.bei_curdir(proc, proc.curdir_aufrufe)
            roh_cwd = proc.cwd.encode("utf-16-le")
            daten = (
                len(roh_cwd).to_bytes(2, "little")
                + len(roh_cwd).to_bytes(2, "little")
                + b"\x00" * 4
                + (_CWD_PUFFER if roh_cwd else 0).to_bytes(8, "little")
                + proc.cwd_griff.to_bytes(8, "little")
            )
        elif adresse == _CWD_PUFFER:
            daten = proc.cwd.encode("utf-16-le")
        else:
            self.letzter_fehler = TEILKOPIE
            _setze(gelesen, 0)
            return 0
        assert len(daten) == anzahl
        ctypes.memmove(puffer, daten, anzahl)
        _setze(gelesen, anzahl)
        return 1

    def _duplicate_handle(
        self,
        quelle: int,
        fremd: int,
        _ziel: int,
        kopie_ref: Any,
        _recht: int,
        _erben: bool,
        _optionen: int,
    ) -> int:
        art, pid = self.offene_griffe[quelle]
        assert art == "prozess"
        proc = self.prozesse[pid]
        proc.dup_aufrufe += 1
        if proc.bei_dup is not None:
            proc.bei_dup(proc, proc.dup_aufrufe)
        if proc.dup_fehler:
            self.letzter_fehler = proc.dup_fehler
            return 0
        kopie = self._neuer_griff("ordner", fremd)
        _setze(kopie_ref, kopie)
        return 1

    def _kernel(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            OpenProcess=self._open_process,
            CloseHandle=self._close_handle,
            GetExitCodeProcess=self._get_exit_code,
            ReadProcessMemory=self._read_memory,
            GetCurrentProcess=lambda: 0xFFFF,
            DuplicateHandle=self._duplicate_handle,
        )

    def _nt(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(NtQueryInformationProcess=self._nt_query_info)


# --- Prozess endet / verschwindet während der Prüfung → zählt als weg -----------------


def test_prozess_endet_waehrend_peb_lesen_zaehlt_als_weg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Prozess endet beim Lesen des PEB → fehlt im Befund, Ordner gilt als frei."""

    def stirbt_beim_lesen(proc: _FakeProzess, aufruf: int) -> None:
        if aufruf == 2:
            proc.exit_code = 0
            proc.speicher_fehler = TEILKOPIE

    welt = _WinWelt(monkeypatch, {4242: _FakeProzess(bei_speicher=stirbt_beim_lesen)})

    assert prozessbaum.arbeitsordner_in_windows(tmp_path) == {}
    assert welt.offene_griffe == {}
    hausmeister_windows._kein_arbeitsordner_drin(tmp_path)


def test_prozess_endet_zwischen_cwd_und_griff_mit_zugriff_verweigert_zaehlt_als_weg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Beendeter Prozess, dessen ``OpenProcess(..., DUP_HANDLE)`` Fehler 5 liefert, zählt als weg."""
    dup_rechte = prozessbaum._PROCESS_QUERY_INFORMATION | prozessbaum._PROCESS_VM_READ | prozessbaum._PROCESS_DUP_HANDLE
    proc = _FakeProzess()

    def stirbt_nach_cwd(p: _FakeProzess, aufruf: int) -> None:
        if aufruf == 3:  # nach dem Lesen der cwd-Zeichenfolge in _arbeitsordner_windows
            p.exit_code = 0
            p.open_fehler[dup_rechte] = ZUGRIFF_VERWEIGERT

    proc.bei_speicher = stirbt_nach_cwd
    welt = _WinWelt(monkeypatch, {4243: proc})

    assert prozessbaum.arbeitsordner_in_windows(tmp_path) == {}
    assert welt.offene_griffe == {}
    hausmeister_windows._kein_arbeitsordner_drin(tmp_path)


def test_prozess_endet_waehrend_griff_pruefung_ohne_cwd_griff_zaehlt_als_weg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fall #649/#654: kein cwd-Griff im PEB, Prozess endet dabei → zählt als weg."""

    def kein_griff_und_tot(proc: _FakeProzess, aufruf: int) -> None:
        if aufruf == 2:  # erster _curdir-Aufruf in _arbeitsordner_griff_windows
            proc.cwd_griff = 0
            proc.exit_code = 0

    welt = _WinWelt(monkeypatch, {9648: _FakeProzess(bei_curdir=kein_griff_und_tot)})

    assert prozessbaum.arbeitsordner_in_windows(tmp_path) == {}
    assert welt.offene_griffe == {}
    hausmeister_windows._kein_arbeitsordner_drin(tmp_path)


def test_prozess_endet_waehrend_duplicate_handle_zaehlt_als_weg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``DuplicateHandle`` scheitert, weil der Prozess gerade beendet wurde → zählt als weg."""

    def stirbt_bei_dup(proc: _FakeProzess, _aufruf: int) -> None:
        proc.exit_code = 0
        proc.dup_fehler = UNGUELTIGER_GRIFF

    welt = _WinWelt(monkeypatch, {4244: _FakeProzess(bei_dup=stirbt_bei_dup)})

    assert prozessbaum.arbeitsordner_in_windows(tmp_path) == {}
    assert welt.offene_griffe == {}
    hausmeister_windows._kein_arbeitsordner_drin(tmp_path)


def test_prozess_im_start_bekommt_cwd_griff_und_wird_geprueft(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Startphase (erst kein cwd-Griff, kurz danach gesetzt) → echt geprüft statt Zufalls-Sperre."""

    def griff_kommt_spaeter(proc: _FakeProzess, aufruf: int) -> None:
        # Aufruf 1: _arbeitsordner_windows; Aufruf 2: erster Versuch im Griff-Leser (noch 0);
        # Aufruf 3+: Griff steht jetzt im PEB.
        proc.cwd_griff = 0 if aufruf == 2 else 0x40

    welt = _WinWelt(monkeypatch, {4245: _FakeProzess(bei_curdir=griff_kommt_spaeter)})

    befunde = prozessbaum.arbeitsordner_in_windows(tmp_path)

    assert 4245 in befunde and befunde[4245].drin is False
    assert welt.offene_griffe == {}
    hausmeister_windows._kein_arbeitsordner_drin(tmp_path)


# --- Echte Zugriffsverweigerung / lebender unlesbarer Prozess → weiter sichere Seite ---


@pytest.mark.parametrize("modus", ["open_dup", "open_beide", "dup_handle"])
def test_zugriff_verweigert_auf_lebenden_prozess_bleibt_sichere_seite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, modus: str
) -> None:
    """Echte Zugriffsverweigerung an einem lebenden Prozess bleibt ``drin=None`` (Ordner bleibt)."""
    dup_rechte = prozessbaum._PROCESS_QUERY_INFORMATION | prozessbaum._PROCESS_VM_READ | prozessbaum._PROCESS_DUP_HANDLE
    proc = _FakeProzess()
    if modus == "open_dup":
        proc.open_fehler[dup_rechte] = ZUGRIFF_VERWEIGERT
    elif modus == "open_beide":
        proc.open_fehler[dup_rechte] = ZUGRIFF_VERWEIGERT
        proc.open_fehler[prozessbaum._PROCESS_QUERY_LIMITED_INFORMATION] = ZUGRIFF_VERWEIGERT
    else:
        proc.dup_fehler = ZUGRIFF_VERWEIGERT

    welt = _WinWelt(monkeypatch, {4246: proc})

    befunde = prozessbaum.arbeitsordner_in_windows(tmp_path)

    assert 4246 in befunde and befunde[4246].drin is None
    assert welt.offene_griffe == {}
    with pytest.raises(hausmeister_windows.Bleibt) as exc:
        hausmeister_windows._kein_arbeitsordner_drin(tmp_path)
    assert exc.value.fehler and "4246" in exc.value.grund


def test_lebender_prozess_dauerhaft_ohne_cwd_griff_bleibt_sichere_seite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ein lebender Prozess dauerhaft ohne cwd-Griff im PEB bleibt auf der sicheren Seite."""
    welt = _WinWelt(monkeypatch, {4247: _FakeProzess(cwd_griff=0)})

    befunde = prozessbaum.arbeitsordner_in_windows(tmp_path)

    assert 4247 in befunde and befunde[4247].drin is None and "kein cwd-Griff" in befunde[4247].grund
    assert welt.offene_griffe == {}
    with pytest.raises(hausmeister_windows.Bleibt) as exc:
        hausmeister_windows._kein_arbeitsordner_drin(tmp_path)
    assert exc.value.fehler and "4247" in exc.value.grund


def test_lebender_eigener_prozess_peb_unlesbar_bleibt_sichere_seite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unlesbarer PEB eines noch lebenden eigenen Prozesses sperrt weiter."""
    welt = _WinWelt(monkeypatch, {4248: _FakeProzess(speicher_fehler=TEILKOPIE)})

    befunde = prozessbaum.arbeitsordner_in_windows(tmp_path)

    assert 4248 in befunde and befunde[4248].text is None and befunde[4248].drin is None
    assert welt.offene_griffe == {}
    with pytest.raises(hausmeister_windows.Bleibt) as exc:
        hausmeister_windows._kein_arbeitsordner_drin(tmp_path)
    assert exc.value.fehler and "4248" in exc.value.grund


# --- Echte Windows-Prozesse (läuft bei GitHub Actions unter Windows) -------------------


@pytest.mark.skipif(sys.platform != "win32", reason="echter Windows-PEB-Weg nur unter Windows")
def test_echt_windows_prozess_endet_waehrend_pruefung_ordner_frei(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Echter Prozess endet zwischen cwd-Lesen und Griff-Prüfung → Ordner wird geräumt."""
    # Ticket-Nummern müssen im Bereich 1–4999 liegen (hausmeister._TICKET_NR, #1526).
    wt = welt.worktree(4654)
    welt.ticket(4654, "CLOSED", 48)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=welt.tmp,
        **prozessbaum.ohne_fenster(),
    )
    echt_drin = prozessbaum._arbeitsordner_drin

    def stirbt_dann_pruefen(
        pid: int, ziel: tuple[tuple[int, int], str], kennungen: dict[str, tuple[int, int]]
    ) -> bool | None:
        if pid == proc.pid:
            proc.kill()
            proc.wait()
            raise OSError(f"Prozess {pid}: kein cwd-Griff im PEB")
        return echt_drin(pid, ziel, kennungen)

    monkeypatch.setattr(prozessbaum, "_arbeitsordner_drin", stirbt_dann_pruefen)
    try:
        r = welt.aufraeumen(wt, loeschen=True)
        assert proc.poll() is not None
        assert r.art == "gelöscht" and not r.fehler, r.grund
        assert not wt.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.skipif(sys.platform != "win32", reason="echter Windows-PEB-Weg nur unter Windows")
def test_echt_windows_zugriff_verweigert_bleibt_sichere_seite(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Echter lebender Prozess mit Zugriffsverweigerung beim Griff-Lesen hält den Ordner."""
    wt = welt.worktree(4655)
    welt.ticket(4655, "CLOSED", 48)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=welt.tmp,
        **prozessbaum.ohne_fenster(),
    )
    echt_drin = prozessbaum._arbeitsordner_drin

    def verweigert(pid: int, ziel: tuple[tuple[int, int], str], kennungen: dict[str, tuple[int, int]]) -> bool | None:
        if pid == proc.pid:
            raise OSError(ZUGRIFF_VERWEIGERT, f"OpenProcess({pid}) zum Übernehmen des cwd-Griffs gescheitert")
        return echt_drin(pid, ziel, kennungen)

    monkeypatch.setattr(prozessbaum, "_arbeitsordner_drin", verweigert)
    try:
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and r.fehler and str(proc.pid) in r.grund, r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait()
