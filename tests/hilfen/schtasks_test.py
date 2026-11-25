"""Windows-Test-Helfer für ``schtasks``: Präfix, Aufräumen und Waisen-Prüfung (#789).

Einzige Stelle des Wissens, wie Test-Aufgaben in der Windows-Aufgabenplanung heißen,
erkannt, gezählt und gelöscht werden:

- Alle neuen Test-Aufgaben tragen das Präfix :data:`PRAEFIX` samt Prozess-PID
  (``to-spawn-test-p<pid>-<zweck> <hex>``). So lassen sich Aufgaben noch laufender
  paralleler Test-Prozesse (z. B. ``pytest -n auto``) von verwaisten Resten toter
  Prozesse unterscheiden.
- Das frühere Präfix :data:`ALT_PRAEFIX` (``to-spawn Hausmeister Test ``, z. B.
  ``to-spawn Hausmeister Test 71ce15``) wird ebenfalls als Test-Aufgabe erkannt und
  als Alt-Rest abgeräumt.
- Die echte Produktiv-Aufgabe ``to-spawn Hausmeister`` (und fremde System-Aufgaben)
  werden nie als Test-Aufgabe erkannt und sind durch eine Schutz-Sperre in
  :func:`loeschen` ausdrücklich gegen Löschen gesichert.
- Unter Linux/macOS sind alle Lese- und Aufräum-Funktionen wirkungslos (kein einziger
  Unterprozess-Aufruf).
"""

from __future__ import annotations

import atexit
import csv
import ctypes
import io
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from to_spawn import hausmeister_aufgabe, konsole, prozessbaum

log = logging.getLogger(__name__)

#: Eindeutiges Test-Präfix für neue Windows-Test-Aufgaben (#789).
PRAEFIX = "to-spawn-test-"
#: Bisheriges Test-Präfix aus älteren Testläufen (z. B. „to-spawn Hausmeister Test 71ce15“).
ALT_PRAEFIX = "to-spawn Hausmeister Test "
#: Alle Präfixe, die eindeutig als Test-Aufgabe gelten — nie „to-spawn Hausmeister“.
ALLE_PRAEFIXE: tuple[str, ...] = (PRAEFIX, ALT_PRAEFIX)

_PID_MUSTER = re.compile(r"^to-spawn-test-p(\d+)(?:[- ]|$)")
_ZWECK_MUSTER = re.compile(r"[^A-Za-z0-9_-]+")

#: Von diesem Prozess registrierte Test-Aufgaben, die spätestens bei Exit/Abbruch weg müssen.
_AKTIVE_AUFGABEN: set[str] = set()

#: Windows-Kernel-Mutex für genau einen aktiven ``schtasks``-Wächter je Anmelde-Sitzung (#789).
WAECHTER_MUTEX_NAME = "Local\\to-spawn-schtasks-waechter"
#: Win32-Fehlercode ``ERROR_ALREADY_EXISTS`` von ``CreateMutexW``.
_FEHLER_EXISTIERT_BEREITS = 183


class WaechterSperre:
    """Hält die exklusive Windows-Wächter-Sperre bis ``pytest_sessionfinish`` (#789)."""

    def __init__(self, freigeben_fn: Callable[[], None]) -> None:
        self._freigeben_fn: Callable[[], None] | None = freigeben_fn

    def freigeben(self) -> None:
        """Gibt die Sperre idempotent frei."""
        fn = self._freigeben_fn
        if fn is not None:
            self._freigeben_fn = None
            fn()


def _win_mutex_nehmen(name: str) -> WaechterSperre | None:
    """Reserviert unter Windows exklusiv einen benannten Kernel-Mutex; bereits belegt → ``None``.

    ``lpMutexAttributes=None`` verhindert Handle-Vererbung an Kindprozesse; bei hartem Abbruch
    des Besitzers gibt der Windows-Kernel das Handle automatisch frei (keine Lock-Reste).
    """
    if sys.platform != "win32":
        return WaechterSperre(lambda: None)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    ctypes.set_last_error(0)
    griff = kernel.CreateMutexW(None, False, name)
    if not griff:
        log.warning("Windows-Wächter-Mutex %r nicht anlegbar (Fehler %d)", name, ctypes.get_last_error())
        return None
    if ctypes.get_last_error() == _FEHLER_EXISTIERT_BEREITS:
        kernel.CloseHandle(griff)
        return None
    schliessen = kernel.CloseHandle

    def _freigeben() -> None:
        schliessen(griff)

    return WaechterSperre(_freigeben)


def waechter_nehmen(name: str = WAECHTER_MUTEX_NAME) -> WaechterSperre | None:
    """Übernimmt den maschinenweiten Windows-``schtasks``-Wächter exklusiv; schon aktiv → ``None``.

    Schützt parallele xdist-Worker vor verschachtelten Kind-pytest-Läufen (z. B. ``test_pt_reste_177``,
    ``test_testheim_232``) und vor parallelen zweiten Testläufen auf demselben Windows-Rechner (#789).
    """
    if not ist_windows():
        return None
    return _win_mutex_nehmen(name)


