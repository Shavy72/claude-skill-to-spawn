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
Stufe beendet also schon hart, ``SIGTERM`` lässt sich nicht abfangen. Nur ``ist_claude``
liest weiter ``/proc`` (unter Windows immer „kein Claude“).
"""

from __future__ import annotations

import ctypes
import functools
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path, PureWindowsPath
from typing import Any

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


def _proc(pid: int, datei: str) -> bytes:
    """Rohinhalt von ``/proc/<pid>/<datei>`` (Naht für Tests)."""
    return (_PROC / str(pid) / datei).read_bytes()


def ist_claude(pid: int) -> bool:
    """Ist ``pid`` eine Claude-Session — nativ (``claude``) oder per npm (``node … claude``)?

    Prozess weg (``FileNotFoundError``/``ProcessLookupError``) heißt „kein Claude“.
    Unlesbar aus anderem Grund zählt als Claude: lieber warten als eine lebende
    Session für beendet halten.
    """
    try:
        name = _proc(pid, "comm").decode(errors="replace").strip()
        argv = [teil.decode(errors="replace") for teil in _proc(pid, "cmdline").split(b"\0")]
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
    return len(teile) >= 2 and Path(teile[0]).name in WRAPPER_NAMEN and teile[1].startswith("-") and "c" in teile[1]


def ist_session_zeile(zeile: str, *, windows: bool = _WINDOWS) -> bool:
    """Ist diese Kommandozeile die Claude-Session selbst?

    Ein Teilstring-Treffer auf „claude" genügt nicht: der direkte Vorfahr des
    Hooks ist ``/bin/sh -c <hook-befehl>``, und dieser Befehl kann „claude" im
    Pfad tragen (``~/.claude/…``). Getroffen wird nur ein Programm, das
    ``claude`` heißt oder im Paket ``claude-code`` liegt. Unter Windows zählen
    Anführungszeichen und die Endung ``.exe`` nicht zum Namen. ``windows`` wählt
    die Zeilenform; Standard ist das laufende System, Windows-Prozesslisten
    (``_prozesse_windows``) übergeben ``True`` unabhängig davon.
    """
    teile = [t.strip('"') for t in zeile.split()] if windows else zeile.split()
    if not teile:
        return False
    if any(marke in zeile for marke in NIE_SESSION):
        return False
    pfad_art = PureWindowsPath if windows else Path
    programm = pfad_art(teile[0]).name
    if windows and programm.lower().endswith(".exe"):
        programm = programm[:-4]
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
    """(kernel32, ntdll) mit Signaturen — eigene Instanzen, ``ctypes.windll`` bleibt unberührt.

    Nur 64-Bit-Python: ``_ProzessInfo`` und die Zeiger-Felder sind für x64 geprüft; ein
    32-Bit-Python (unter WOW64) bekäme ohne diese Prüfung still falsche Listen.
    """
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise OSError(
            "Windows-Prozessliste braucht 64-Bit-Python "
            f"(dieses Python hat {ctypes.sizeof(ctypes.c_void_p) * 8}-Bit-Zeiger)"
        )
    kernel = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
    nt = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
    zeiger, ulong = ctypes.c_void_p, ctypes.c_ulong
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [ulong, ctypes.c_int, ulong], zeiger
    kernel.CloseHandle.argtypes = [zeiger]
    nt.NtQuerySystemInformation.argtypes = [ulong, zeiger, ulong, ctypes.POINTER(ulong)]
    nt.NtQuerySystemInformation.restype = ulong
    nt.NtQueryInformationProcess.argtypes = [zeiger, ulong, zeiger, ulong, ctypes.POINTER(ulong)]
    nt.NtQueryInformationProcess.restype = ulong
    return kernel, nt


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


@functools.cache
def _kernel_mit_fehlercode() -> Any:
    """kernel32 mit ``use_last_error`` (für ``ctypes.get_last_error``) und den cwd-Signaturen."""
    _win32()  # 64-Bit-Prüfung
    zeiger, ulong = ctypes.c_void_p, ctypes.c_ulong
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [ulong, ctypes.c_int, ulong], zeiger
    kernel.CloseHandle.argtypes = [zeiger]
    kernel.GetExitCodeProcess.argtypes = [zeiger, ctypes.POINTER(ulong)]
    kernel.ReadProcessMemory.argtypes = [zeiger, zeiger, zeiger, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    return kernel


def _arbeitsordner_windows(pid: int) -> str | None:
    """Win32-cwd von ``pid``; ``None`` = nicht zu prüfen (weg, beendet, fremd/geschützt —
    wie ``PermissionError`` unter ``/proc``); ``OSError`` = läuft, ist offen, liefert aber nichts.

    ponytail: 32-Bit-Prozesse (WOW64) führen ihre cwd im 32-Bit-PEB, hier steht die vom
    Start; Upgrade: ``ProcessWow64Information`` + PEB32 lesen. Die Umbenennungs-Probe
    deckt native 32-Bit-Prozesse ab.
    """
    kernel, nt = _kernel_mit_fehlercode(), _win32()[1]
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

        def lesen(adresse: int, anzahl: int) -> bytes:
            puffer = ctypes.create_string_buffer(anzahl)
            gelesen = ctypes.c_size_t(0)
            ok = kernel.ReadProcessMemory(griff, adresse, puffer, anzahl, ctypes.byref(gelesen))
            if not ok or gelesen.value != anzahl:
                raise OSError(ctypes.get_last_error(), f"ReadProcessMemory({pid}) gescheitert")
            return puffer.raw

        basis = (ctypes.c_void_p * 6)()  # PROCESS_BASIC_INFORMATION, [1] = PebBaseAddress
        laenge = ctypes.c_ulong(0)
        status = nt.NtQueryInformationProcess(griff, 0, basis, ctypes.sizeof(basis), ctypes.byref(laenge))
        if status or not basis[1]:
            raise OSError(f"NtQueryInformationProcess({pid}): Status 0x{status:08X}")
        parameter = int.from_bytes(lesen(basis[1] + _PEB_PROCESS_PARAMETERS, 8), "little")
        kopf = lesen(parameter + _PARAMS_CURRENT_DIRECTORY, 16)  # UNICODE_STRING DosPath
        bytes_lang, puffer_adresse = int.from_bytes(kopf[:2], "little"), int.from_bytes(kopf[8:], "little")
        if not bytes_lang or not puffer_adresse:
            raise OSError(f"Prozess {pid}: leere cwd im PEB")
        return lesen(puffer_adresse, bytes_lang).decode("utf-16-le")
    finally:
        kernel.CloseHandle(griff)


def arbeitsordner_windows() -> dict[int, str | None]:
    """PID → Win32-cwd aller prüfbaren Prozesse; ``None`` = läuft, aber cwd unlesbar
    (Aufrufer entscheidet fail-closed). Nicht prüfbare (siehe :func:`_arbeitsordner_windows`)
    fehlen. Momentaufnahme unlesbar = ``OSError``."""
    ergebnis: dict[int, str | None] = {}
    for pid in _momentaufnahme_windows():
        if pid in (0, 4):  # Leerlauf- und System-Prozess: kein Benutzer-PEB
            continue
        try:
            cwd = _arbeitsordner_windows(pid)
        except OSError as fehler:
            log.debug("cwd von Prozess %s unlesbar: %s", pid, fehler)
            ergebnis[pid] = None
            continue
        if cwd is not None:
            ergebnis[pid] = cwd
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


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--nachlauf":
        _ziel = Path(sys.argv[5]) if len(sys.argv) >= 6 and sys.argv[4] == "--protokoll" else None
        sys.exit(_nachlauf(int(sys.argv[2]), int(sys.argv[3]), protokoll=_ziel))
    sys.exit(2)
