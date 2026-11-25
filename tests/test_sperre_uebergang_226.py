"""#226: Windows-Sperrstelle beim Skill-Update (Übergang Byte 0 ↔ Versatz 2^31-2).

Seit #221 sperrt ``to_spawn/sperre.py`` unter Windows bei Versatz ``2**31 - 2``, damit
Dateien mit Inhalt (Wartezettel im Leitstand) lesbar bleiben. Während eines Skill-Updates
kann noch ein älterer Prozess laufen, der nur Byte 0 sperrt.

Dieser Test prüft:
1. Plattformunabhängig über eine getreue Bereichs-Sperr-Attrappe (``LK_NBLCK`` + ``LK_UNLCK``,
   mit echtem Dateizeiger aus ``os.lseek``):
   - Alt-Prozess auf Byte 0 sperrt neuen Prozess (leere Datei und Datei mit Inhalt) und
     hinterlässt keine hängende Sperre bei ``2**31 - 2``.
   - Neuer Prozess auf leerer Sperrdatei sperrt Alt-Prozess auf Byte 0 in Gegenrichtung und
     gibt beim Verlassen beide Stellen frei.
   - Neuer Prozess auf Datei mit Inhalt hält nur ``2**31 - 2`` (Byte 0 bleibt nach Prüfung
     frei, Inhalt lesbar).
   - Alte Test-Nähte ohne ``LK_UNLCK`` (wie in ``test_hausmeister_1373_windows.py``) rufen
     ``locking`` weiter genau einmal auf.
   - Fehler beim Sperren von Byte 0 rollen ``2**31 - 2`` sauber zurück.
2. Auf echtem Windows zusätzlich mit zwei getrennten Python-Prozessen und echtem ``msvcrt``.
"""

from __future__ import annotations

import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import prozessbaum, sperre

WURZEL = Path(__file__).resolve().parents[1]


