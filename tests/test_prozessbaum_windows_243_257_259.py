"""#243, #257, #259: Windows-Prozessbaum (lebt, Alter, WOW64-cwd, erhöhtes MSYS).

- #257: ``deploy_status.prozess_lebt`` nutzt unter Windows ``prozessbaum.lebt``;
  ``prozessbaum.alter_s`` liefert das Prozess-Alter unter Windows für die 6-h-Waisen-Regel
  im Aufpasser.
- #259: 32-Bit-Prozesse (WOW64, z. B. ``SysWOW64/cmd.exe``) werden über ``IsWow64Process2``
  erkannt und aus dem 32-Bit-PEB gelesen (bei Fehler: fail-closed); kein Griff-Leck, wenn
  der Prozess zwischen ``DuplicateHandle`` und Gegenlesen endet.
- #243: Verweigert ``OpenProcess`` den Zugriff auf einen MSYS-/Cygwin-Prozess (Bildpfad
  neben ``msys-2.0.dll``/``cygwin1.dll`` über ``QueryFullProcessImageNameW``), bleibt der
  Ordner fail-closed; Nicht-MSYS-Prozesse mit verweigertem Zugriff sperren nicht.
"""

# ruff: noqa: F811 — ``welt`` ist ein importiertes Fixture
from __future__ import annotations

import ctypes
import logging
import os
import re
import signal
import subprocess
import sys
import time
import types
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from git_bash import git_bash
from test_hausmeister_1368 import Welt, welt  # noqa: F401 — Fixture

from to_spawn import deploy_status, hausmeister_windows, prozessbaum
from to_spawn.aufpasser import Aufpasser, Einstellungen

REPO = Path(__file__).resolve().parent.parent
WINDOWS = sys.platform == "win32"
SYSWOW64_CMD = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "SysWOW64" / "cmd.exe"


def _ist_admin() -> bool:
    if not WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def _baum_tot(pid: int) -> None:
    """Beendet ``pid`` und alle lebenden Nachfahren (aufräumen ohne Ausnahme)."""
    try:
        pids = list(reversed(prozessbaum.baum(pid)))
    except OSError:
        pids = [pid]
    prozessbaum.senden(pids, signal.SIGTERM)


