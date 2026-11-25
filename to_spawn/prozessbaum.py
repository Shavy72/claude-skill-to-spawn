"""Prozessbaum ermitteln, prüfen und beenden — an genau einer Stelle.

respawn (alte Session ablösen) und aufpasser (Fenster schließen/fortsetzen) beenden
beide den ganzen Prozessbaum eines tmux-Panes. Der Staffel-Hook findet die eigene
Claude-Session in der Vorfahren-Kette und beendet sie: ``session_vorfahr`` und
``session_beenden``. Unter Windows laufen außerdem ``lebt``, ``prozesse``, ``baum``,
``senden`` und ``beenden`` (#501, #92, #68): sie wählen selbst zwischen ``/proc`` und der
Kernel-Prozessliste (ctypes, kein Prozess-Start, #154). Wie der Baum gelesen wird, was
„lebt“ heißt, wie eine Claude-Session erkannt wird und wie Signale stufenweise eskalieren,
steht nur hier. Die Aufrufer wählen nur Stufen (Signal + Wartezeit), Reihenfolge der PIDs und worauf gewartet wird.

Windows: jedes Signal wird zu ``TerminateProcess`` (so macht es ``os.kill``) — die erste
Stufe beendet also schon hart, ``SIGTERM`` lässt sich nicht abfangen. ``ist_claude`` liest
unter Windows die Kommandozeile aus dem Kernel statt ``/proc`` (#602).
"""

from __future__ import annotations

import ctypes
import functools
import logging
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path, PureWindowsPath
from typing import Any, NamedTuple

log = logging.getLogger(__name__)

_PROC = Path("/proc")

Stufe = tuple[signal.Signals, float]
"""Ein Signal und wie lange danach höchstens gewartet wird (Sekunden)."""