class _BereichsMsvcrt:
    """Getreuer Nachbau von ``msvcrt.locking`` je ``(Datei-Schlüssel, Byte-Versatz)`` und Halter."""

    LK_UNLCK = 0
    LK_NBLCK = 2

    def __init__(self, fehler_bei_versatz: dict[int, OSError] | None = None) -> None:
        self.gesperrt: dict[tuple[tuple[int, int], int], int] = {}
        self.protokoll: list[tuple[int, int, int]] = []
        self._fehler = fehler_bei_versatz or {}

    @staticmethod
    def _datei_schluessel(fd: int) -> tuple[int, int]:
        st = os.fstat(fd)
        return (st.st_dev, st.st_ino)

    def locking(self, fd: int, modus: int, laenge: int) -> None:
        versatz = os.lseek(fd, 0, os.SEEK_CUR)
        self.protokoll.append((modus, versatz, laenge))
        if modus == self.LK_NBLCK and versatz in self._fehler:
            raise self._fehler[versatz]
        schluessel = (self._datei_schluessel(fd), versatz)
        if modus == self.LK_NBLCK:
            halter = self.gesperrt.get(schluessel)
            if halter is not None and halter != fd:
                raise OSError(errno.EACCES, "Permission denied")
            self.gesperrt[schluessel] = fd
        elif modus == self.LK_UNLCK:
            if self.gesperrt.get(schluessel) == fd:
                del self.gesperrt[schluessel]
        else:
            raise ValueError(f"unbekannter Modus: {modus}")

    def alt_sperren(self, datei: Path) -> int:
        """Simuliert einen Alt-Prozess (vor #221): öffnet ``datei`` und sperrt nur Byte 0."""
        fd = os.open(str(datei), os.O_RDONLY | os.O_CREAT, 0o666)
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            self.locking(fd, self.LK_NBLCK, 1)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def alt_freigeben(self, fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            self.locking(fd, self.LK_UNLCK, 1)
        finally:
            os.close(fd)


def test_msvcrt_alt_auf_byte_0_blockiert_neu_leere_datei(tmp_path: Path) -> None:
    datei = tmp_path / "hausmeister.lock"
    ms = _BereichsMsvcrt()
    alt_fd = ms.alt_sperren(datei)
    try:
        with sperre.halte(datei, msvcrt=ms) as gehalten:
            assert gehalten is False
        # Nach dem Fehlschlag darf kein hängender Lock bei 2^31-2 zurückbleiben.
        assert list(ms.gesperrt.keys()) == [(ms._datei_schluessel(alt_fd), 0)]
    finally:
        ms.alt_freigeben(alt_fd)

    with sperre.halte(datei, msvcrt=ms) as gehalten:
        assert gehalten is True
    assert ms.gesperrt == {}


def test_msvcrt_neu_auf_leerer_datei_blockiert_alt_auf_byte_0(tmp_path: Path) -> None:
    datei = tmp_path / "gate.lock"
    ms = _BereichsMsvcrt()
    with sperre.halte(datei, msvcrt=ms) as gehalten:
        assert gehalten is True
        with pytest.raises(OSError) as info:
            ms.alt_sperren(datei)
        assert info.value.errno == errno.EACCES

    assert ms.gesperrt == {}
    alt_fd = ms.alt_sperren(datei)
    ms.alt_freigeben(alt_fd)


def test_msvcrt_datei_mit_inhalt_haelt_byte_0_frei_erkennt_aber_alt(tmp_path: Path) -> None:
    datei = tmp_path / "zettel.json"
    datei.write_text('{"halter": "A"}', encoding="utf-8")
    ms = _BereichsMsvcrt()

    # Alt hält Byte 0 → neuer Lauf sieht „belegt“ und räumt 2^31-2 wieder auf.
    alt_fd = ms.alt_sperren(datei)
    try:
        with sperre.halte(datei, msvcrt=ms) as gehalten:
            assert gehalten is False
        assert list(ms.gesperrt.keys()) == [(ms._datei_schluessel(alt_fd), 0)]
    finally:
        ms.alt_freigeben(alt_fd)

    # Neu hält Datei mit Inhalt → nur 2^31-2 bleibt gesperrt, Byte 0 frei (Inhalt lesbar).
    with sperre.halte(datei, msvcrt=ms) as gehalten:
        assert gehalten is True
        versatze = [versatz for (_, versatz) in ms.gesperrt]
        assert versatze == [sperre._MSVCRT_VERSATZ]
        with sperre.halte(datei, msvcrt=ms) as zweiter:
            assert zweiter is False
    assert ms.gesperrt == {}


def test_msvcrt_fehler_bei_byte_0_gibt_hauptversatz_frei(tmp_path: Path) -> None:
    datei = tmp_path / "kaputt.lock"
    ms = _BereichsMsvcrt(fehler_bei_versatz={0: OSError(errno.ENOLCK, "No locks available")})
    with pytest.raises(OSError) as info, sperre.halte(datei, msvcrt=ms):
        raise AssertionError("darf nicht gehalten werden")
    assert info.value.errno == errno.ENOLCK
    assert ms.gesperrt == {}


def test_msvcrt_alte_testnaht_ohne_lk_unlck_bleibt_einmalig(tmp_path: Path) -> None:
    """Alte Test-Nähte (z. B. ``test_hausmeister_1373_windows.py``) kennen nur ``LK_NBLCK``."""

    class _AltNaht:
        LK_NBLCK = 2

        def __init__(self) -> None:
            self.aufrufe: list[tuple[int, int]] = []

        def locking(self, fd: int, modus: int, laenge: int) -> None:
            self.aufrufe.append((modus, laenge))

    ms = _AltNaht()
    with sperre.halte(tmp_path / "alt.lock", msvcrt=ms) as gehalten:
        assert gehalten is True
    assert ms.aufrufe == [(2, 1)]


# --- Echte Zwei-Prozess-Tests auf Windows (echtes msvcrt) --------------------

_WIN_KIND = r"""
import errno, msvcrt, os, sys
from pathlib import Path
from to_spawn import sperre

art, datei, rolle = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
if art == "alt":
    fd = os.open(str(datei), os.O_RDONLY | os.O_CREAT, 0o666)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            gehalten = True
        except OSError as fehler:
            if fehler.errno in (errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)):
                gehalten = False
            else:
                raise
        print("gehalten" if gehalten else "belegt", flush=True)
        if gehalten and rolle == "halter":
            sys.stdin.readline()
    finally:
        os.close(fd)
else:
    with sperre.halte(datei, warten=0.0) as gehalten:
        print("gehalten" if gehalten else "belegt", flush=True)
        if gehalten and rolle == "halter":
            sys.stdin.readline()
"""


def _win_kind(art: str, datei: Path, rolle: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", _WIN_KIND, art, str(datei), rolle],
        cwd=str(WURZEL),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        **prozessbaum.ohne_fenster(),
    )


def _win_zeile(p: subprocess.Popen[str]) -> str:
    assert p.stdout is not None
    zeile = p.stdout.readline().strip()
    assert zeile, p.stderr.read() if p.stderr else ""
    return zeile


@pytest.mark.skipif(sys.platform != "win32", reason="echter Zwei-Prozess-Test braucht Windows-msvcrt")
def test_windows_zwei_prozesse_alt_und_neu_schliessen_sich_aus(tmp_path: Path) -> None:
    datei = tmp_path / "uebergang.lock"
    # Richtung 1: Alt-Prozess hält Byte 0 → neuer Prozess sieht „belegt“.
    alt = _win_kind("alt", datei, "halter")
    try:
        assert _win_zeile(alt) == "gehalten"
        neu = _win_kind("neu", datei, "pruefer")
        assert _win_zeile(neu) == "belegt"
        assert neu.wait(timeout=30) == 0
    finally:
        alt.communicate("\n", timeout=30)
    assert alt.returncode == 0

    # Richtung 2: Neuer Prozess hält leere Sperrdatei → Alt-Prozess auf Byte 0 sieht „belegt“.
    neu_halter = _win_kind("neu", datei, "halter")
    try:
        assert _win_zeile(neu_halter) == "gehalten"
        alt_pruefer = _win_kind("alt", datei, "pruefer")
        assert _win_zeile(alt_pruefer) == "belegt"
        assert alt_pruefer.wait(timeout=30) == 0
    finally:
        neu_halter.communicate("\n", timeout=30)
    assert neu_halter.returncode == 0

    # Nach Freigabe kommen beide wieder durch.
    danach = _win_kind("alt", datei, "pruefer")
    assert _win_zeile(danach) == "gehalten"
    assert danach.wait(timeout=30) == 0


@pytest.mark.skipif(sys.platform != "win32", reason="echter Zwei-Prozess-Test braucht Windows-msvcrt")
def test_windows_zwei_prozesse_datei_mit_inhalt_bleibt_lesbar_und_erkennt_alt(tmp_path: Path) -> None:
    datei = tmp_path / "zettel.json"
    inhalt = b'{"halter": "A"}'
    datei.write_bytes(inhalt)

    # Alt-Prozess auf Byte 0 wird vom neuen Prozess erkannt.
    alt = _win_kind("alt", datei, "halter")
    try:
        assert _win_zeile(alt) == "gehalten"
        neu = _win_kind("neu", datei, "pruefer")
        assert _win_zeile(neu) == "belegt"
        assert neu.wait(timeout=30) == 0
    finally:
        alt.communicate("\n", timeout=30)
    assert alt.returncode == 0

    # Neuer Prozess hält: Inhalt bleibt lesbar, Datei wächst nicht, zweiter neuer sieht „belegt“.
    neu_halter = _win_kind("neu", datei, "halter")
    try:
        assert _win_zeile(neu_halter) == "gehalten"
        assert datei.read_bytes() == inhalt
        zweiter = _win_kind("neu", datei, "pruefer")
        assert _win_zeile(zweiter) == "belegt"
        assert zweiter.wait(timeout=30) == 0
    finally:
        neu_halter.communicate("\n", timeout=30)
    assert neu_halter.returncode == 0
    assert datei.stat().st_size == len(inhalt)
