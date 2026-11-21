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


@contextmanager
def gebunden(prozess: Any) -> Iterator[None]:
    """Hält ``prozess`` samt allen Nachkommen für die Dauer des Blocks in einem Job (siehe oben)."""
    griff: int | None = None
    if _WINDOWS:
        try:
            griff = _job_fuer(int(prozess.pid))
        except (OSError, AttributeError, TypeError, ValueError, ctypes.ArgumentError) as fehler:
            # Ein Job-Fehler darf den Bau nie abbrechen — die Session läuft dann ohne Job.
            log.warning("Job für Session %s nicht angelegt (%s) — ohne Job", prozess.pid, fehler)
    try:
        yield
    finally:
        if griff is not None:
            _kernel().CloseHandle(griff)
            log.info("Job der Session %d geschlossen — Rest-Baum beendet", prozess.pid)


def _kernel() -> Any:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel


def _job_fuer(pid: int) -> int | None:
    """Neuer Job mit KILL_ON_JOB_CLOSE, ``pid`` aufgenommen; Griff oder ``None`` bei Fehler."""
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

    kernel = _kernel()
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        log.warning("Job für Session %d nicht angelegt (Fehler %d) — ohne Job", pid, ctypes.get_last_error())
        return None
    ok = False
    prozess = None
    try:
        grenzen = _Erweitert()
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
        fehler = ctypes.get_last_error()
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
