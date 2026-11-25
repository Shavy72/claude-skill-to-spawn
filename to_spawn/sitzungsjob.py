"""Session-Baum an ein Windows-Job-Objekt binden — Ende des Laufs beendet den ganzen Baum (#122).

Für Aufrufer: ``with sitzungsjob.gebunden(prozess): prozess.wait()``.

Warum: unter Windows erwischt ``taskkill /T`` nur echte Windows-Kinder. Git Bash startet
seine Kinder (``sh -c "sleep 37 & wait"``) so, dass sie nicht als Kind der Shell gelten —
sie überlebten das Beenden einer Bau-Session. Ein Job-Objekt erbt sich dagegen in jeden
Prozess, den ein Mitglied startet, auch über Git Bash. Mit ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``
beendet das Schließen des letzten Griffs alle noch lebenden Mitglieder.

- Windows: ``gebunden`` legt ein Job-Objekt an, nimmt ``prozess`` auf und schließt den Griff
  beim Verlassen des Blocks. Stirbt der Halter selbst (Tab zu, ``taskkill`` auf ``bau.py``),
  schließt Windows den Griff — der Baum stirbt mit. Klappt das Anlegen nicht, läuft die
  Session ohne Job weiter (Warnung im Log), sie wird nie deswegen abgebrochen.
- Linux/macOS: tut nichts, der bisherige Weg bleibt unverändert.

Grenzen: Der Job erlaubt bewusst kein Ausbrechen (``JOB_OBJECT_LIMIT_BREAKAWAY_OK`` aus) —
gemessen: mit erlaubtem Ausbrechen verlässt Git Bash den Job von selbst und ``sleep`` überlebt.
Darum stirbt beim Schließen auch alles, was die Session absichtlich abgelöst startete (z. B.
der Nachläufer des Stop-Hooks, der dann evtl. seine ``OK``-Zeile nicht mehr schreibt).
"""

# ponytail: Aufnahme direkt nach dem Start — was ``prozess`` in diesen Mikrosekunden startet,
# liegt außerhalb. Upgrade: Start mit CREATE_SUSPENDED, aufnehmen, dann fortsetzen.

from __future__ import annotations

import ctypes
import functools
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger("to_spawn.sitzungsjob")

_WINDOWS = sys.platform == "win32"
_JOB_ERWEITERTE_GRENZEN = 9  # JobObjectExtendedLimitInformation
_KILL_ON_JOB_CLOSE = 0x2000
_PROZESS_RECHTE = 0x0100 | 0x0001  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _letzter_fehler() -> int:
    """``ctypes.get_last_error()`` unter Windows; unter Linux (Test-Attrappe) ``0``."""
    return int(ctypes.get_last_error()) if hasattr(ctypes, "get_last_error") else 0


def _fehler_zuruecksetzen() -> None:
    if hasattr(ctypes, "set_last_error"):
        ctypes.set_last_error(0)