def _kinder() -> dict[int, list[int]]:
    """Eltern-PID → Kind-PIDs; Prozessliste unlesbar = Fehler (``OSError``).

    Linux aus ``/proc/*/stat``, Windows aus der Kernel-Prozessliste (ohne
    Kommandozeilen). Windows trägt die Eltern-PID nur bei der Geburt ein und vergibt
    PIDs neu: ein Kind, dessen Eltern-PID fehlt (Elternteil tot) oder einem später
    gestarteten Prozess gehört, hängt an keinem Baum — sonst beendete ``beenden`` Fremde
    mit. Unbekannte Startzeit (0) zählt als echt.
    """
    kinder: dict[int, list[int]] = {}
    if _WINDOWS:
        liste = _momentaufnahme_windows()
        if not liste:  # „keine Prozesse“ gibt es nicht — leer heißt: Liste nicht lesbar
            raise OSError("Windows-Prozessliste nicht lesbar")
        for pid, (eltern, start) in liste.items():
            vorfahr = liste.get(eltern)
            # Recycelte PID: ein „Eltern“-Prozess, der nach dem Kind startete, ist ein Fremder.
            if pid != eltern and vorfahr is not None and not (vorfahr[1] and start and vorfahr[1] > start):
                kinder.setdefault(eltern, []).append(pid)
        return kinder
    for eintrag in _PROC.iterdir():
        if not eintrag.name.isdigit():
            continue
        try:
            stat = (eintrag / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # ``pid (comm) zustand ppid …`` — comm darf Leerzeichen und Klammern enthalten.
        rest = stat[stat.rfind(")") + 2 :].split()
        if len(rest) < 2:
            continue
        kinder.setdefault(int(rest[1]), []).append(int(eintrag.name))
    return kinder


def baum(pid: int) -> list[int]:
    """``pid`` selbst und alle Nachfahren (Breite zuerst, ``pid`` vorn)."""
    kinder = _kinder()
    raus, warteschlange = [], [pid]
    while warteschlange:
        p = warteschlange.pop(0)
        raus.append(p)
        warteschlange.extend(kinder.get(p, []))
    return raus


def lebt(pid: int) -> bool:
    """Prozess existiert noch (Linux: ``/proc/<pid>`` vorhanden, auch Zombies; Windows: noch aktiv)."""
    return _lebt_windows(pid) if _WINDOWS else (_PROC / str(pid)).exists()


def prozesse() -> dict[int, tuple[int, str, int]]:
    """PID → (Eltern-PID, Kommandozeile, Startzeit) aller Prozesse; unlesbar = leer.

    Die Startzeit taugt nur zum Vergleich untereinander (Linux: Ticks seit Boot,
    Windows: FILETIME; 0 = unbekannt). Liest jede Kommandozeile — wer nur den Baum
    braucht, nimmt ``baum`` (unter Windows ohne Kommandozeilen, deutlich billiger).
    """
    if _WINDOWS:
        return _prozesse_windows()
    liste: dict[int, tuple[int, str, int]] = {}
    try:
        eintraege = list(_PROC.iterdir())
    except OSError as fehler:
        log.warning("%s nicht lesbar: %s", _PROC, fehler)
        return liste
    for eintrag in eintraege:
        if not eintrag.name.isdigit():
            continue
        try:
            stat = (eintrag / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rest = stat[stat.rfind(")") + 2 :].split()  # Felder ab 3 (Zustand); 4 = ppid, 22 = starttime
        if len(rest) < 20:
            continue
        liste[int(eintrag.name)] = (int(rest[1]), _cmdline_linux(int(eintrag.name)).strip(), int(rest[19]))
    return liste


class _KeinProc(FileNotFoundError):
    """Das System hat gar kein ``/proc`` (Windows) — nicht „Prozess weg“."""


def _proc(pid: int, datei: str) -> bytes:
    """Rohinhalt von ``/proc/<pid>/<datei>`` (Naht für Tests); fehlt ``/proc`` ganz: :class:`_KeinProc`."""
    try:
        return (_PROC / str(pid) / datei).read_bytes()
    except FileNotFoundError:
        if not _PROC.is_dir():
            raise _KeinProc(f"{_PROC} fehlt") from None
        raise


def ist_claude(pid: int) -> bool:
    """Ist ``pid`` eine Claude-Session — nativ (``claude``) oder per npm (``node … claude``)?

    Prozess weg (``FileNotFoundError``/``ProcessLookupError``) heißt „kein Claude“.
    Unlesbar aus anderem Grund zählt als Claude: lieber warten als eine lebende
    Session für beendet halten. Quelle ist ``/proc``, wo es eins gibt (auch ein von Tests
    eingespeistes); fehlt es unter Windows, die Kommandozeile aus dem Kernel, geprüft wie
    beim Staffel-Hook (:func:`ist_session_zeile`).
    """
    try:
        name = _proc(pid, "comm").decode(errors="replace").strip()
        argv = [teil.decode(errors="replace") for teil in _proc(pid, "cmdline").split(b"\0")]
    except _KeinProc:
        return _ist_claude_windows(pid) if _WINDOWS else False
    except (FileNotFoundError, ProcessLookupError):
        return False
    except OSError as fehler:
        log.warning("/proc/%s nicht lesbar (%s) — zählt als Claude.", pid, fehler)
        return True
    argv0 = argv[0] if argv else ""
    if name == "claude" or Path(argv0).name == "claude":
        return True
    if name != "node" and Path(argv0).name != "node":
        return False
    skript = argv[1] if len(argv) > 1 else ""
    return Path(skript).name == "claude" or skript.endswith("claude-code/cli.js")


def _ist_claude_windows(pid: int) -> bool:
    """``ist_claude`` unter Windows: leere Kommandozeile heißt weg oder unlesbar — ``lebt`` entscheidet."""
    try:
        zeile = _kommandozeile_windows(pid)
    except OSError as fehler:
        log.warning("Kommandozeile von %s nicht lesbar (%s) — zählt als Claude.", pid, fehler)
        return True
    if zeile:
        return ist_session_zeile(zeile, windows=True)
    if not _lebt_windows(pid):
        return False
    log.warning("Kommandozeile von %s nicht lesbar — zählt als Claude.", pid)
    return True


def senden(pids: Sequence[int], sig: signal.Signals) -> None:
    """Schickt ``sig`` an jede PID in Listenfolge; schon beendete zählen nicht als Fehler.

    Windows meldet einen beendeten Prozess nicht als ``ProcessLookupError``, sondern als
    ``OSError`` (ungültiger Parameter bzw. Zugriff verweigert, solange noch ein Handle offen
    ist) — dort zählt nur ein Fehler an einem noch lebenden Prozess, und der landet im Log.
    """
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            continue
        except OSError as fehler:
            if not _WINDOWS:
                raise
            if lebt(pid):
                log.warning("Signal %s an PID %d fehlgeschlagen: %s", sig, pid, fehler)


def _warte_tot(
    pids: Sequence[int],
    warte_s: float,
    takt_s: float,
    schlafen: Callable[[float], None],
) -> bool:
    """True, sobald keiner der ``pids`` mehr lebt; höchstens ``warte_s / takt_s`` Takte."""
    for _ in range(round(warte_s / takt_s)):
        if not any(lebt(pid) for pid in pids):
            return True
        schlafen(takt_s)
    return False


def beenden(
    pids: Sequence[int],
    stufen: Sequence[Stufe],
    *,
    warten_auf: Sequence[int] | None = None,
    takt_s: float = 0.1,
    schlafen: Callable[[float], None] | None = None,
    eskalation: Callable[[signal.Signals, list[int]], None] | None = None,
) -> bool:
    """Beendet ``pids`` stufenweise (z. B. SIGTERM, dann SIGKILL).

    Je Stufe bekommen die noch lebenden ``pids`` das Signal — in der Reihenfolge der
    Liste —, dann wird bis zur Wartezeit der Stufe im Takt ``takt_s`` gewartet, bis
    keiner aus ``warten_auf`` (Vorgabe: alle ``pids``) mehr lebt. ``eskalation`` hört
    vor jeder Folgestufe, welches Signal an welche Überlebenden geht (für Logzeilen).
    ``schlafen`` (Vorgabe: ``time.sleep``) ist die Uhr-Naht für Tests.
    Ergebnis: True, wenn am Ende keiner aus ``warten_auf`` mehr lebt.
    """
    schlafen = schlafen or time.sleep
    ziel = list(pids) if warten_auf is None else list(warten_auf)
    for nr, (sig, warte_s) in enumerate(stufen):
        lebende = [pid for pid in pids if lebt(pid)]
        if not lebende:
            return True
        if nr and eskalation is not None:
            eskalation(sig, lebende)
        senden(lebende, sig)
        if _warte_tot(ziel, warte_s, takt_s, schlafen):
            return True
    return not any(lebt(pid) for pid in ziel)


# --- Session der Staffel finden und beenden (Linux + Windows, #501) ----------------------

#: Höchstens so viele Vorfahren werden nach der Session abgesucht.
MAX_VORFAHREN = 12
#: Frist, die eine Session zum geordneten Beenden bekommt, bevor SIGKILL folgt (Linux).
NACHLAUF_SEKUNDEN = 20.0
#: Programme, die nur Befehle weiterreichen (``sh -c <hook>``) — nie die Session.
WRAPPER_NAMEN = ("sh", "bash", "dash", "zsh", "env")
#: Kommandozeilen, die nie die Session sind (Launcher, Hook selbst).
NIE_SESSION = ("bau.py", "staffel_stop.py")
_WINDOWS = sys.platform == "win32"
#: Endungen, die unter Windows nicht zum Programmnamen gehören (``ssh.exe``, ``claude.cmd``).
_WINDOWS_ENDUNGEN = (".exe", ".cmd", ".bat")


def programm_name(arg: str, *, windows: bool = _WINDOWS) -> str:
    """Programmname aus ``argv[0]`` (oder einem Befehlswort) — einzige Stelle dafür.

    Jeder Vergleich gegen einen festen Namen (``ssh``, ``bash``, ``claude``, ``pytest`` …)
    geht hierüber, damit er unter Windows genauso greift wie unter Linux.
    Linux: Basisname, sonst nichts (``/usr/bin/ssh`` → ``ssh``; Groß/Klein und Endungen
    bleiben). Windows: Anführung weg, Basisname nach Windows-Regeln (``\\`` und ``/``),
    klein geschrieben, ``.exe``/``.cmd``/``.bat`` ab — ``C:\\Windows\\System32\\OpenSSH\\ssh.exe``,
    ``SSH.EXE`` und ``ssh`` heißen alle ``ssh``. ``windows`` wählt die Form; Standard ist
    das laufende System.
    """
    if not windows:
        return Path(arg).name
    name = PureWindowsPath(arg.strip('"')).name.lower()
    for endung in _WINDOWS_ENDUNGEN:
        if name.endswith(endung):
            return name[: -len(endung)]
    return name


def ohne_fenster() -> dict[str, Any]:
    """Popen-Optionen für Hilfsprozesse: unter Windows ohne sichtbares Fenster.

    Nie ``DETACHED_PROCESS``: ohne Konsole bekäme jedes Kind eine eigene, und Windows
    Terminal reißt dafür je ein Fenster nach vorn (Befund 27.09.2026).
    """
    if _WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {}


def ist_wrapper_zeile(zeile: str) -> bool:
    """Reicht diese Kommandozeile nur einen Befehl weiter (``sh -c '…'``, ``bash -lc '…'``)?

    Ein solcher Wrapper ist bloß Elternprozess des echten Programms, kein eigener Lauf:
    ein tmux-Fenster startet den Launcher als ``sh -c '… bau.py <N> …; exec bash'``, und
    eine Duplikat-Prüfung darf ihn nicht als zweiten ``bau.py`` zählen. Eine führende PID
    (Zeilenform von ``pgrep -af``) wird übersprungen.
    """
    teile = zeile.split()
    if teile and teile[0].isdigit():
        teile = teile[1:]
    return len(teile) >= 2 and programm_name(teile[0]) in WRAPPER_NAMEN and teile[1].startswith("-") and "c" in teile[1]


def ist_session_zeile(zeile: str, *, windows: bool = _WINDOWS) -> bool:
    """Ist diese Kommandozeile die Claude-Session selbst?

    Ein Teilstring-Treffer auf „claude" genügt nicht: der direkte Vorfahr des
    Hooks ist ``/bin/sh -c <hook-befehl>``, und dieser Befehl kann „claude" im
    Pfad tragen (``~/.claude/…``). Getroffen wird nur ein Programm, das
    ``claude`` heißt oder im Paket ``claude-code`` liegt. Unter Windows zählen
    Anführungszeichen und Endungen nicht zum Namen (:func:`programm_name`). ``windows`` wählt
    die Zeilenform; Standard ist das laufende System, Windows-Prozesslisten
    (``_prozesse_windows``) übergeben ``True`` unabhängig davon.
    """
    teile = [t.strip('"') for t in zeile.split()] if windows else zeile.split()
    if not teile:
        return False
    if any(marke in zeile for marke in NIE_SESSION):
        return False
    pfad_art = PureWindowsPath if windows else Path
    programm = programm_name(teile[0], windows=windows)
    if programm in WRAPPER_NAMEN and "-c" in teile[1:3]:
        return False
    for teil in teile:
        if teil.startswith("-"):
            continue
        pfad = pfad_art(teil)
        if pfad.name.startswith("claude"):
            return True
        if "claude-code" in pfad.parts:
            return True
    return False


def _ppid_linux(pid: int) -> int | None:
    try:
        zeilen = _proc(pid, "status").decode("utf-8", errors="replace")
    except OSError:
        return None
    for zeile in zeilen.splitlines():
        if zeile.startswith("PPid:"):
            try:
                return int(zeile.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def _cmdline_linux(pid: int) -> str:
    try:
        rohwert = _proc(pid, "cmdline")
    except OSError:
        return ""
    return rohwert.replace(b"\x00", b" ").decode("utf-8", "replace")


def _vorfahren_linux(pid: int) -> Iterator[tuple[int, str]]:
    """``(pid, Kommandozeile)`` der Vorfahren aus ``/proc``, nächster zuerst."""
    aktuell = _ppid_linux(pid)
    while aktuell is not None:
        yield aktuell, _cmdline_linux(aktuell)
        aktuell = _ppid_linux(aktuell)


# --- Windows-Prozessliste ohne Prozess-Start (#154) ---------------------------------------
# Früher startete jede Abfrage PowerShell + WMI ``Win32_Process`` (~1,4 s). Jetzt liefert ein
# einziger Kernel-Aufruf ``NtQuerySystemInformation(SystemProcessInformation)`` PID,
# Eltern-PID und Startzeit aller Prozesse — dieselbe Quelle, aus der auch die Toolhelp-
# Momentaufnahme (``CreateToolhelp32Snapshot``) liest; Toolhelp lässt aber die Startzeit weg,
# die dann je Prozess per ``OpenProcess`` nachgeholt werden müsste (gemessen: Toolhelp
# allein ~25 ms, mit Startzeiten ~36 ms, dieser Weg ~10 ms bei ~750 Prozessen). Die
# Kommandozeile (``NtQueryInformationProcess``) wird nur gelesen, wo ein Aufrufer sie
# braucht (``prozesse``, Vorfahren-Kette); nicht zu öffnende Prozesse (geschützte
# Systemprozesse, erhöhte Prozesse fremder Konten) haben eine leere — wie WMI ohne Admin.

_SYSTEM_PROCESS_INFORMATION = 5  # SYSTEM_INFORMATION_CLASS
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_COMMAND_LINE_INFORMATION = 60  # PROCESSINFOCLASS, ab Windows 8.1
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
#: Startgröße des Puffers; wächst bei Bedarf und merkt sich nur die Größe, nie Daten.
_puffer_bytes = 2 << 20


class _UnicodeString(ctypes.Structure):
    """``UNICODE_STRING``: Länge in Bytes, Zeiger auf UTF-16."""

    _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_void_p)]


class _ProzessInfo(ctypes.Structure):
    """Kopf von ``SYSTEM_PROCESS_INFORMATION`` (``winternl.h``) bis zur Eltern-PID."""

    _fields_ = [
        ("NextEntryOffset", ctypes.c_ulong),
        ("NumberOfThreads", ctypes.c_ulong),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", ctypes.c_ulong),
        ("NumberOfThreadsHighWatermark", ctypes.c_ulong),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", _UnicodeString),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
    ]


@functools.cache
def _win32() -> tuple[Any, Any]:
    """(kernel32 mit ``use_last_error``, ntdll) mit Signaturen — eigene Instanzen,
    ``ctypes.windll`` bleibt unberührt.

    Nur 64-Bit-Python: ``_ProzessInfo`` und die Zeiger-Felder sind für x64 geprüft; ein
    32-Bit-Python (unter WOW64) bekäme ohne diese Prüfung still falsche Listen.
    """
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise OSError(
            "Windows-Prozessliste braucht 64-Bit-Python "
            f"(dieses Python hat {ctypes.sizeof(ctypes.c_void_p) * 8}-Bit-Zeiger)"
        )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    nt = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
    zeiger, ulong = ctypes.c_void_p, ctypes.c_ulong
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [ulong, ctypes.c_int, ulong], zeiger
    kernel.CloseHandle.argtypes = [zeiger]
    kernel.GetExitCodeProcess.argtypes = [zeiger, ctypes.POINTER(ulong)]
    kernel.ReadProcessMemory.argtypes = [zeiger, zeiger, zeiger, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    nt.NtQuerySystemInformation.argtypes = [ulong, zeiger, ulong, ctypes.POINTER(ulong)]
    nt.NtQuerySystemInformation.restype = ulong
    nt.NtQueryInformationProcess.argtypes = [zeiger, ulong, zeiger, ulong, ctypes.POINTER(ulong)]
    nt.NtQueryInformationProcess.restype = ulong
    # Ordner-Kennung (siehe :func:`arbeitsordner_in_windows`)
    kernel.GetCurrentProcess.argtypes, kernel.GetCurrentProcess.restype = [], zeiger
    kernel.DuplicateHandle.argtypes = [zeiger, zeiger, zeiger, ctypes.POINTER(zeiger), ulong, ctypes.c_int, ulong]
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ulong, ulong, zeiger, ulong, ulong, zeiger]
    kernel.CreateFileW.restype = zeiger
    kernel.GetFinalPathNameByHandleW.argtypes = [zeiger, ctypes.c_wchar_p, ulong, ulong]
    kernel.GetFinalPathNameByHandleW.restype = ulong
    for abfrage in (nt.NtQueryInformationFile, nt.NtQueryVolumeInformationFile):
        abfrage.argtypes, abfrage.restype = [zeiger, zeiger, zeiger, ulong, ulong], ulong
    return kernel, nt


@functools.cache
def _advapi32() -> Any:
    """advapi32 mit Signaturen für den Token-Besitzer (eigene Instanz)."""
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    zeiger, ulong = ctypes.c_void_p, ctypes.c_ulong
    advapi.OpenProcessToken.argtypes = [zeiger, ulong, ctypes.POINTER(zeiger)]
    advapi.GetTokenInformation.argtypes = [zeiger, ctypes.c_int, zeiger, ulong, ctypes.POINTER(ulong)]
    advapi.IsValidSid.argtypes = [zeiger]
    advapi.GetLengthSid.argtypes, advapi.GetLengthSid.restype = [zeiger], ulong
    return advapi


def _momentaufnahme_windows() -> dict[int, tuple[int, int]]:
    """PID → (Eltern-PID, Startzeit als FILETIME, 0 = unbekannt); unlesbar = ``OSError``."""
    global _puffer_bytes
    _, nt = _win32()
    noetig = ctypes.c_ulong(0)
    for _ in range(5):  # die Liste kann zwischen zwei Versuchen wachsen
        puffer = ctypes.create_string_buffer(_puffer_bytes)
        status = nt.NtQuerySystemInformation(_SYSTEM_PROCESS_INFORMATION, puffer, _puffer_bytes, ctypes.byref(noetig))
        if status != _STATUS_INFO_LENGTH_MISMATCH:
            break
        _puffer_bytes = max(_puffer_bytes * 2, noetig.value + (1 << 16))
    if status:
        raise OSError(f"NtQuerySystemInformation: Status 0x{status:08X}")
    liste: dict[int, tuple[int, int]] = {}
    versatz = 0
    while True:
        eintrag = _ProzessInfo.from_buffer(puffer, versatz)
        liste[eintrag.UniqueProcessId or 0] = (eintrag.InheritedFromUniqueProcessId or 0, max(eintrag.CreateTime, 0))
        if not eintrag.NextEntryOffset:
            return liste
        versatz += eintrag.NextEntryOffset


def _kommandozeile_windows(pid: int) -> str:
    """Kommandozeile von ``pid``; nicht lesbar (weg, geschützt) = ``""``."""
    kernel, nt = _win32()
    griff = kernel.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not griff:
        return ""
    try:
        laenge = ctypes.c_ulong(0)
        status = nt.NtQueryInformationProcess(griff, _PROCESS_COMMAND_LINE_INFORMATION, None, 0, ctypes.byref(laenge))
        if status != _STATUS_INFO_LENGTH_MISMATCH or laenge.value < ctypes.sizeof(_UnicodeString):
            return ""
        puffer = ctypes.create_string_buffer(laenge.value)
        if nt.NtQueryInformationProcess(griff, _PROCESS_COMMAND_LINE_INFORMATION, puffer, laenge, ctypes.byref(laenge)):
            return ""
        text = _UnicodeString.from_buffer(puffer)
        return ctypes.wstring_at(text.Buffer, text.Length // 2) if text.Buffer and text.Length else ""
    finally:
        kernel.CloseHandle(griff)


def _prozesse_windows() -> dict[int, tuple[int, str, int]]:
    """PID → (Eltern-PID, Kommandozeile, Startzeit) aller Prozesse; Fehler = leer."""
    try:
        liste = _momentaufnahme_windows()
    except OSError as fehler:
        log.warning("Windows-Prozessliste nicht lesbar: %s", fehler)
        return {}
    return {pid: (eltern, _kommandozeile_windows(pid), start) for pid, (eltern, start) in liste.items()}


# --- Windows-Arbeitsordner (cwd) aus dem PEB ----------------------------------------------
# Win32_Process kennt keine cwd. Sie steht im Prozess selbst: PEB → ProcessParameters →
# CurrentDirectory (x64-Versätze aus ``winternl.h``/``RTL_USER_PROCESS_PARAMETERS``). Nötig für
# Git-Bash-/MSYS-Prozesse: deren cwd-Griff erlaubt Umbenennen und Löschen, die
# Umbenennungs-Probe sieht sie nicht. MSYS setzt bei ``cd`` die Win32-cwd mit (gemessen).
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010
_STILL_ACTIVE = 259
_FEHLER_ZUGRIFF_VERWEIGERT = 5
_FEHLER_UNGUELTIGER_PARAMETER = 87  # OpenProcess: PID gibt es nicht (mehr)
_PEB_PROCESS_PARAMETERS = 0x20
_PARAMS_CURRENT_DIRECTORY = 0x38


class _LeereCwd(OSError):
    """PEB lesbar, cwd darin (noch) leer — Startphase: sperrt immer, auch bei fremden Prozessen."""


def _arbeitsordner_windows(pid: int) -> str | None:
    """Win32-cwd von ``pid``; ``None`` = nicht zu prüfen (weg, beendet, fremd/geschützt —
    wie ``PermissionError`` unter ``/proc``); ``OSError`` = läuft, ist offen, liefert aber nichts.

    ponytail: 32-Bit-Prozesse (WOW64) führen ihre cwd im 32-Bit-PEB, hier steht die vom
    Start; Upgrade: ``ProcessWow64Information`` + PEB32 lesen. Die Umbenennungs-Probe
    deckt native 32-Bit-Prozesse ab.
    """
    kernel, _ = _win32()
    ctypes.set_last_error(0)
    griff = kernel.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid)
    if not griff:
        fehler = ctypes.get_last_error()
        if fehler in (_FEHLER_ZUGRIFF_VERWEIGERT, _FEHLER_UNGUELTIGER_PARAMETER):
            return None
        raise OSError(fehler, f"OpenProcess({pid}) gescheitert")
    try:
        code = ctypes.c_ulong(0)
        if kernel.GetExitCodeProcess(griff, ctypes.byref(code)) and code.value != _STILL_ACTIVE:
            return None  # beendet, nur noch Griffe offen (wie Zombie unter Linux)
        kopf = _curdir(griff, pid)
        bytes_lang, puffer_adresse = int.from_bytes(kopf[:2], "little"), int.from_bytes(kopf[8:16], "little")
        if not bytes_lang or not puffer_adresse:
            raise _LeereCwd(f"Prozess {pid}: leere cwd im PEB")
        try:
            return _speicher(griff, pid, puffer_adresse, bytes_lang).decode("utf-16-le")
        except UnicodeDecodeError as fehler:  # kaputte cwd = nicht lesbar, nie Abbruch
            raise OSError(f"Prozess {pid}: cwd nicht als UTF-16 lesbar ({fehler})") from None
    finally:
        kernel.CloseHandle(griff)


_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1


def _benutzer_windows(pid: int) -> bytes | None:
    """SID des Benutzers, dem ``pid`` gehört (aus dem Prozess-Token); nicht lesbar = ``None``."""
    kernel, _ = _win32()
    advapi = _advapi32()
    prozess = kernel.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not prozess:
        return None
    try:
        marke = ctypes.c_void_p()
        if not advapi.OpenProcessToken(prozess, _TOKEN_QUERY, ctypes.byref(marke)):
            return None
        try:
            puffer = ctypes.create_string_buffer(256)  # TOKEN_USER: Zeiger + Attribute + SID (≤ 68 Bytes)
            laenge = ctypes.c_ulong(0)
            if not advapi.GetTokenInformation(marke, _TOKEN_USER, puffer, 256, ctypes.byref(laenge)):
                return None
            sid = ctypes.c_void_p.from_buffer(puffer).value
            if not sid or not advapi.IsValidSid(sid):
                return None
            return ctypes.string_at(sid, advapi.GetLengthSid(sid))
        finally:
            kernel.CloseHandle(marke)
    finally:
        kernel.CloseHandle(prozess)


def _ist_fremd_windows(pid: int) -> bool:
    """Gehört ``pid`` nachweislich einem anderen Benutzer (SID) als dieser Lauf? Eigener Benutzer
    ist nie fremd, auch in einer anderen Sitzung (ssh, Aufgabenplanung); nicht feststellbar
    (eine SID fehlt) = ``False`` — im Zweifel eigen, der Prozess sperrt weiter."""
    fremd, selbst = _benutzer_windows(pid), _benutzer_windows(os.getpid())
    return fremd is not None and selbst is not None and fremd != selbst


def _speicher(prozess: int, pid: int, adresse: int, anzahl: int) -> bytes:
    """``anzahl`` Bytes ab ``adresse`` aus dem Speicher von ``prozess``; unvollständig = ``OSError``."""
    kernel, _ = _win32()
    puffer = ctypes.create_string_buffer(anzahl)
    gelesen = ctypes.c_size_t(0)
    ok = kernel.ReadProcessMemory(prozess, adresse, puffer, anzahl, ctypes.byref(gelesen))
    if not ok or gelesen.value != anzahl:
        raise OSError(ctypes.get_last_error(), f"ReadProcessMemory({pid}) gescheitert")
    return puffer.raw


def _curdir(prozess: int, pid: int) -> bytes:
    """``CURDIR`` aus dem PEB (x64): Bytes 0–15 ``UNICODE_STRING DosPath``, 16–23 ``Handle``."""
    _, nt = _win32()
    basis = (ctypes.c_void_p * 6)()  # PROCESS_BASIC_INFORMATION, [1] = PebBaseAddress
    laenge = ctypes.c_ulong(0)
    status = nt.NtQueryInformationProcess(prozess, 0, basis, ctypes.sizeof(basis), ctypes.byref(laenge))
    if status or not basis[1]:
        raise OSError(f"NtQueryInformationProcess({pid}): Status 0x{status:08X}")
    parameter = int.from_bytes(_speicher(prozess, pid, basis[1] + _PEB_PROCESS_PARAMETERS, 8), "little")
    return _speicher(prozess, pid, parameter + _PARAMS_CURRENT_DIRECTORY, 24)


# --- Ordner-Identität statt Pfad-Text (PR 233, Fix-Runde 2) ---------------------------------
# Der cwd-TEXT im PEB sagt nur, wie der Prozess seine cwd einmal genannt hat. Wird die Junction,
# über die er hineinkam, danach entfernt oder umgebogen, oder heißt die cwd ``\\localhost\C$\…``,
# zeigt der Text woandershin als der Ordner, in dem der Prozess steht. Der Griff daneben im PEB
# (``CURDIR.Handle``) hält den echten Ordner offen: duplizieren, Kennung (Volume-Seriennummer +
# Datei-Index) lesen, mit dem Ziel vergleichen. Die Kennung ist über die Loopback-Freigabe
# dieselbe wie lokal (gemessen). Für „darunter“ liefert der Griff den echten End-Pfad; dessen
# Eltern werden geöffnet und ebenfalls per Kennung verglichen, nie per Text.
_PROCESS_DUP_HANDLE = 0x0040
_DUPLICATE_SAME_ACCESS = 0x2
_FILE_SHARE_ALLE = 0x7
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000  # nötig, um Ordner zu öffnen
_UNGUELTIGER_GRIFF = ctypes.c_void_p(-1).value
_FILE_FS_VOLUME_INFORMATION = 1
_FILE_STANDARD_INFORMATION = 5
_FILE_INTERNAL_INFORMATION = 6
_STATUS_BUFFER_OVERFLOW = 0x80000005  # Volume-Name passt nicht — fester Teil ist gefüllt


class ArbeitsordnerBefund(NamedTuple):
    """Ein Prozess in :func:`arbeitsordner_in_windows`."""

    text: str | None  #: cwd wie im PEB; ``None`` = unlesbar
    drin: bool | None  #: cwd ist der Ordner oder liegt darin (Identität); ``None`` = nicht feststellbar
    grund: str = ""  #: bei ``None``: warum


def _kennung(griff: int) -> tuple[int, int]:
    """(Volume-Seriennummer, Datei-Index) eines offenen Ordner-Griffs; kein Ordner, kein
    Datei-Griff oder Fehler = ``OSError``. Braucht keine Zugriffsrechte am Griff (der
    cwd-Griff hat nur ``FILE_TRAVERSE``)."""
    _, nt = _win32()
    io = (ctypes.c_byte * 16)()  # IO_STATUS_BLOCK
    puffer = ctypes.create_string_buffer(512)
    status = nt.NtQueryVolumeInformationFile(griff, io, puffer, 512, _FILE_FS_VOLUME_INFORMATION)
    if status and status != _STATUS_BUFFER_OVERFLOW:
        raise OSError(f"NtQueryVolumeInformationFile: Status 0x{status:08X}")
    serie = int.from_bytes(puffer.raw[8:12], "little")  # nach VolumeCreationTime
    standard = ctypes.create_string_buffer(24)
    status = nt.NtQueryInformationFile(griff, io, standard, 24, _FILE_STANDARD_INFORMATION)
    if status:
        raise OSError(f"NtQueryInformationFile(Standard): Status 0x{status:08X}")
    if not standard.raw[21]:  # FILE_STANDARD_INFORMATION.Directory
        raise OSError("Griff zeigt auf keinen Ordner")
    index = ctypes.create_string_buffer(8)
    status = nt.NtQueryInformationFile(griff, io, index, 8, _FILE_INTERNAL_INFORMATION)
    if status:
        raise OSError(f"NtQueryInformationFile(Index): Status 0x{status:08X}")
    return serie, int.from_bytes(index.raw, "little")


def _endpfad(griff: int) -> str:
    r"""Echter End-Pfad des Ordners hinter ``griff`` (``GetFinalPathNameByHandleW``: lange
    Namen, Junctions aufgelöst, wo der Ordner JETZT liegt), mit ``\\?\``-Vorsatz; ``OSError``."""
    kernel, _ = _win32()
    puffer = ctypes.create_unicode_buffer(1024)
    laenge = kernel.GetFinalPathNameByHandleW(griff, puffer, 1024, 0)
    if laenge >= 1024:
        puffer = ctypes.create_unicode_buffer(laenge + 1)
        laenge = kernel.GetFinalPathNameByHandleW(griff, puffer, laenge + 1, 0)
    if not laenge or laenge >= len(puffer):
        raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW gescheitert")
    return puffer.value


def _pfad_teile(endpfad: str) -> tuple[list[str], int]:
    r"""End-Pfad je Teil und Zahl der Wurzel-Teile: ``\\?\C:\…``/``\\?\Volume{…}\…`` = 4
    (lokal), ``\\?\UNC\srv\frei\…`` = 6 (Netz); andere Form = ``OSError``."""
    teile = endpfad.split("\\")
    if teile and not teile[-1]:
        teile.pop()  # Wurzel endet auf ``\``
    if teile[:3] != ["", "", "?"] or len(teile) < 4:
        raise OSError(f"unbekannte End-Pfad-Form: {endpfad}")
    wurzel = 6 if teile[3].upper() == "UNC" else 4
    if len(teile) < wurzel:
        raise OSError(f"unbekannte End-Pfad-Form: {endpfad}")
    return teile, wurzel


def _ordner_von(pfad: str) -> tuple[tuple[int, int], str]:
    """(Kennung, End-Pfad) des Ordners hinter ``pfad`` — per Griff, Junctions folgt Windows
    beim Öffnen; nicht zu öffnen = ``OSError``."""
    kernel, _ = _win32()
    griff = kernel.CreateFileW(pfad, 0, _FILE_SHARE_ALLE, None, _OPEN_EXISTING, _FILE_FLAG_BACKUP_SEMANTICS, None)
    if not griff or griff == _UNGUELTIGER_GRIFF:
        raise OSError(ctypes.get_last_error(), f"Ordner nicht zu öffnen: {pfad}")
    try:
        return _kennung(griff), _endpfad(griff)
    finally:
        kernel.CloseHandle(griff)


def _liegt_darin(griff: int, ziel: tuple[tuple[int, int], str], kennungen: dict[str, tuple[int, int]]) -> bool:
    r"""Ist der Ordner hinter ``griff`` das Ziel (Kennung, End-Pfad aus :func:`_ordner_von`) oder
    liegt er darin? Gleiche Kennung ⇒ ja. Beide End-Pfade lokal ⇒ je Pfad-Teil, Groß/Klein egal
    (End-Pfade sind kanonisch; so muss kein Eltern-Ordner geöffnet werden — ``WindowsApps``
    etwa verweigert das). Sonst (Netz-Form, z. B. ``\\localhost\C$``: gleicher Ordner, anderer
    Text) werden die echten Eltern geöffnet und per Kennung verglichen. ``kennungen`` =
    Zwischenspeicher Eltern-Pfad → Kennung; Fehler = ``OSError``."""
    if _kennung(griff) == ziel[0]:
        return True
    teile, wurzel = _pfad_teile(_endpfad(griff))
    ziel_teile, ziel_wurzel = _pfad_teile(ziel[1])
    if wurzel == ziel_wurzel == 4:
        return [t.casefold() for t in teile[: len(ziel_teile)]] == [t.casefold() for t in ziel_teile]
    for n in range(len(teile) - 1, wurzel - 1, -1):
        pfad = "\\".join(teile[:n]) + ("\\" if n == wurzel else "")  # Wurzel braucht den Schluss-``\``
        if pfad not in kennungen:
            kennungen[pfad] = _ordner_von(pfad)[0]
        if kennungen[pfad] == ziel[0]:
            return True
    return False


def _arbeitsordner_griff_windows(pid: int) -> int | None:
    """Eigene Kopie des cwd-Griffs von ``pid`` (Aufrufer schließt); ``None`` = Prozess weg oder
    beendet; ``OSError`` = läuft, Griff aber nicht zu übernehmen (fehlt, Rechte, cwd wechselte
    während des Lesens)."""
    kernel, _ = _win32()
    ctypes.set_last_error(0)
    prozess = kernel.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ | _PROCESS_DUP_HANDLE, False, pid)
    if not prozess:
        fehler = ctypes.get_last_error()
        if fehler == _FEHLER_UNGUELTIGER_PARAMETER:
            return None
        raise OSError(fehler, f"OpenProcess({pid}) zum Übernehmen des cwd-Griffs gescheitert")
    try:
        code = ctypes.c_ulong(0)
        if kernel.GetExitCodeProcess(prozess, ctypes.byref(code)) and code.value != _STILL_ACTIVE:
            return None

        vorher = _curdir(prozess, pid)
        fremd = int.from_bytes(vorher[16:24], "little")
        if not fremd:
            raise OSError(f"Prozess {pid}: kein cwd-Griff im PEB")
        kopie = ctypes.c_void_p()
        if not kernel.DuplicateHandle(
            prozess, fremd, kernel.GetCurrentProcess(), ctypes.byref(kopie), 0, False, _DUPLICATE_SAME_ACCESS
        ):
            raise OSError(ctypes.get_last_error(), f"DuplicateHandle({pid}) gescheitert")
        if _curdir(prozess, pid) != vorher:  # cwd wechselte: der Griff-Wert kann schon einem anderen gehören
            kernel.CloseHandle(kopie)
            raise OSError(f"Prozess {pid}: cwd wechselte während der Prüfung")
        return kopie.value
    finally:
        kernel.CloseHandle(prozess)