@contextmanager
def _cmd32_in(start_cwd: Path, ziel_cwd: Path) -> Iterator[subprocess.Popen[str]]:
    """Echter 32-Bit-Prozess (``SysWOW64/cmd.exe``), der nach dem Start per ``cd`` in
    ``ziel_cwd`` wechselt (prüft das Lesen des 32-Bit-PEB auch nach Ordnerwechsel)."""
    if not SYSWOW64_CMD.is_file():
        pytest.skip(f"{SYSWOW64_CMD} fehlt auf diesem System")
    proc = subprocess.Popen(
        [str(SYSWOW64_CMD), "/Q", "/K"],
        cwd=str(start_cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        **prozessbaum.ohne_fenster(),
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(f'cd /d "{ziel_cwd}"\necho BEREIT32\n')
        proc.stdin.flush()
        ende = time.monotonic() + 15
        while time.monotonic() < ende:
            zeile = proc.stdout.readline()
            if not zeile or "BEREIT32" in zeile:
                break
        assert proc.poll() is None, "32-Bit-cmd lief nicht an"
        yield proc
    finally:
        _baum_tot(proc.pid)
        proc.kill()
        proc.wait(timeout=15)
        for strom in (proc.stdin, proc.stdout):
            if strom is not None:
                strom.close()


# --- #257: deploy_status.prozess_lebt + Prozess-Alter unter Windows -----------------------


@pytest.mark.skipif(not WINDOWS, reason="Windows-Weg von deploy_status.prozess_lebt und alter_s")
def test_257_deploy_status_lebt_und_alter_echter_prozess_ignoriert_term() -> None:
    """Echter Prozess, der SIGTERM ignoriert: lebt vor Beenden, danach tot; Alter messbar."""
    skript = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('OK', flush=True); time.sleep(60)"
    )
    kind = subprocess.Popen(
        [sys.executable, "-c", skript],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        **prozessbaum.ohne_fenster(),
    )
    try:
        assert kind.stdout is not None
        assert kind.stdout.readline().strip() == "OK"
        assert deploy_status.prozess_lebt(kind.pid) is True
        alter = prozessbaum.alter_s(kind.pid)
        assert alter is not None and 0.0 <= alter < 60.0, alter

        assert prozessbaum.beenden([kind.pid], ((signal.SIGTERM, 5.0),))
        kind.wait(timeout=10)
        assert deploy_status.prozess_lebt(kind.pid) is False
        assert not prozessbaum.lebt(kind.pid)
        assert prozessbaum.alter_s(kind.pid) is None
    finally:
        if kind.poll() is None:
            kind.kill()
            kind.wait(timeout=10)
        if kind.stdout is not None:
            kind.stdout.close()


@pytest.mark.skipif(not WINDOWS, reason="Git-Bash-Kind unter Windows")
def test_257_git_bash_kind_lebt_und_stirbt_sauber() -> None:
    """Echtes Git-Bash-Kind ``sh -c 'sleep 37 & wait'``: lebt, Alter lesbar, nach Ende keiner mehr."""
    bash = git_bash()
    if bash is None:
        pytest.skip("Git Bash fehlt")
    sh = subprocess.Popen(
        [bash, "-c", "sleep 37 & wait"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **prozessbaum.ohne_fenster(),
    )
    kinder: list[int] = []
    try:
        ende = time.monotonic() + 15
        while time.monotonic() < ende:
            kinder = prozessbaum.baum(sh.pid)
            if len(kinder) >= 2:
                break
            time.sleep(0.1)
        assert len(kinder) >= 2, f"sleep-Kind nicht erschienen: {kinder}"
        for pid in kinder:
            assert deploy_status.prozess_lebt(pid) is True
            alter = prozessbaum.alter_s(pid)
            assert alter is not None and 0.0 <= alter < 60.0, (pid, alter)

        assert prozessbaum.beenden(list(reversed(kinder)), ((signal.SIGTERM, 5.0),))
        sh.wait(timeout=10)
        for pid in kinder:
            assert not prozessbaum.lebt(pid), f"Prozess {pid} lebt nach Ende noch"
            assert deploy_status.prozess_lebt(pid) is False
    finally:
        _baum_tot(sh.pid)
        if sh.poll() is None:
            sh.kill()
            sh.wait(timeout=10)


def test_257_deploy_status_ungueltige_pid_ist_unbekannt() -> None:
    for wert in (None, 0, -5, True, False, "123"):
        assert deploy_status.prozess_lebt(wert) is None


@pytest.mark.skipif(not WINDOWS, reason="Windows-Waisen-Regel (6 h) ohne ps")
def test_257_aufpasser_waise_aelter_6h_unter_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """6-h-Regel für verwaiste Prozesse im Aufpasser greift unter Windows über ``prozessbaum.alter_s``."""
    marke = f"waise257_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    skript = tmp_path / f"{marke}.py"
    skript.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(skript)], **prozessbaum.ohne_fenster())
    try:
        muster = re.escape(marke)
        ende = time.monotonic() + 10
        while time.monotonic() < ende and not any(p == proc.pid for p, _ in prozessbaum.passende(muster)):
            time.sleep(0.05)
        e = Einstellungen(
            zustand=tmp_path / "zustand",
            tmux_socket=f"waise257-{os.getpid()}",
            deploy_muster=muster,
        )
        wache = Aufpasser(e)
        # Frisch gestartet → Alter < 6 h → blockiert.
        echtes_alter = wache._prozess_alter_s(proc.pid)
        assert echtes_alter is not None and 0.0 <= echtes_alter < 60.0, echtes_alter
        assert wache.deploy_laeuft(muster)

        # Uhr 7 h vorgestellt → echter Kernel-Zeitstempel ist > 6 h alt → Waise ignoriert.
        echte_uhr = time.time
        monkeypatch.setattr(prozessbaum.time, "time", lambda: echte_uhr() + 7 * 3600)
        caplog.set_level(logging.INFO)
        assert wache.deploy_laeuft(muster) == []
        assert "Waise ignoriert" in caplog.text
    finally:
        _baum_tot(proc.pid)
        proc.wait(timeout=15)


# --- #259: 32-Bit-Prozess (WOW64) + fail-closed + Griff-Leck ------------------------------


