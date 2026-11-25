"""Skill-Issue #154, Fix-Runde: Fehlerzweige des Kernel-Wegs unter Windows.

Rechenlogik mit Attrappen der Kernel-Funktionen (``_win32`` liefert die Attrappen):
Puffer-Wachstum bei ``STATUS_INFO_LENGTH_MISMATCH``, Obergrenze, Fehlerstatus,
Kommandozeile unlesbar → ``""`` mit geschlossenem Griff, Wächter gegen 32-Bit-Python.
Dazu ein echter Fall ohne Attrappe: Kommandozeile eines gerade beendeten Kindes.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import types
from typing import Any

import pytest

from to_spawn import prozessbaum

MISMATCH = prozessbaum._STATUS_INFO_LENGTH_MISMATCH
ZUGRIFF_VERWEIGERT = 0xC0000022
EINTRAG = ctypes.sizeof(prozessbaum._ProzessInfo)


def _setze(ref: Any, wert: int) -> None:
    """Schreibt in das Ziel eines ``ctypes.byref``-Arguments."""
    ref._obj.value = wert


def _nt_systeminfo(antworten: list[str], aufrufe: list[int]) -> Any:
    """Attrappe von ``NtQuerySystemInformation``: je Aufruf die nächste Antwort.

    ``"klein"`` = Mismatch mit Bedarf zwei Einträge, ``"ok"`` = zwei Einträge (PID 10 → 4, 11 → 10).
    """

    def abfrage(_klasse: int, puffer: Any, groesse: int, noetig: Any) -> int:
        aufrufe.append(groesse)
        antwort = antworten[min(len(aufrufe), len(antworten)) - 1]
        if antwort == "klein" or groesse < 2 * EINTRAG:
            _setze(noetig, 2 * EINTRAG)
            return MISMATCH
        if antwort == "fehler":
            return ZUGRIFF_VERWEIGERT
        erster = prozessbaum._ProzessInfo.from_buffer(puffer, 0)
        erster.NextEntryOffset, erster.UniqueProcessId, erster.InheritedFromUniqueProcessId = EINTRAG, 10, 4
        erster.CreateTime = 100
        zweiter = prozessbaum._ProzessInfo.from_buffer(puffer, EINTRAG)
        zweiter.NextEntryOffset, zweiter.UniqueProcessId, zweiter.InheritedFromUniqueProcessId = 0, 11, 10
        zweiter.CreateTime = 200
        return 0

    return abfrage


@pytest.fixture
def kernel_attrappe(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """``_win32`` liefert Attrappen; der Puffer startet absichtlich zu klein."""
    welt = types.SimpleNamespace(antworten=["ok"], aufrufe=[], geschlossen=[], oeffnen=lambda pid: 0, cmd=[])
    kernel = types.SimpleNamespace(
        OpenProcess=lambda _recht, _erben, pid: welt.oeffnen(pid),
        CloseHandle=lambda griff: welt.geschlossen.append(griff) or 1,
    )

    def cmd_abfrage(griff: int, _klasse: int, puffer: Any, groesse: int, laenge: Any) -> int:
        welt.cmd.append(getattr(groesse, "value", groesse))  # echte argtypes wandeln c_ulong in int
        return welt.cmd_status[len(welt.cmd) - 1](laenge)

    nt = types.SimpleNamespace(
        NtQuerySystemInformation=lambda *a: _nt_systeminfo(welt.antworten, welt.aufrufe)(*a),
        NtQueryInformationProcess=cmd_abfrage,
    )
    monkeypatch.setattr(prozessbaum, "_win32", lambda: (kernel, nt))
    monkeypatch.setattr(prozessbaum, "_puffer_bytes", EINTRAG)
    return welt


def test_puffer_waechst_nach_mismatch(kernel_attrappe: types.SimpleNamespace) -> None:
    kernel_attrappe.antworten = ["klein", "ok"]
    assert prozessbaum._momentaufnahme_windows() == {10: (4, 100), 11: (10, 200)}
    assert kernel_attrappe.aufrufe[0] == EINTRAG and kernel_attrappe.aufrufe[1] >= 2 * EINTRAG
    assert prozessbaum._puffer_bytes == kernel_attrappe.aufrufe[1]  # nächste Abfrage startet groß genug


def test_dauerhafter_mismatch_ist_fehler_nach_obergrenze(kernel_attrappe: types.SimpleNamespace) -> None:
    kernel_attrappe.antworten = ["klein"]
    with pytest.raises(OSError, match="0xC0000004"):
        prozessbaum._momentaufnahme_windows()
    assert len(kernel_attrappe.aufrufe) == 5


def test_fehlerstatus_ist_fehler(kernel_attrappe: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prozessbaum, "_puffer_bytes", 4 * EINTRAG)
    kernel_attrappe.antworten = ["fehler"]
    with pytest.raises(OSError, match="0xC0000022"):
        prozessbaum._momentaufnahme_windows()


def test_kommandozeile_prozess_nicht_zu_oeffnen(kernel_attrappe: types.SimpleNamespace) -> None:
    assert prozessbaum._kommandozeile_windows(4711) == ""
    assert kernel_attrappe.geschlossen == [] and kernel_attrappe.cmd == []


def test_kommandozeile_erste_abfrage_scheitert(kernel_attrappe: types.SimpleNamespace) -> None:
    kernel_attrappe.oeffnen = lambda _pid: 77
    kernel_attrappe.cmd_status = [lambda _l: ZUGRIFF_VERWEIGERT]
    assert prozessbaum._kommandozeile_windows(4711) == ""
    assert kernel_attrappe.geschlossen == [77]


def test_kommandozeile_zweite_abfrage_scheitert(kernel_attrappe: types.SimpleNamespace) -> None:
    kernel_attrappe.oeffnen = lambda _pid: 78

    def bedarf(laenge: Any) -> int:
        _setze(laenge, 64)
        return MISMATCH

    kernel_attrappe.cmd_status = [bedarf, lambda _l: ZUGRIFF_VERWEIGERT]
    assert prozessbaum._kommandozeile_windows(4711) == ""
    assert kernel_attrappe.cmd == [0, 64] and kernel_attrappe.geschlossen == [78]


def test_32_bit_python_klarer_fehler(monkeypatch: pytest.MonkeyPatch) -> None:
    echt = ctypes.sizeof
    monkeypatch.setattr(ctypes, "sizeof", lambda typ: 4 if typ is ctypes.c_void_p else echt(typ))
    with pytest.raises(OSError, match="64-Bit-Python"):
        prozessbaum._win32.__wrapped__()


@pytest.mark.skipif(sys.platform != "win32", reason="echter Windows-Prozess")
def test_kommandozeile_gerade_beendetes_kind_ist_leer() -> None:
    # Popen hält den Griff offen: die PID kann bis zum Ende des Tests nicht neu vergeben werden.
    kind = subprocess.Popen([sys.executable, "-c", "pass"], **prozessbaum.ohne_fenster())
    kind.wait()
    assert prozessbaum._kommandozeile_windows(kind.pid) == ""


def _curdir_bytes(griff: int, text_len: int = 12, puffer: int = 0x2000) -> bytes:
    """24-Byte-``CURDIR`` (DosPath-Kopf + Handle) für PEB-Tests."""
    return (
        text_len.to_bytes(2, "little")
        + text_len.to_bytes(2, "little")
        + b"\x00" * 4
        + puffer.to_bytes(8, "little")
        + griff.to_bytes(8, "little")
    )


def _peb_attrappe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    curdirs: list[bytes],
    duplizieren: Any,
    exit_codes: list[int] | None = None,
) -> types.SimpleNamespace:
    """Attrappe für ``_arbeitsordner_griff_windows`` und ``_arbeitsordner_windows``."""
    zustand = types.SimpleNamespace(
        geschlossen=[],
        curdir_idx=0,
        exit_idx=0,
        dup_aufrufe=[],
    )
    codes = exit_codes or [prozessbaum._STILL_ACTIVE]

    def get_exit_code(_griff: int, ziel: Any) -> int:
        wert = codes[min(zustand.exit_idx, len(codes) - 1)]
        zustand.exit_idx += 1
        _setze(ziel, wert)
        return 1

    def dup_handle(_src: int, fremd: int, _dst: int, ziel: Any, *_rest: Any) -> int:
        zustand.dup_aufrufe.append(fremd)
        erg = duplizieren(fremd)
        if erg is None:
            return 0
        _setze(ziel, erg)
        return 1

    def curdir(_prozess: int, _pid: int) -> bytes:
        wert = curdirs[min(zustand.curdir_idx, len(curdirs) - 1)]
        zustand.curdir_idx += 1
        return wert

    kernel = types.SimpleNamespace(
        OpenProcess=lambda _recht, _erben, _pid: 900,
        CloseHandle=lambda griff: zustand.geschlossen.append(getattr(griff, "value", griff)) or 1,
        GetExitCodeProcess=get_exit_code,
        GetCurrentProcess=lambda: 1,
        DuplicateHandle=dup_handle,
    )
    monkeypatch.setattr(prozessbaum, "_win32", lambda: (kernel, types.SimpleNamespace()))
    monkeypatch.setattr(prozessbaum, "_curdir", curdir)
    monkeypatch.setattr(ctypes, "set_last_error", lambda _wert: None, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 6, raising=False)
    return zustand


def test_cwd_griff_einmaliger_wechsel_wird_neu_gelesen(monkeypatch: pytest.MonkeyPatch) -> None:
    alt, neu = _curdir_bytes(101), _curdir_bytes(102)
    zustand = _peb_attrappe(
        monkeypatch,
        curdirs=[alt, neu, neu, neu],
        duplizieren=lambda fremd: 500 + fremd,
    )
    assert prozessbaum._arbeitsordner_griff_windows(42) == 602
    assert zustand.dup_aufrufe == [101, 102]
    assert zustand.geschlossen == [601, 900]


def test_cwd_griff_duplicate_scheitert_wegen_wechsel_wird_neu_gelesen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alt, neu = _curdir_bytes(101), _curdir_bytes(102)
    zustand = _peb_attrappe(
        monkeypatch,
        curdirs=[alt, neu, neu, neu],
        duplizieren=lambda fremd: None if fremd == 101 else 602,
    )
    assert prozessbaum._arbeitsordner_griff_windows(42) == 602
    assert zustand.dup_aufrufe == [101, 102]
    assert zustand.geschlossen == [900]


def test_cwd_griff_dauerhafter_wechsel_bleibt_fehler(monkeypatch: pytest.MonkeyPatch) -> None:
    wechselnd = [_curdir_bytes(100 + i) for i in range(20)]
    zustand = _peb_attrappe(
        monkeypatch,
        curdirs=wechselnd,
        duplizieren=lambda fremd: 500 + fremd,
    )
    with pytest.raises(OSError, match="cwd wechselte während der Prüfung"):
        prozessbaum._arbeitsordner_griff_windows(42)
    assert len(zustand.dup_aufrufe) == prozessbaum._CURDIR_VERSUCHE
    assert zustand.geschlossen[-1] == 900
    assert len(zustand.geschlossen) == prozessbaum._CURDIR_VERSUCHE + 1


def test_cwd_griff_prozess_endet_waehrend_lesen_ist_none(monkeypatch: pytest.MonkeyPatch) -> None:
    zustand = _peb_attrappe(
        monkeypatch,
        curdirs=[_curdir_bytes(101)],
        duplizieren=lambda _fremd: None,
        exit_codes=[prozessbaum._STILL_ACTIVE, 0],
    )
    assert prozessbaum._arbeitsordner_griff_windows(42) is None
    assert zustand.geschlossen == [900]


def test_cwd_text_prozess_endet_waehrend_lesen_ist_none(monkeypatch: pytest.MonkeyPatch) -> None:
    zustand = _peb_attrappe(
        monkeypatch,
        curdirs=[_curdir_bytes(101)],
        duplizieren=lambda _fremd: None,
        exit_codes=[prozessbaum._STILL_ACTIVE, 0],
    )
    monkeypatch.setattr(
        prozessbaum,
        "_speicher",
        lambda *_a: (_ for _ in ()).throw(OSError(299, "ReadProcessMemory gescheitert")),
    )
    assert prozessbaum._arbeitsordner_windows(42) is None
    assert zustand.geschlossen == [900]