def _arbeitsordner_drin(
    pid: int, ziel: tuple[tuple[int, int], str], kennungen: dict[str, tuple[int, int]]
) -> bool | None:
    """Liegt die echte cwd von ``pid`` im Ziel (siehe :func:`_liegt_darin`)? ``None`` = Prozess
    weg; ``OSError`` = nicht feststellbar. Der übernommene Griff wird immer geschlossen."""
    kernel, _ = _win32()
    griff = _arbeitsordner_griff_windows(pid)
    if griff is None:
        return None
    try:
        return _liegt_darin(griff, ziel, kennungen)
    finally:
        kernel.CloseHandle(griff)


def arbeitsordner_in_windows(ordner: str | Path) -> dict[int, ArbeitsordnerBefund]:
    """Steht ein Prozess in ``ordner``? PID → :class:`ArbeitsordnerBefund` aller prüfbaren
    Prozesse. ``drin`` kommt aus der Identität des Ordners, in dem der Prozess wirklich steht
    (cwd-Griff aus dem PEB), nicht aus dem Pfad-Text — Junction danach entfernt oder umgebogen,
    ``\\\\localhost\\C$\\…``, 8.3, ``subst`` ändern daran nichts. ``text`` bleibt für Aufrufer,
    die zusätzlich per Text sperren wollen.

    Nicht prüfbar (Prozess fremd/geschützt: ``OpenProcess`` verweigert, siehe
    :func:`_arbeitsordner_windows`) fehlen wie bisher, ebenso offene Prozesse mit unlesbarer
    cwd, die nachweislich einem anderen Benutzer gehören
    (:func:`_ist_fremd_windows`, #552 — ein geschützter Dienst sperrte sonst jedes Aufräumen).
    Jeder andere Fehler eines laufenden Prozesses ⇒ ``drin=None`` mit Grund (Aufrufer
    entscheidet fail-closed). ``ordner`` nicht zu öffnen oder Momentaufnahme unlesbar = ``OSError``.

    ponytail: unlesbarer Prozess eines fremden Benutzers mit cwd im Ordner bleibt unentdeckt
    (wie bei #243); offene Dateien fängt im echten Lauf die Umbenennungs-Probe."""
    ziel = _ordner_von(str(ordner))
    kennungen: dict[str, tuple[int, int]] = {}
    ergebnis: dict[int, ArbeitsordnerBefund] = {}
    for pid in _momentaufnahme_windows():
        if pid in (0, 4):  # Leerlauf- und System-Prozess: kein Benutzer-PEB
            continue
        try:
            text = _arbeitsordner_windows(pid)
        except OSError as fehler:
            log.debug("cwd von Prozess %s unlesbar: %s", pid, fehler)
            if not isinstance(fehler, _LeereCwd) and _ist_fremd_windows(pid):
                continue  # fremder Benutzer: wie nicht zu öffnen (#243, #552)
            ergebnis[pid] = ArbeitsordnerBefund(None, None, f"cwd unlesbar: {fehler}")
            continue
        if text is None:
            continue  # nicht prüfbar (Grenze aus #243) oder schon beendet
        try:
            drin = _arbeitsordner_drin(pid, ziel, kennungen)
        except OSError as fehler:
            log.debug("cwd-Ordner von Prozess %s nicht feststellbar: %s", pid, fehler)
            ergebnis[pid] = ArbeitsordnerBefund(text, None, f"Ordner-Identität nicht lesbar: {fehler}")
            continue
        if drin is not None:
            ergebnis[pid] = ArbeitsordnerBefund(text, drin)
    return ergebnis