@pytest.mark.skipif(not WINDOWS, reason="WOW64 (32-Bit-PEB) gibt es nur unter Windows")
def test_259_wow64_cmd_im_ordner_haelt_ordner(welt: Welt) -> None:
    """Echter 32-Bit-``cmd.exe`` wechselt in den Worktree → cwd-Prüfung (PEB32) sperrt."""
    wt = welt.worktree(2591)
    welt.ticket(2591, "CLOSED", 48)
    with _cmd32_in(welt.tmp, wt) as proc:
        cwd = prozessbaum._arbeitsordner_windows(proc.pid)
        assert cwd is not None and hausmeister_windows.pfad_norm(cwd) == hausmeister_windows.pfad_norm(wt), cwd
        befunde = prozessbaum.arbeitsordner_in_windows(wt)
        assert proc.pid in befunde and befunde[proc.pid].drin is True, befunde.get(proc.pid)
        # Trocken (ohne Umbenennungs-Probe): allein die cwd-Prüfung muss sperren.
        with pytest.raises(hausmeister_windows.Bleibt, match=str(proc.pid)):
            hausmeister_windows._kein_arbeitsordner_drin(wt)
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and "Prozess" in r.grund, r.grund
        assert wt.is_dir()
    assert not prozessbaum.lebt(proc.pid)


@pytest.mark.skipif(not WINDOWS, reason="WOW64 (32-Bit-PEB) gibt es nur unter Windows")
def test_259_wow64_cmd_im_nachbarordner_haelt_nicht(welt: Welt) -> None:
    """Echter 32-Bit-``cmd.exe`` im Nachbarordner sperrt den Worktree nicht."""
    wt = welt.worktree(2592)
    welt.ticket(2592, "CLOSED", 48)
    nachbar = wt.with_name(wt.name + "0")
    nachbar.mkdir()
    with _cmd32_in(welt.tmp, nachbar) as proc:
        befunde = prozessbaum.arbeitsordner_in_windows(wt)
        assert proc.pid in befunde and befunde[proc.pid].drin is False, befunde.get(proc.pid)
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "gelöscht", r.grund
        assert not wt.exists()
    assert not prozessbaum.lebt(proc.pid)


