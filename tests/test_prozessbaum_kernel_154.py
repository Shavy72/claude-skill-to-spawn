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