def ist_windows() -> bool:
    """``True`` nur unter Windows (einzige Plattform-Naht für Tests)."""
    return sys.platform == "win32"


def normalisiere_name(name: str) -> str:
    """Entfernt Rand-Leerzeichen und genau einen führenden Backslash aus ``schtasks``-Ausgaben."""
    return name.strip().removeprefix("\\")


def ist_test_aufgabe(name: str) -> bool:
    """Prüft, ob ``name`` eine Test-Aufgabe ist (nie ``to-spawn Hausmeister`` oder Unterordner)."""
    sauber = normalisiere_name(name)
    return any(sauber.startswith(praefix) for praefix in ALLE_PRAEFIXE)


def aufgabe_pid(name: str) -> int | None:
    """Liest die eingebettete Prozess-PID aus einem neuen Test-Aufgaben-Namen; sonst ``None``."""
    treffer = _PID_MUSTER.match(normalisiere_name(name))
    if not treffer:
        return None
    pid = int(treffer.group(1))
    return pid if 0 < pid < 2**31 else None


def neuer_name(zweck: str = "probe", pid: int | None = None) -> str:
    """Erzeugt einen eindeutigen Test-Aufgaben-Namen mit Präfix, PID und Leerzeichen.

    Das Leerzeichen vor dem Zufalls-Teil prüft das Windows-Quoting weiter genau wie bei
    der echten Aufgabe ``to-spawn Hausmeister``.
    """
    ziel_pid = os.getpid() if pid is None else pid
    sauber_zweck = _ZWECK_MUSTER.sub("-", zweck.strip()).strip("-") or "probe"
    return f"{PRAEFIX}p{ziel_pid}-{sauber_zweck} {uuid.uuid4().hex[:8]}"


def csv_test_aufgaben(ausgabe: str) -> list[str]:
    """Extrahiert alle eindeutigen Test-Aufgaben-Namen aus ``schtasks /Query /FO CSV /NH``."""
    gefunden: set[str] = set()
    for zeile in csv.reader(io.StringIO(ausgabe)):
        if not zeile:
            continue
        sauber = normalisiere_name(zeile[0])
        if ist_test_aufgabe(sauber):
            gefunden.add(sauber)
    return sorted(gefunden)