@pytest.mark.skipif(not WINDOWS, reason="WOW64 (32-Bit-PEB) gibt es nur unter Windows")
def test_259_wow64_peb32_unlesbar_bleibt_fail_closed(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """WOW64 erkannt, aber 32-Bit-PEB nicht lesbar → fail-closed (Ordner bleibt mit Fehler)."""
    wt = welt.worktree(2593)
    welt.ticket(2593, "CLOSED", 48)
    with _cmd32_in(welt.tmp, welt.tmp) as proc:
        kernel, nt = prozessbaum._win32()
        echt_open = kernel.OpenProcess
        echt_ntq = nt.NtQueryInformationProcess
        ziel_griffe: set[int] = set()

        def open_process(recht: int, erben: bool, pid: int):
            griff = echt_open(recht, erben, pid)
            if griff and pid == proc.pid:
                ziel_griffe.add(griff)
            return griff

        def kaputt_wow64(griff: int, klasse: int, puffer: object, groesse: int, laenge: object) -> int:
            if klasse == 26 and griff in ziel_griffe:  # ProcessWow64Information nur für proc.pid
                return 0xC0000022
            return echt_ntq(griff, klasse, puffer, groesse, laenge)

        kernel_attrappe = types.SimpleNamespace(
            OpenProcess=open_process,
            CloseHandle=kernel.CloseHandle,
            GetExitCodeProcess=kernel.GetExitCodeProcess,
            ReadProcessMemory=kernel.ReadProcessMemory,
            GetCurrentProcess=kernel.GetCurrentProcess,
            DuplicateHandle=kernel.DuplicateHandle,
            CreateFileW=kernel.CreateFileW,
            GetFinalPathNameByHandleW=kernel.GetFinalPathNameByHandleW,
            IsWow64Process2=kernel.IsWow64Process2,
            QueryFullProcessImageNameW=kernel.QueryFullProcessImageNameW,
            GetProcessTimes=kernel.GetProcessTimes,
        )
        nt_attrappe = types.SimpleNamespace(
            NtQuerySystemInformation=nt.NtQuerySystemInformation,
            NtQueryInformationProcess=kaputt_wow64,
            NtQueryInformationFile=nt.NtQueryInformationFile,
            NtQueryVolumeInformationFile=nt.NtQueryVolumeInformationFile,
        )
        monkeypatch.setattr(prozessbaum, "_win32", lambda: (kernel_attrappe, nt_attrappe))
        with pytest.raises(hausmeister_windows.Bleibt) as exc:
            hausmeister_windows._kein_arbeitsordner_drin(wt)
        assert exc.value.fehler is True and str(proc.pid) in exc.value.grund, exc.value.grund
    assert not prozessbaum.lebt(proc.pid)


def test_259_kein_griff_leck_bei_fehler_nach_duplicate_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Endet der Prozess zwischen ``DuplicateHandle`` und dem Gegenlesen von ``_curdir``,
    wird die bereits duplizierte Griff-Kopie sauber geschlossen (#259)."""
    geschlossen: list[int] = []

    def dup(_src: int, _h: int, _dst: int, ziel: object, *_rest: object) -> int:
        ziel._obj.value = 777  # type: ignore[attr-defined]
        return 1

    def exit_code(_griff: int, ziel: object) -> int:
        ziel._obj.value = 259  # STILL_ACTIVE  # type: ignore[attr-defined]
        return 1

    kernel = types.SimpleNamespace(
        OpenProcess=lambda _r, _e, _p: 555,
        GetExitCodeProcess=exit_code,
        GetCurrentProcess=lambda: 1,
        DuplicateHandle=dup,
        CloseHandle=lambda g: geschlossen.append(g.value if hasattr(g, "value") else g) or 1,
    )
    monkeypatch.setattr(prozessbaum, "_win32", lambda: (kernel, types.SimpleNamespace()))
    monkeypatch.setattr(ctypes, "set_last_error", lambda _n: None, raising=False)

    aufrufe = 0

    def curdir(_p: int, _pid: int):
        nonlocal aufrufe
        aufrufe += 1
        if aufrufe == 1:
            return (10, 0x1000, 0x42, b"vorher")
        raise OSError("Prozess zwischenzeitlich beendet")

    monkeypatch.setattr(prozessbaum, "_curdir", curdir)
    with pytest.raises(OSError, match="beendet"):
        prozessbaum._arbeitsordner_griff_windows(4242)
    assert 777 in geschlossen and 555 in geschlossen, geschlossen


# --- #243: erhöhter Git-Bash-/MSYS-Prozess (Zugriff verweigert) ---------------------------


def _verweigere_peb_zugriff(monkeypatch: pytest.MonkeyPatch, ziel_pid: int) -> None:
    """Simuliert ``ERROR_ACCESS_DENIED`` nur beim PEB-``OpenProcess`` auf ``ziel_pid``.
    ``PROCESS_QUERY_LIMITED_INFORMATION`` (Bildpfad) geht echt an den Kernel."""
    kernel, nt = prozessbaum._win32()
    echt_open = kernel.OpenProcess

    def open_process(recht: int, erben: bool, pid: int):
        if pid == ziel_pid and (recht & prozessbaum._PROCESS_VM_READ):
            ctypes.set_last_error(prozessbaum._FEHLER_ZUGRIFF_VERWEIGERT)
            return 0
        return echt_open(recht, erben, pid)

    kernel_attrappe = types.SimpleNamespace(
        OpenProcess=open_process,
        CloseHandle=kernel.CloseHandle,
        GetExitCodeProcess=kernel.GetExitCodeProcess,
        ReadProcessMemory=kernel.ReadProcessMemory,
        GetCurrentProcess=kernel.GetCurrentProcess,
        DuplicateHandle=kernel.DuplicateHandle,
        CreateFileW=kernel.CreateFileW,
        GetFinalPathNameByHandleW=kernel.GetFinalPathNameByHandleW,
        IsWow64Process2=kernel.IsWow64Process2,
        QueryFullProcessImageNameW=kernel.QueryFullProcessImageNameW,
        GetProcessTimes=kernel.GetProcessTimes,
    )
    monkeypatch.setattr(prozessbaum, "_win32", lambda: (kernel_attrappe, nt))


@pytest.mark.skipif(not WINDOWS, reason="MSYS-Bildpfadprüfung bei Zugriff verweigert nur unter Windows")
def test_243_msys_mit_zugriff_verweigert_bleibt(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Echter MSYS-``sleep``, dessen PEB-Lesen auf Zugriff verweigert geht: Bildpfad echt
    gelesen (neben ``msys-2.0.dll``) → Ordner bleibt mit Fehler."""
    wt = welt.worktree(2431)
    welt.ticket(2431, "CLOSED", 48)
    proc = subprocess.Popen(["sleep", "60"], cwd=welt.tmp, **prozessbaum.ohne_fenster())
    try:
        _verweigere_peb_zugriff(monkeypatch, proc.pid)
        with pytest.raises(hausmeister_windows.Bleibt) as exc:
            hausmeister_windows._kein_arbeitsordner_drin(wt)
        assert exc.value.fehler is True and str(proc.pid) in exc.value.grund, exc.value.grund
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and str(proc.pid) in r.grund, r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert not prozessbaum.lebt(proc.pid)


@pytest.mark.skipif(not WINDOWS, reason="Bildpfadprüfung bei Zugriff verweigert nur unter Windows")
def test_243_nicht_msys_mit_zugriff_verweigert_haelt_nicht(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Echter Nicht-MSYS-Prozess (``python.exe``) mit verweigertem Zugriff sperrt nicht."""
    wt = welt.worktree(2432)
    welt.ticket(2432, "CLOSED", 48)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=welt.tmp,
        **prozessbaum.ohne_fenster(),
    )
    try:
        _verweigere_peb_zugriff(monkeypatch, proc.pid)
        hausmeister_windows._kein_arbeitsordner_drin(wt)
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "gelöscht", r.grund
        assert not wt.exists()
    finally:
        _baum_tot(proc.pid)
        proc.kill()
        proc.wait(timeout=10)
    assert not prozessbaum.lebt(proc.pid)


def _starte_als_standardnutzer(befehl: list[str], ausgabe: Path) -> int:
    """Startet ``befehl`` mit eingeschränktem Medium-Integrity-Token (nicht erhöht) aus einem
    Administrator-Prozess — nur für den echten #243-Beleg auf dem Windows-Runner."""
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    TOKEN_ALL_ACCESS = 0xF01FF
    LUA_TOKEN = 0x4
    TOKEN_INTEGRITY_LEVEL = 25
    SE_GROUP_INTEGRITY = 0x00000020
    WAIT_INFINITE = 0xFFFFFFFF

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class TOKEN_MANDATORY_LABEL(ctypes.Structure):
        _fields_ = [("Label", SID_AND_ATTRIBUTES)]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.CreateRestrictedToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    advapi.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    advapi.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]

    STARTF_USESTDHANDLES = 0x00000100
    CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SECURITY_ATTRIBUTES),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE

    eigen = wintypes.HANDLE()
    eingeschraenkt = wintypes.HANDLE()
    medium_sid = ctypes.c_void_p()
    assert advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_ALL_ACCESS, ctypes.byref(eigen)), (
        ctypes.get_last_error()
    )
    try:
        assert advapi.CreateRestrictedToken(
            eigen, LUA_TOKEN, 0, None, 0, None, 0, None, ctypes.byref(eingeschraenkt)
        ), ctypes.get_last_error()
        try:
            assert advapi.ConvertStringSidToSidW("S-1-16-8192", ctypes.byref(medium_sid)), ctypes.get_last_error()
            try:
                label = TOKEN_MANDATORY_LABEL(SID_AND_ATTRIBUTES(medium_sid, SE_GROUP_INTEGRITY))
                assert advapi.SetTokenInformation(
                    eingeschraenkt, TOKEN_INTEGRITY_LEVEL, ctypes.byref(label), ctypes.sizeof(label)
                ), ctypes.get_last_error()
            finally:
                kernel.LocalFree(medium_sid)

            sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
            log_handle = kernel.CreateFileW(str(ausgabe), 0x40000000, 0x7, ctypes.byref(sa), 2, 0x80, None)
            assert log_handle and log_handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
            try:
                si = STARTUPINFOW(
                    cb=ctypes.sizeof(STARTUPINFOW),
                    dwFlags=STARTF_USESTDHANDLES,
                    hStdOutput=log_handle,
                    hStdError=log_handle,
                )
                pi = PROCESS_INFORMATION()
                zeile = ctypes.create_unicode_buffer(subprocess.list2cmdline(befehl))
                ok = advapi.CreateProcessAsUserW(
                    eingeschraenkt,
                    None,
                    zeile,
                    None,
                    None,
                    True,
                    CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                    None,
                    str(ausgabe.parent),
                    ctypes.byref(si),
                    ctypes.byref(pi),
                )
                assert ok, f"CreateProcessAsUserW gescheitert: WinError {ctypes.get_last_error()}"
                try:
                    kernel.WaitForSingleObject(pi.hProcess, WAIT_INFINITE)
                    code = wintypes.DWORD(1)
                    kernel.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
                    return int(code.value)
                finally:
                    kernel.CloseHandle(pi.hThread)
                    kernel.CloseHandle(pi.hProcess)
            finally:
                kernel.CloseHandle(log_handle)
        finally:
            kernel.CloseHandle(eingeschraenkt)
    finally:
        kernel.CloseHandle(eigen)


