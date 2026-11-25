"""Datei-Sperre gegen parallele Läufe (#94, L1 E2-10) — die einzige Stelle mit fcntl/msvcrt.

Eine Tür: ``with sperre.halte(datei, warten=…) as gehalten:``. ``gehalten`` ist ``True``,
solange dieser Lauf die Sperre hält, ``False``, wenn ein anderer Lauf sie auch nach
``warten`` Sekunden noch hält. Verlassen des Blocks gibt sie frei.

Versteckt wird hier, welches Werkzeug sperrt:
- Linux/macOS: ``fcntl.flock`` auf ``datei`` (verträgt sich mit ``flock`` aus der Shell).
- Windows: ``msvcrt.locking`` auf 1 Byte weit hinter dem Inhalt von ``datei`` (der
  Inhalt bleibt für andere Prozesse lesbar, z. B. Wartezettel im Leitstand).
  Übergangsschutz beim Skill-Update (#226): Byte 0 (alte Sperrstelle vor #221) wird
  mitgeprüft und bei leerer Sperrdatei mitgehalten, bei Datei mit Inhalt sofort per
  ``LK_UNLCK`` wieder freigegeben.
- Ohne beide: Rückfall auf eine O_EXCL-Datei ``<datei>.excl`` neben ``datei``.

Zwei Fehlerarten, getrennt nur hier (#165):
- Ordner/Sperr-Datei nicht anleg- oder öffenbar (Rechte, Ordner statt Datei) ⇒
  :class:`SperreNichtOeffenbar` — nie „belegt“, der Aufrufer meldet es laut.
- Der Sperr-Versuch selbst scheitert anders als „belegt“ (z. B. ENOLCK) ⇒ ``OSError``;
  der Aufrufer entscheidet, ob er das wie „belegt“ behandelt.
``SperreNichtOeffenbar`` erbt von ``OSError``: wer nur ``OSError`` fängt, bleibt heil.

Test-Nähte: :data:`_fcntl`, :data:`_msvcrt` (auf ``None`` setzen erzwingt den nächsten Zweig).
"""

from __future__ import annotations

import contextlib
import errno
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl_modul
except ImportError:  # Windows
    _fcntl_modul = None  # type: ignore[assignment]
try:
    import msvcrt as _msvcrt_modul
except ImportError:  # nicht Windows
    _msvcrt_modul = None  # type: ignore[assignment]

_fcntl: Any = _fcntl_modul
_msvcrt: Any = _msvcrt_modul
#: Abstand zwischen zwei Versuchen, solange ``warten`` läuft.
TAKT_S = 0.2
# msvcrt.locking meldet einen fremd gehaltenen Bereich je nach CRT mit EACCES oder EDEADLOCK.
_BELEGT = {errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)}
# Windows sperrt Bytes verbindlich (auch fürs Lesen); darum hinter jedem echten Inhalt.
# Sperren jenseits des Dateiendes erlaubt Windows, die Datei wächst dadurch nicht.
_MSVCRT_VERSATZ = 2**31 - 2
# Alte Sperrstelle vor #221 (Übergangsschutz beim Skill-Update, #226):
# - Leere Sperrdatei (st_size == 0): beide Stellen halten ⇒ alter und neuer Prozess schließen
#   sich in beiden Richtungen aus.
# - Datei mit Inhalt (z. B. Wartezettel im Leitstand): Byte 0 kurz prüfen und sofort wieder
#   freigeben, damit der Inhalt lesbar bleibt, ein noch laufender Alt-Prozess auf Byte 0 aber
#   trotzdem als „belegt“ erkannt wird.
_MSVCRT_VERSATZ_ALT = 0


class SperreNichtOeffenbar(OSError):
    """Ordner oder Sperr-Datei ließ sich nicht anlegen/öffnen — ein Fehler, nicht „belegt“.

    ``str()`` nennt den Pfad so, wie ein Mensch ihn eintippt. ``OSError`` selbst zeigt
    ``repr(filename)`` — unter Windows mit verdoppelten Backslashes, nicht suchbar (#68).
    """

    def __str__(self) -> str:
        return f"[Errno {self.errno}] {self.strerror}: {self.filename}"


def _oeffnen_gescheitert(datei: Path, fehler: OSError) -> SperreNichtOeffenbar:
    return SperreNichtOeffenbar(fehler.errno, f"Sperr-Datei nicht öffenbar: {fehler.strerror}", str(datei))


def flock_vorhanden() -> bool:
    """``True``, wenn die Sperre mit Shell-``flock`` kompatibel ist (fcntl vorhanden)."""
    return _fcntl is not None