def _lauf(*args: str) -> subprocess.CompletedProcess[str] | None:
    """Führt ``schtasks`` fensterlos mit Zeitlimit und UTF-8-Textlesart aus; Fehler → ``None``."""
    try:
        return subprocess.run(
            ["schtasks", *args],
            capture_output=True,
            **konsole.UNTERPROZESS_TEXT,
            check=False,
            timeout=hausmeister_aufgabe.SCHTASKS_ZEITLIMIT_S,
            **prozessbaum.fensterlos(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as fehler:
        log.warning("schtasks %s fehlgeschlagen: %s", args[:1], fehler)
        return None


def existiert(name: str) -> bool:
    """Prüft unter Windows, ob die Aufgabe ``name`` in der Aufgabenplanung vorhanden ist."""
    if not ist_windows():
        return False
    erg = _lauf("/Query", "/TN", normalisiere_name(name))
    return erg is not None and erg.returncode == 0


def offene_test_aufgaben() -> list[str]:
    """Listet alle derzeit vorhandenen Test-Aufgaben (auch deaktivierte); Linux → ``[]``."""
    if not ist_windows():
        return []
    erg = _lauf("/Query", "/FO", "CSV", "/NH")
    if erg is None:
        return []
    aufgaben = csv_test_aufgaben(erg.stdout)
    if erg.returncode != 0 and not aufgaben:
        log.debug("schtasks /Query lieferte Exit %d: %s", erg.returncode, (erg.stderr or erg.stdout).strip()[:200])
    return aufgaben


def registrieren(name: str) -> str:
    """Merkt ``name`` als aktive Test-Aufgabe dieses Prozesses für Aufräumen bei Abbruch/Exit."""
    sauber = normalisiere_name(name)
    if not ist_test_aufgabe(sauber):
        raise ValueError(f"Nur eindeutige Test-Aufgaben dürfen registriert werden, nicht {name!r}")
    _AKTIVE_AUFGABEN.add(sauber)
    return sauber


def loeschen(name: str) -> bool:
    """Löscht eine Test-Aufgabe unter Windows und prüft, dass sie weg ist.

    Verweigert jede Nicht-Test-Aufgabe mit ``ValueError``, damit ``to-spawn Hausmeister``
    oder System-Aufgaben nie versehentlich gelöscht werden.
    """
    sauber = normalisiere_name(name)
    if not ist_test_aufgabe(sauber):
        raise ValueError(f"Nur eindeutige Test-Aufgaben dürfen gelöscht werden, nicht {name!r}")
    if not ist_windows():
        _AKTIVE_AUFGABEN.discard(sauber)
        return True
    erg = _lauf("/Delete", "/TN", sauber, "/F")
    if erg is not None and erg.returncode == 0:
        _AKTIVE_AUFGABEN.discard(sauber)
        return True
    if not existiert(sauber):
        _AKTIVE_AUFGABEN.discard(sauber)
        return True
    meldung = (erg.stderr or erg.stdout).strip()[:200] if erg is not None else "schtasks nicht ausführbar"
    log.warning("schtasks-Test-Aufgabe %r konnte nicht gelöscht werden: %s", sauber, meldung)
    return False


def eigene_raeumen() -> list[str]:
    """Löscht alle von diesem Prozess registrierten Test-Aufgaben; gibt unlöschbare zurück."""
    if not _AKTIVE_AUFGABEN:
        return []
    unloeschbar: list[str] = []
    for name in sorted(_AKTIVE_AUFGABEN):
        if not loeschen(name):
            unloeschbar.append(name)
    return unloeschbar


def ist_verwaist(
    name: str,
    *,
    eigener_pid: int | None = None,
    lebt_fn: Callable[[int], bool] | None = None,
) -> bool:
    """``True``, wenn ``name`` eine eigene, eine Alt-Präfix- oder eine Tote-PID-Test-Aufgabe ist."""
    if not ist_test_aufgabe(name):
        return False
    pid = aufgabe_pid(name)
    if pid is None:
        return True  # Alt-Präfix ohne PID (z. B. „to-spawn Hausmeister Test 71ce15“)
    ich = os.getpid() if eigener_pid is None else eigener_pid
    if pid == ich:
        return True
    pruefe_lebt = prozessbaum.lebt if lebt_fn is None else lebt_fn
    return not pruefe_lebt(pid)


def verwaiste_aufgaben(
    *,
    eigener_pid: int | None = None,
    lebt_fn: Callable[[int], bool] | None = None,
) -> list[str]:
    """Offene Test-Aufgaben ohne anderen noch laufenden Besitzer-Prozess."""
    return [name for name in offene_test_aufgaben() if ist_verwaist(name, eigener_pid=eigener_pid, lebt_fn=lebt_fn)]


def verwaiste_raeumen(
    *,
    eigener_pid: int | None = None,
    lebt_fn: Callable[[int], bool] | None = None,
) -> list[str]:
    """Löscht alle verwaisten Test-Aufgaben unter Windows; gibt die geräumten Namen zurück."""
    if not ist_windows():
        return []
    geraeumt: list[str] = []
    for name in verwaiste_aufgaben(eigener_pid=eigener_pid, lebt_fn=lebt_fn):
        if loeschen(name):
            log.info("Verwaiste schtasks-Test-Aufgabe geräumt: %s", name)
            geraeumt.append(name)
    return geraeumt


@contextmanager
def abbruch_schutz() -> Iterator[None]:
    """Räumt aktive Test-Aufgaben bei ``SIGTERM``/``SIGBREAK``, bevor der Prozess endet (#789)."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    signale = [s for s in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGBREAK", None)) if s is not None]
    vorher: dict[signal.Signals, Any] = {}

    def _bei_abbruch(signum: int, frame: Any) -> None:
        eigene_raeumen()
        sig = signal.Signals(signum)
        alt = vorher.get(sig)
        if alt is not None:
            try:
                signal.signal(sig, alt)
            except (OSError, ValueError):
                pass
        if callable(alt):
            alt(signum, frame)
            return
        if alt == signal.SIG_IGN:
            return
        raise SystemExit(128 + signum)

    for sig in signale:
        try:
            vorher[sig] = signal.getsignal(sig)
            signal.signal(sig, _bei_abbruch)
        except (OSError, ValueError):
            vorher.pop(sig, None)
    try:
        yield
    finally:
        for sig, alt in vorher.items():
            try:
                signal.signal(sig, alt)
            except (OSError, ValueError):
                pass


@contextmanager
def aufgaben_raum() -> Iterator[Callable[..., str]]:
    """Kontext für Tests: vergibt Test-Aufgaben-Namen und löscht alle im ``finally`` (#789)."""
    angelegt: list[str] = []

    def fabrik(zweck: str = "probe") -> str:
        name = registrieren(neuer_name(zweck))
        angelegt.append(name)
        return name

    with abbruch_schutz():
        try:
            yield fabrik
        finally:
            unloeschbar: list[str] = []
            for name in reversed(angelegt):
                if not loeschen(name):
                    unloeschbar.append(name)
            if unloeschbar:
                raise AssertionError(f"schtasks-Test-Aufgaben blieben nach Teardown stehen (#789): {unloeschbar}")


atexit.register(eigene_raeumen)