_PRUEF_SKRIPT_243 = """\
import ctypes
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
from to_spawn import hausmeister_windows, prozessbaum

wt, pid = pathlib.Path(sys.argv[2]), int(sys.argv[3])
k, _ = prozessbaum._win32()
ctypes.set_last_error(0)
h = k.OpenProcess(prozessbaum._PROCESS_QUERY_INFORMATION | prozessbaum._PROCESS_VM_READ, False, pid)
err = ctypes.get_last_error()
if h:
    k.CloseHandle(h)
bild = prozessbaum._bildpfad_windows(pid)
befunde = prozessbaum.arbeitsordner_in_windows(wt)
befund = befunde.get(pid)
try:
    hausmeister_windows._kein_arbeitsordner_drin(wt)
    print(f"FREI h={h} err={err} bild={bild!r} befund={befund!r}")
    sys.exit(2)
except hausmeister_windows.Bleibt as b:
    print(f"BLEIBT fehler={b.fehler} h={h} err={err} bild={bild!r} befund={befund!r} grund={b.grund}")
    ok = (
        not h
        and err == 5
        and bool(bild)
        and prozessbaum._ist_msys_bildpfad(bild)
        and befund is not None
        and befund.text is None
        and befund.drin is None
        and "MSYS/Cygwin" in befund.grund
        and b.fehler
    )
    sys.exit(0 if ok else 3)
"""