def _vorfahren_windows(pid: int) -> Iterator[tuple[int, str]]:
    """Wie ``_vorfahren_linux``, aus einer Momentaufnahme (Kommandozeile nur der Vorfahren).

    Windows vergibt PIDs neu: ein „Eltern“-Prozess, der nach dem Kind gestartet wurde,
    ist ein Fremder mit recycelter Nummer — dort endet die Kette.
    """
    try:
        prozesse = _momentaufnahme_windows()
    except OSError as fehler:
        log.warning("Windows-Prozessliste nicht lesbar: %s", fehler)
        return
    kind = prozesse.get(pid)
    while kind is not None:
        eltern_pid, kind_start = kind
        eltern = prozesse.get(eltern_pid)
        if eltern is None or eltern_pid == 0 or (eltern[1] and kind_start and eltern[1] > kind_start):
            return
        yield eltern_pid, _kommandozeile_windows(eltern_pid)
        kind = eltern


def session_vorfahr(pid: int, *, grenze: int | None = None) -> int | None:
    """Erster Vorfahr von ``pid``, dessen Kommandozeile die Claude-Session ist.

    Der Launcher (``bau.py``) darf nie getroffen werden — er startet die Folge-Session:
    taucht er auf oder ist die ``grenze`` (PID des Launchers) erreicht, ist das
    Ergebnis ``None``. Oberhalb liegen fremde Sessions, dort wird nie gesucht.
    """
    vorfahren = _vorfahren_windows(pid) if _WINDOWS else _vorfahren_linux(pid)
    for _, (aktuell, zeile) in zip(range(MAX_VORFAHREN), vorfahren):
        if aktuell <= 1 or (grenze is not None and aktuell == grenze):
            return None
        if "bau.py" in zeile:
            return None
        if ist_session_zeile(zeile):
            return aktuell
    return None


