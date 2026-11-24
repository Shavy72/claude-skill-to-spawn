"""pt-Räumer bei Inode-Knappheit (#355 Teil 2).

Eine rote volle Suite hinterlässt ~220k Einträge; bei Suiten im 10-Minuten-Takt lief ``/tmp``
(tmpfs, 1 Mio. Inodes) vor Ablauf der Schonfrist von 1 h voll. Jetzt: ab 70 % Inode-Belegung
räumt der Räumer tote eigene Reste schon ab 10 min. Echte Ordner in ``tmp_path``, der
Inode-Wert kommt über die einspritzbare Funktion ``inode_anteil``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from test_pt_reste_177 import _HAT_XDIST, _env, _pt_ordner, _rest, _start, _tote_pid, _warten, _welt

import conftest

#: Zwischen knapper (10 min) und normaler (1 h) Schonfrist.
MITTEL_ALT_S = 20 * 60


def _marker_alter(ordner: Path, sekunden: float) -> None:
    zeit = time.time() - sekunden
    os.utime(ordner / conftest._MARKER, (zeit, zeit))


def _reste(temp: Path) -> dict[str, Path]:
    tot = _tote_pid()
    reste = {
        "mittel_tot": _rest(temp, "pt-mitteltt", tot, alt=False),
        "frisch_tot": _rest(temp, "pt-frischtt", tot, alt=False),
        "mittel_lebt": _rest(temp, "pt-mittellb", os.getpid(), alt=False),
        "fremd": _rest(temp, "pt-fremd355", None, alt=True),
    }
    for name in ("mittel_tot", "mittel_lebt"):
        _marker_alter(reste[name], MITTEL_ALT_S)
    return reste


@pytest.mark.parametrize("anteil", [0.70, 0.95])
def test_knappe_inodes_raeumen_tote_reste_schon_nach_kurzer_frist(tmp_path: Path, anteil: float) -> None:
    reste = _reste(tmp_path)
    conftest._reste_raeumen(tmp_path, inode_anteil=lambda _p: anteil)
    assert not reste["mittel_tot"].exists(), "toter Rest älter als 10 min muss bei Knappheit weg"
    assert reste["frisch_tot"].is_dir(), "frischer Rest bleibt (nie einen startenden Lauf treffen)"
    assert reste["mittel_lebt"].is_dir(), "Ordner eines lebenden Prozesses bleibt"
    assert reste["fremd"].is_dir(), "Ordner ohne Marker ist fremd und bleibt"


@pytest.mark.parametrize("anteil", [0.0, 0.69, None])
def test_ohne_knappheit_gilt_die_normale_schonfrist(tmp_path: Path, anteil: float | None) -> None:
    reste = _reste(tmp_path)
    conftest._reste_raeumen(tmp_path, inode_anteil=lambda _p: anteil)
    assert sorted(p.name for p in reste.values()) == _pt_ordner(tmp_path)


def test_ohne_statvfs_ist_der_anteil_unbekannt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows hat kein ``os.statvfs``: Inode-Regel greift dann nicht, kein Absturz."""
    monkeypatch.delattr(os, "statvfs", raising=False)
    assert conftest._inode_anteil(tmp_path) is None


@pytest.mark.braucht("posix")
def test_inode_anteil_misst_das_echte_laufwerk(tmp_path: Path) -> None:
    anteil = conftest._inode_anteil(tmp_path)
    assert anteil is None or 0.0 <= anteil <= 1.0, anteil


@pytest.mark.skipif(not _HAT_XDIST, reason="pytest-xdist fehlt")
def test_gruener_xdist_lauf_nach_rotem_laesst_nur_den_roten_ordner(tmp_path: Path) -> None:
    """Zwei Läufe hintereinander: der rote behält seinen Ordner, der grüne (``-n 2``) räumt seinen."""
    temp = tmp_path / "t"
    temp.mkdir()
    welt = _welt(tmp_path / "welt")
    code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "rot.txt", FEHLER="1")))
    assert code == 1, ausgabe
    rot = _pt_ordner(temp)
    assert len(rot) == 1, rot
    code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "gruen.txt"), "-n", "2"))
    assert code == 0, ausgabe
    gruen = Path((tmp_path / "gruen.txt").read_text(encoding="utf-8")).parent
    assert gruen.name.startswith("pt-") and gruen.name not in rot, gruen
    assert _pt_ordner(temp) == rot, "grüner Lauf muss seinen eigenen pt-Ordner gelöscht haben"
