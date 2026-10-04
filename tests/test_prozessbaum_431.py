"""#431 Fixrunde 4, Befunde 1+2: ein gemeinsames Prozessbaum-Modul.

respawn und aufpasser beendeten den Prozessbaum eines tmux-Panes mit je eigenem
Gerüst. ``to_spawn.prozessbaum`` hält dieses Wissen jetzt an genau einer Stelle;
die Tests laufen gegen echte Kindprozesse, nicht gegen Attrappen.
"""

from __future__ import annotations

import ast
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL))

from to_spawn import prozessbaum


def _starten(befehl: list[str]) -> subprocess.Popen[bytes]:
    """Startet ``befehl`` und räumt ihn im Hintergrund ab — wie tmux es mit dem Pane
    tut. Sonst bliebe der beendete Prozess als Zombie in ``/proc`` und „lebte“."""
    proz = subprocess.Popen(befehl)
    threading.Thread(target=proz.wait, daemon=True).start()
    return proz


@pytest.fixture
def eltern_mit_kind() -> Iterator[subprocess.Popen[bytes]]:
    """Eine Shell, die selbst ein ``sleep`` als Kind startet und dann wartet."""
    proz = _starten(["sh", "-c", "sleep 30 & wait"])
    try:
        ende = time.monotonic() + 5
        while len(prozessbaum.baum(proz.pid)) < 2 and time.monotonic() < ende:
            time.sleep(0.05)
        yield proz
    finally:
        for pid in reversed(prozessbaum.baum(proz.pid)):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proz.wait(timeout=5)


def test_baum_enthaelt_wurzel_und_kind(
    eltern_mit_kind: subprocess.Popen[bytes],
) -> None:
    baum = prozessbaum.baum(eltern_mit_kind.pid)
    assert baum[0] == eltern_mit_kind.pid
    assert len(baum) == 2


def test_lebt_echt_und_tot() -> None:
    proz = subprocess.Popen(["sleep", "30"])
    assert prozessbaum.lebt(proz.pid)
    proz.kill()
    proz.wait(timeout=5)
    assert not prozessbaum.lebt(proz.pid)


def test_beenden_term_reicht(eltern_mit_kind: subprocess.Popen[bytes]) -> None:
    baum = prozessbaum.baum(eltern_mit_kind.pid)
    kind = baum[1]
    stufen = ((signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0))
    eskaliert: list[int] = []
    assert prozessbaum.beenden(
        list(reversed(baum)), stufen, eskalation=lambda sig, _: eskaliert.append(sig)
    )
    eltern_mit_kind.wait(timeout=5)
    assert not prozessbaum.lebt(kind)
    assert eskaliert == []


def test_beenden_eskaliert_zu_kill() -> None:
    """Ein Prozess, der SIGTERM ignoriert, stirbt erst an der nächsten Stufe."""
    proz = _starten(["sh", "-c", "trap '' TERM; while :; do sleep 0.1; done"])
    time.sleep(0.3)
    eskaliert: list[int] = []
    try:
        assert prozessbaum.beenden(
            [proz.pid],
            ((signal.SIGTERM, 0.5), (signal.SIGKILL, 5.0)),
            eskalation=lambda sig, lebende: eskaliert.append(sig),
        )
        assert eskaliert == [signal.SIGKILL]
    finally:
        proz.kill()
        proz.wait(timeout=5)


def test_beenden_wartet_nur_auf_ziel() -> None:
    """``warten_auf=[]``: Signal geht raus, gewartet wird nicht."""
    proz = _starten(["sh", "-c", "trap '' TERM; while :; do sleep 0.1; done"])
    time.sleep(0.3)
    try:
        start = time.monotonic()
        assert prozessbaum.beenden([proz.pid], ((signal.SIGTERM, 30.0),), warten_auf=[])
        assert time.monotonic() - start < 2
    finally:
        proz.kill()
        proz.wait(timeout=5)


def test_baum_proc_unlesbar_ist_fehler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ein nicht lesbares ``/proc`` heißt Fehler, nicht „keine Kinder“."""
    monkeypatch.setattr(prozessbaum, "_PROC", tmp_path / "fehlt")
    with pytest.raises(OSError):
        prozessbaum.baum(1)


def _importe(datei: Path) -> set[str]:
    namen: set[str] = set()
    for knoten in ast.walk(ast.parse(datei.read_text(encoding="utf-8"))):
        if isinstance(knoten, ast.ImportFrom):
            namen.update(a.name for a in knoten.names)
            namen.add(knoten.module or "")
        elif isinstance(knoten, ast.Import):
            namen.update(a.name for a in knoten.names)
    return namen


@pytest.mark.parametrize("modul", ["respawn.py", "aufpasser.py"])
def test_respawn_und_aufpasser_nutzen_prozessbaum(modul: str) -> None:
    assert "prozessbaum" in _importe(WURZEL / "to_spawn" / modul)


def test_respawn_unter_1000_zeilen() -> None:
    zeilen = (
        (WURZEL / "to_spawn" / "respawn.py").read_text(encoding="utf-8").count("\n")
    )
    assert zeilen < 1000, f"respawn.py hat {zeilen} Zeilen"


def test_kein_zweites_geruest_in_respawn() -> None:
    """Befund 2: respawn hat kein eigenes Prozessbaum-Gerüst mehr."""
    quelle = (WURZEL / "to_spawn" / "respawn.py").read_text(encoding="utf-8")
    for alt in ("def _nachkommen(", "def _lebt(", "def _signal(", "def _ist_claude("):
        assert alt not in quelle, alt