def session_beenden(pid: int, protokoll: Path | None = None) -> bool:
    """Beendet die Session ``pid``, ohne darauf zu warten. False = gar nicht angestoßen.

    Der Aufrufer (Stop-Hook) ist selbst ein Kind der Session und darf nicht warten:
    Claude Code läuft erst nach dem Hook weiter. Ein abgelöster Nachläufer erledigt den
    Rest und überlebt das Sterben der Session.

    Linux: SIGTERM sofort, der Nachläufer schickt SIGKILL nach ``NACHLAUF_SEKUNDEN``.
    Windows kennt kein SIGTERM: der Nachläufer wartet, bis der Aufrufer fertig ist, und
    beendet dann den ganzen Baum (``taskkill /T /F`` — auch MCP-Server und Shells).

    ``protokoll``: Datei, an die der Nachläufer eine Ergebnis-Zeile anhängt (seine eigene
    Ausgabe sieht niemand). Ein gescheitertes ``taskkill`` steht dort als ``WARNUNG``.
    """
    if _WINDOWS and not _lebt_windows(pid):
        # Ohne diese Prüfung würde der Nachläufer eine inzwischen recycelte PID samt Baum beenden.
        log.warning("PID %d lebt nicht (mehr) — nichts zu beenden", pid)
        return False
    if not _WINDOWS:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            log.warning("SIGTERM an PID %d fehlgeschlagen: %s", pid, exc)
            return False
    befehl = [sys.executable, str(Path(__file__).resolve()), "--nachlauf", str(pid), str(os.getpid())]
    if protokoll is not None:
        befehl += ["--protokoll", str(protokoll)]
    optionen: dict[str, Any] = ohne_fenster() if _WINDOWS else {"start_new_session": True}
    try:
        subprocess.Popen(
            befehl,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **optionen,
        )
    except OSError as exc:
        log.warning("Nachläufer für PID %d nicht gestartet: %s", pid, exc)
        # Linux: SIGTERM ist raus, die Session endet trotzdem. Windows: nichts passiert.
        return not _WINDOWS
    return True


