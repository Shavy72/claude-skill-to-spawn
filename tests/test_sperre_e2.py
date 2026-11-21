"""Sperr-Modul (#94, L1 E2-10): eine Tür ``sperre.halte`` für fcntl, msvcrt und O_EXCL.

Prüf-Test: fcntl/msvcrt gibt es im Skill-Code nur in ``to_spawn/sperre.py`` (plus die
Ausnahmen aus dem Issue). Weg-Test: zwei echte Prozesse auf dieselbe Sperre — je
Zweig (Standard, erzwungenes msvcrt, O_EXCL-Rückfall).
"""

from __future__ import annotations

import ast
import subprocess
import sys
import time
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]
PLATTFORM_SPERREN = frozenset({"fcntl", "msvcrt"})
#: Eigene Sperren mit eigenem Zuschnitt, Umbau in Teil b (Issue #94).
AUSNAHMEN = frozenset({"sperre", "capo", "leitstand", "vertrauen", "leitstand_fenster"})


def _funde(quelle: str) -> list[tuple[int, str]]:
    funde: list[tuple[int, str]] = []
    for knoten in ast.walk(ast.parse(quelle)):
        if isinstance(knoten, ast.Import):
            funde += [(knoten.lineno, a.name) for a in knoten.names if a.name.split(".")[0] in PLATTFORM_SPERREN]
        elif isinstance(knoten, ast.ImportFrom) and knoten.module:
            if knoten.module.split(".")[0] in PLATTFORM_SPERREN:
                funde.append((knoten.lineno, knoten.module))
        elif isinstance(knoten, ast.Attribute) and knoten.attr in {"flock", "locking"}:
            funde.append((knoten.lineno, f".{knoten.attr}"))
    return funde


def test_selbstcheck_findet_import_und_aufruf() -> None:
    quelle = "import fcntl\nfrom msvcrt import locking\nx.flock(f, 1)\nimport os\n"
    assert _funde(quelle) == [(1, "fcntl"), (2, "msvcrt"), (3, ".flock")]


def test_fcntl_msvcrt_nur_in_sperre() -> None:
    funde = [
        f"{datei.relative_to(WURZEL).as_posix()}:{zeile} {was}"
        for datei in sorted((WURZEL / "to_spawn").rglob("*.py"))
        if datei.stem not in AUSNAHMEN
        for zeile, was in _funde(datei.read_text(encoding="utf-8"))
    ]
    assert not funde, "Sperren nur über to_spawn.sperre.halte:\n" + "\n".join(funde)


# --- Zwei Prozesse ---------------------------------------------------------------

_KIND = r"""
import errno, sys, time
from pathlib import Path
from to_spawn import sperre

zweig, datei, rolle, warten = sys.argv[1], Path(sys.argv[2]), sys.argv[3], float(sys.argv[4])
if zweig == "msvcrt":
    import fcntl

    class _Msvcrt:
        # Nachbau von msvcrt.locking: Byte-Bereich-Sperre, belegt = EACCES.
        LK_NBLCK = 2

        def locking(self, fd, modus, laenge):
            try:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, laenge, 0)
            except OSError:
                raise OSError(errno.EACCES, "Permission denied") from None

    sperre._fcntl, sperre._msvcrt = None, _Msvcrt()
elif zweig == "excl":
    sperre._fcntl, sperre._msvcrt = None, None

print("bereit", 0, flush=True)
start = time.monotonic()
with sperre.halte(datei, warten=warten) as gehalten:
    print("gehalten" if gehalten else "belegt", round(time.monotonic() - start, 2), flush=True)
    if rolle == "halter":
        sys.stdin.readline()
"""

ZWEIGE = ["standard", "msvcrt", "excl"]


def _kind(zweig: str, datei: Path, rolle: str, warten: float) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", _KIND, zweig, str(datei), rolle, str(warten)],
        cwd=str(WURZEL),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def _antwort(p: subprocess.Popen[str]) -> tuple[str, float]:
    assert p.stdout is not None
    zeile = p.stdout.readline().split()
    if zeile and zeile[0] == "bereit":
        zeile = p.stdout.readline().split()
    assert zeile, p.stderr.read() if p.stderr else ""
    return zeile[0], float(zeile[1])


def _zweig_moeglich(zweig: str) -> None:
    if zweig == "msvcrt":
        pytest.importorskip("fcntl", reason="msvcrt-Nachbau braucht fcntl.lockf")


@pytest.mark.parametrize("zweig", ZWEIGE)
def test_zweiter_gibt_nach_warten_auf(zweig: str, tmp_path: Path) -> None:
    _zweig_moeglich(zweig)
    datei = tmp_path / "sub" / "lauf.lock"
    halter = _kind(zweig, datei, "halter", 0)
    try:
        assert _antwort(halter)[0] == "gehalten"
        zweiter = _kind(zweig, datei, "pruefer", 0.6)
        art, dauer = _antwort(zweiter)
        assert zweiter.wait(timeout=30) == 0
        assert art == "belegt" and dauer >= 0.5
        sofort = _kind(zweig, datei, "pruefer", 0)
        assert _antwort(sofort)[0] == "belegt"
        assert sofort.wait(timeout=30) == 0
    finally:
        halter.communicate("\n", timeout=30)
    assert halter.returncode == 0
    danach = _kind(zweig, datei, "pruefer", 0)
    assert _antwort(danach)[0] == "gehalten"
    assert danach.wait(timeout=30) == 0


@pytest.mark.parametrize("zweig", ZWEIGE)
def test_zweiter_wartet_bis_frei(zweig: str, tmp_path: Path) -> None:
    _zweig_moeglich(zweig)
    datei = tmp_path / "lauf.lock"
    halter = _kind(zweig, datei, "halter", 0)
    try:
        assert _antwort(halter)[0] == "gehalten"
        zweiter = _kind(zweig, datei, "pruefer", 20)
        assert zweiter.stdout is not None
        assert zweiter.stdout.readline().startswith("bereit")
        time.sleep(0.6)  # zweiter steht jetzt in der Warteschleife
        assert halter.stdin is not None
        halter.stdin.write("\n")
        halter.stdin.flush()
        art, dauer = _antwort(zweiter)
        assert zweiter.wait(timeout=30) == 0
        assert art == "gehalten" and 0.4 <= dauer < 20
    finally:
        halter.communicate(timeout=30)
    assert halter.returncode == 0
    if zweig == "excl":
        assert not datei.with_name("lauf.lock.excl").exists()