class Job:
    """Offener Windows-Job-Griff (``None`` unter Linux/macOS oder bei Job-Fehler).

    ``schliessen()`` schließt den Griff (``KILL_ON_JOB_CLOSE`` beendet Rest-Mitglieder),
    ``beenden()`` bricht alle Mitglieder sofort über ``TerminateJobObject`` ab und schließt
    den Griff. Beide sind mehrfach sicher aufrufbar.
    """

    def __init__(self, pid: int, griff: int | None) -> None:
        self._pid = pid
        self._griff = griff

    @property
    def aktiv(self) -> bool:
        """``True``, solange ein offener Job-Griff gehalten wird."""
        return self._griff is not None

    def aufnehmen(self, pid: int) -> bool:
        """Nimmt einen bereits gestarteten Nachfahren ``pid`` in den offenen Job auf."""
        if self._griff is None:
            return False
        kernel = _kernel()
        prozess = kernel.OpenProcess(_PROZESS_RECHTE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not prozess:
            return False
        try:
            drin = ctypes.c_int(0)
            if kernel.IsProcessInJob(prozess, self._griff, ctypes.byref(drin)) and drin.value:
                return True
            return bool(kernel.AssignProcessToJobObject(self._griff, prozess))
        finally:
            kernel.CloseHandle(prozess)

    def beenden(self) -> bool:
        """Beendet alle Prozesse im Job sofort (``TerminateJobObject``) und schließt den Griff."""
        if self._griff is None:
            return False
        griff, self._griff = self._griff, None
        kernel = _kernel()
        ok = False
        try:
            _fehler_zuruecksetzen()
            ok = bool(kernel.TerminateJobObject(griff, 1))
            if not ok:
                log.warning(
                    "TerminateJobObject für Session %d fehlgeschlagen (Fehler %d)",
                    self._pid,
                    _letzter_fehler(),
                )
        finally:
            kernel.CloseHandle(griff)
        log.info("Job der Session %d beendet und geschlossen — Rest-Baum beendet", self._pid)
        return ok

    def schliessen(self) -> None:
        """Schließt den Job-Griff (``KILL_ON_JOB_CLOSE`` räumt Rest-Baum ab)."""
        if self._griff is None:
            return
        griff, self._griff = self._griff, None
        _kernel().CloseHandle(griff)
        log.info("Job der Session %d geschlossen — Rest-Baum beendet", self._pid)


@contextmanager
def gebunden(prozess: Any) -> Iterator[Job]:
    """Hält ``prozess`` samt allen Nachkommen für die Dauer des Blocks in einem Job (siehe oben)."""
    griff: int | None = None
    pid = 0
    if _WINDOWS:
        bereits_beendet = callable(getattr(prozess, "poll", None)) and prozess.poll() is not None
        if not bereits_beendet:
            try:
                pid = int(prozess.pid)
                griff = _job_fuer(pid)
            except (OSError, AttributeError, TypeError, ValueError, ctypes.ArgumentError) as fehler:
                # Ein Job-Fehler darf den Bau nie abbrechen — die Session läuft dann ohne Job.
                log.warning("Job für Session %s nicht angelegt (%s) — ohne Job", getattr(prozess, "pid", "?"), fehler)
    if pid == 0 and hasattr(prozess, "pid"):
        try:
            pid = int(prozess.pid)
        except (TypeError, ValueError):
            pid = 0
    job = Job(pid, griff)
    try:
        yield job
    finally:
        job.schliessen()


@functools.cache
def _kernel() -> Any:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


@functools.cache
def _job_grenzen_typ() -> type[ctypes.Structure]:
    """``JOBOBJECT_EXTENDED_LIMIT_INFORMATION`` — einmal gebaut, statt je Start neu."""
    from ctypes import wintypes

    class _Basis(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _Erweitert(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _Basis),
            ("IoInfo", ctypes.c_uint64 * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    return _Erweitert


def _job_fuer(pid: int) -> int | None:
    """Neuer Job mit KILL_ON_JOB_CLOSE, ``pid`` aufgenommen; Griff oder ``None`` bei Fehler."""
    erweitert_typ = _job_grenzen_typ()
    kernel = _kernel()
    _fehler_zuruecksetzen()
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        log.warning("Job für Session %d nicht angelegt (Fehler %d) — ohne Job", pid, _letzter_fehler())
        return None
    ok = False
    prozess = None
    try:
        grenzen = erweitert_typ()
        grenzen.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        prozess = kernel.OpenProcess(_PROZESS_RECHTE, False, pid)
        ok = (
            bool(prozess)
            and bool(
                kernel.SetInformationJobObject(
                    job, _JOB_ERWEITERTE_GRENZEN, ctypes.byref(grenzen), ctypes.sizeof(grenzen)
                )
            )
            and bool(kernel.AssignProcessToJobObject(job, prozess))
        )
        fehler = _letzter_fehler()
    finally:
        # Griffe auch bei Ausnahme schließen; der Job-Griff bleibt nur bei Erfolg offen.
        if prozess:
            kernel.CloseHandle(prozess)
        if not ok:
            kernel.CloseHandle(job)
    if not ok:
        log.warning("Session %d nicht in Job aufgenommen (Fehler %d) — ohne Job", pid, fehler)
        return None
    log.info("Session %d in Job aufgenommen (KILL_ON_JOB_CLOSE)", pid)
    return int(job)