def _lebt_windows(pid: int) -> bool:
    import ctypes

    kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    griff = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not griff:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel.GetExitCodeProcess(griff, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(griff)


def _protokolliere(protokoll: Path | None, zeile: str) -> None:
    """Hängt ``zeile`` mit Zeitstempel an ``protokoll`` — Fehler beim Schreiben sind kein Abbruch."""
    if protokoll is None:
        return
    try:
        protokoll.parent.mkdir(parents=True, exist_ok=True)
        with protokoll.open("a", encoding="utf-8") as datei:
            datei.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {zeile}\n")
    except OSError as exc:
        log.warning("Nachlauf-Protokoll %s nicht schreibbar: %s", protokoll, exc)


def _nachlauf(pid: int, aufrufer: int, frist: float = NACHLAUF_SEKUNDEN, protokoll: Path | None = None) -> int:
    """Notnagel im eigenen Prozess (siehe ``session_beenden``)."""
    ende = time.monotonic() + frist
    if _WINDOWS:
        while time.monotonic() < ende and _lebt_windows(aufrufer):
            time.sleep(0.2)
        erg = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False, **ohne_fenster()
        )
        if erg.returncode == 0:
            log.info("taskkill /T /F %d → Exit 0", pid)
            _protokolliere(protokoll, f"OK taskkill /T /F {pid} → Exit 0")
        else:
            meldung = (erg.stdout or b"").decode(errors="replace").strip() + (erg.stderr or b"").decode(
                errors="replace"
            ).strip()
            log.warning("taskkill /T /F %d → Exit %d: %s", pid, erg.returncode, meldung)
            _protokolliere(protokoll, f"WARNUNG taskkill /T /F {pid} → Exit {erg.returncode}: {meldung[:200]}")
        return 0
    while time.monotonic() < ende:
        if not lebt(pid):
            _protokolliere(protokoll, f"OK Session {pid} nach SIGTERM beendet")
            return 0
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError as exc:
        _protokolliere(protokoll, f"OK Session {pid} vor SIGKILL beendet ({exc})")
        return 0
    _protokolliere(protokoll, f"OK SIGKILL an Session {pid} nach {frist:.0f} s")
    return 0