@contextlib.contextmanager
def halte(datei: Path, warten: float = 0.0, *, msvcrt: Any = None) -> Iterator[bool]:
    """Sperre ``datei`` (legt sie samt Ordner an); liefert ``False``, wenn belegt.

    ``warten``: so lange (Sekunden) erneut versuchen, ``0`` = genau ein Versuch.
    ``msvcrt``: nur Test-Naht — erzwingt den msvcrt-Zweig mit diesem Modul.
    Wirft :class:`SperreNichtOeffenbar`, wenn Ordner oder Datei nicht anleg-/öffenbar sind.
    """
    datei = Path(datei)
    try:
        datei.parent.mkdir(parents=True, exist_ok=True)
    except OSError as fehler:
        raise _oeffnen_gescheitert(datei.parent, fehler) from fehler
    fc = None if msvcrt is not None else _fcntl
    ms = msvcrt if msvcrt is not None else _msvcrt
    ende = time.monotonic() + max(warten, 0.0)
    if fc is None and ms is None:
        with _excl(datei, ende) as gehalten:
            yield gehalten
        return
    # Nur lesend öffnen: flock/msvcrt brauchen kein Schreibrecht, so geht auch eine fremd
    # angelegte Sperr-Datei (z. B. von staging_deploy.sh). O_CREAT legt sie bei Bedarf an.
    try:
        fd = os.open(str(datei), os.O_RDONLY | os.O_CREAT, 0o666)
    except OSError as fehler:
        raise _oeffnen_gescheitert(datei, fehler) from fehler
    gehalten_versatze: list[int] = []
    try:
        while True:
            versatze = _versuch(fd, fc, ms)
            if versatze is not None:
                gehalten_versatze = versatze
                break
            if time.monotonic() >= ende:
                yield False
                return
            time.sleep(TAKT_S)
        try:
            yield True
        finally:
            if fc is not None:
                with contextlib.suppress(OSError):
                    fc.flock(fd, fc.LOCK_UN)
            elif ms is not None:
                for versatz in reversed(gehalten_versatze):
                    _msvcrt_freigeben(fd, ms, versatz)
    finally:
        os.close(fd)


def _msvcrt_sperren(fd: int, ms: Any, versatz: int) -> bool:
    """Sperrt 1 Byte bei ``versatz`` nicht blockierend; ``False`` = belegt."""
    os.lseek(fd, versatz, os.SEEK_SET)
    try:
        ms.locking(fd, ms.LK_NBLCK, 1)
    except OSError as fehler:
        if fehler.errno in _BELEGT:
            return False
        raise
    return True


def _msvcrt_freigeben(fd: int, ms: Any, versatz: int) -> None:
    """Gibt 1 Byte bei ``versatz`` frei, falls ``ms`` ``LK_UNLCK`` kennt (alte Test-Naht nicht)."""
    lk_unlck = getattr(ms, "LK_UNLCK", None)
    if lk_unlck is None:
        return
    with contextlib.suppress(OSError):
        os.lseek(fd, versatz, os.SEEK_SET)
        ms.locking(fd, lk_unlck, 1)


def _versuch(fd: int, fc: Any, ms: Any) -> list[int] | None:
    """Ein nicht blockierender Versuch: gehaltene msvcrt-Versätze (Linux: ``[]``), ``None`` = belegt."""
    if fc is not None:
        try:
            fc.flock(fd, fc.LOCK_EX | fc.LOCK_NB)
        except BlockingIOError:
            return None
        return []
    if not _msvcrt_sperren(fd, ms, _MSVCRT_VERSATZ):
        return None
    if getattr(ms, "LK_UNLCK", None) is None:
        # Alte Test-Nähte kennen kein LK_UNLCK — ohne Freigabe kein Zweit-Versuch.
        return [_MSVCRT_VERSATZ]
    try:
        alt_frei = _msvcrt_sperren(fd, ms, _MSVCRT_VERSATZ_ALT)
    except BaseException:
        _msvcrt_freigeben(fd, ms, _MSVCRT_VERSATZ)
        raise
    if not alt_frei:
        _msvcrt_freigeben(fd, ms, _MSVCRT_VERSATZ)
        return None
    try:
        hat_inhalt = os.fstat(fd).st_size > 0
    except BaseException:
        _msvcrt_freigeben(fd, ms, _MSVCRT_VERSATZ_ALT)
        _msvcrt_freigeben(fd, ms, _MSVCRT_VERSATZ)
        raise
    if hat_inhalt:
        _msvcrt_freigeben(fd, ms, _MSVCRT_VERSATZ_ALT)
        return [_MSVCRT_VERSATZ]
    return [_MSVCRT_VERSATZ, _MSVCRT_VERSATZ_ALT]


@contextlib.contextmanager
def _excl(datei: Path, ende: float) -> Iterator[bool]:
    """Rückfall ohne Sperr-Werkzeug: wer ``<datei>.excl`` anlegt, hält die Sperre.

    ponytail: ein hart beendeter Lauf lässt die Datei liegen und sperrt dauerhaft;
    Upgrade: PID hineinschreiben und tote Halter wegräumen, falls je eine Plattform
    ohne fcntl und msvcrt gebraucht wird.
    """
    exkl = datei.with_name(datei.name + ".excl")
    while True:
        try:
            os.close(os.open(str(exkl), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            if exkl.is_dir():  # ein Ordner wird nie frei — kein „belegt“
                raise _oeffnen_gescheitert(exkl, OSError(errno.EISDIR, os.strerror(errno.EISDIR))) from None
            if time.monotonic() >= ende:
                yield False
                return
            time.sleep(TAKT_S)
        except OSError as fehler:
            raise _oeffnen_gescheitert(exkl, fehler) from fehler
    try:
        yield True
    finally:
        with contextlib.suppress(OSError):
            exkl.unlink()