@pytest.mark.skipif(not WINDOWS, reason="Erhöhter MSYS-Prozess nur unter Windows")
@pytest.mark.skipif(
    not _ist_admin(),
    reason="Echter Beleg mit erhöhtem Prozess braucht Administrator-Rechte (läuft auf dem Windows-Runner)",
)
def test_243_echter_erhoehter_msys_prozess_sperrt_ohne_attrappe(tmp_path: Path) -> None:
    """Auf dem GitHub-Windows-Runner (Administrator): erhöhter MSYS-``sleep`` im Ordner,
    geprüft aus einem echten nicht-erhöhten Kindprozess — ganz ohne Attrappen."""
    wt = tmp_path / "wt-243-admin"
    wt.mkdir()
    skript = tmp_path / "pruefer_243.py"
    skript.write_text(_PRUEF_SKRIPT_243, encoding="utf-8")
    bericht = tmp_path / "ergebnis.txt"
    erhoeht = subprocess.Popen(["sleep", "60"], cwd=wt, **prozessbaum.ohne_fenster())
    try:
        code = _starte_als_standardnutzer(
            [sys.executable, str(skript), str(REPO), str(wt), str(erhoeht.pid)],
            bericht,
        )
        inhalt = bericht.read_text(encoding="utf-8", errors="replace") if bericht.is_file() else "<keine Ausgabe>"
        assert code == 0, f"Exit {code}: {inhalt}"
    finally:
        erhoeht.kill()
        erhoeht.wait(timeout=10)
    assert not prozessbaum.lebt(erhoeht.pid)