# --- Prozesse nach Muster suchen (#251) ---------------------------------------------------
# Eine Tür für „welche laufenden Prozesse passen auf dieses Muster?“. Linux fragt weiter
# ``pgrep -af`` (Verhalten und Fehlerbilder wie vor #251), Windows liest die Kernel-
# Prozessliste ohne Prozess-Start (#154) — dort gibt es kein ``pgrep``.


class ProzesslisteFehler(RuntimeError):
    """Die laufenden Prozesse ließen sich nicht abfragen (Werkzeug fehlt, Zeitgrenze, Kernel-Fehler).

    Der Text nennt das Werkzeug („pgrep nicht nutzbar …“, „Windows-Prozessliste nicht
    nutzbar …“). Aufrufer bleiben fail-closed: kein Treffer heißt nie „Abfrage gescheitert“.
    """


def passende(muster: str, *, zeitgrenze: float = 30) -> list[tuple[int, str]]:
    """``(PID, Kommandozeile)`` aller laufenden Prozesse, deren Kommandozeile auf ``muster`` passt.

    ``muster`` ist ein Regex, der in Python und als ``pgrep``-ERE gleich gilt; gesucht wird
    irgendwo in der ganzen Kommandozeile (``re.search``). Nie dabei: der eigene Prozess,
    seine Vorfahren und die Abfrage selbst. Lässt sich die Liste nicht lesen ⇒
    :class:`ProzesslisteFehler` statt einer leeren Liste. Ein ungültiges ``muster`` wirft
    ``re.error`` (Programmierfehler, kein Laufzeitfall).

    Linux: ``pgrep -af`` (Exit 1 = kein Treffer). Windows: Kernel-Prozessliste, je Prozess
    die Kommandozeile (geschützte Prozesse ohne lesbare Kommandozeile passen nie).
    """
    regex = re.compile(muster)
    if _WINDOWS:
        return _passende_windows(regex)
    return _passende_linux(muster, zeitgrenze)


def argv(pid: int) -> list[str]:
    """Argumente von ``pid`` als Liste; nicht lesbar (weg, geschützt) = ``[]``.

    Linux: ``/proc/<pid>/cmdline``. Windows: Kommandozeile nach Windows-Regeln zerlegt
    (``CommandLineToArgvW``), damit Aufrufer wie unter Linux nur Programm und erste
    Argumente prüfen können.
    """
    if _WINDOWS:
        try:
            return _zerlege_windows(_kommandozeile_windows(pid))
        except OSError:
            return []
    try:
        roh = _proc(pid, "cmdline")
    except OSError:
        return []
    return [teil.decode("utf-8", "replace") for teil in roh.split(b"\0") if teil]


def _passende_linux(muster: str, zeitgrenze: float) -> list[tuple[int, str]]:
    try:
        fertig = subprocess.run(
            ["pgrep", "-af", muster],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=zeitgrenze,
        )
    except subprocess.TimeoutExpired:
        raise ProzesslisteFehler(f"pgrep nicht nutzbar (Zeitgrenze {zeitgrenze:g} s überschritten)") from None
    except OSError as fehler:
        # Fester Kennsatz „No such file“: der Systemtext kann übersetzt sein.
        raise ProzesslisteFehler(f"pgrep nicht nutzbar (No such file — {fehler})") from None
    if fertig.returncode != 0:
        # Exit 1 = kein Treffer; ein „No such file“ im Fehlertext ist eine Störung (Regel vor #251).
        if "No such file" in fertig.stderr:
            raise ProzesslisteFehler(f"pgrep nicht nutzbar ({fertig.stderr.strip()[:300]})")
        return []
    eigene = {os.getpid()}
    vorfahr = _ppid_linux(os.getpid())
    while vorfahr is not None and vorfahr > 0 and vorfahr not in eigene:
        eigene.add(vorfahr)
        vorfahr = _ppid_linux(vorfahr)
    treffer = []
    for zeile in fertig.stdout.splitlines():
        pid, _, kommandozeile = zeile.partition(" ")
        if pid.isdigit() and int(pid) not in eigene:
            treffer.append((int(pid), kommandozeile))
    return treffer


def _passende_windows(regex: re.Pattern[str]) -> list[tuple[int, str]]:
    try:
        liste = _momentaufnahme_windows()
    except OSError as fehler:
        raise ProzesslisteFehler(f"Windows-Prozessliste nicht nutzbar ({fehler})") from None
    eigene = _eigene_kette_windows(liste)
    treffer = []
    for pid in liste:
        if pid <= 4 or pid in eigene:  # 0 = Leerlauf, 4 = System: ohne Kommandozeile
            continue
        kommandozeile = _kommandozeile_windows(pid)
        if kommandozeile and regex.search(kommandozeile):
            treffer.append((pid, kommandozeile))
    return treffer


def _eigene_kette_windows(liste: dict[int, tuple[int, int]]) -> set[int]:
    """Eigene PID und Vorfahren aus einer Momentaufnahme.

    Windows vergibt PIDs neu: ein „Eltern“prozess, der jünger ist als das Kind, ist ein
    Fremder mit wiederverwendeter PID — dort endet die Kette.
    """
    pid = os.getpid()
    kette = {pid}
    while pid in liste:
        eltern, start = liste[pid]
        if eltern <= 4 or eltern in kette or eltern not in liste:
            break
        eltern_start = liste[eltern][1]
        if start and eltern_start and eltern_start > start:
            break
        kette.add(eltern)
        pid = eltern
    return kette


@functools.cache
def _shell32() -> tuple[Any, Any]:
    """(shell32, kernel32) mit Signaturen für ``CommandLineToArgvW``/``LocalFree``."""
    shell = ctypes.WinDLL("shell32")  # type: ignore[attr-defined]
    kernel = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
    shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    return shell, kernel


def _zerlege_windows(kommandozeile: str) -> list[str]:
    """Windows-Kommandozeile → Argumente (Regeln der C-Laufzeit); leer bleibt leer."""
    if not kommandozeile.strip():
        return []  # CommandLineToArgvW("") liefert sonst den Pfad des eigenen Programms
    shell, kernel = _shell32()
    anzahl = ctypes.c_int(0)
    feld = shell.CommandLineToArgvW(kommandozeile, ctypes.byref(anzahl))
    if not feld:
        return kommandozeile.split()
    try:
        return [feld[i] for i in range(anzahl.value)]
    finally:
        kernel.LocalFree(ctypes.cast(feld, ctypes.c_void_p))


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--nachlauf":
        _ziel = Path(sys.argv[5]) if len(sys.argv) >= 6 and sys.argv[4] == "--protokoll" else None
        sys.exit(_nachlauf(int(sys.argv[2]), int(sys.argv[3]), protokoll=_ziel))
    sys.exit(2)
