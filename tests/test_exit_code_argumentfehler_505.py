"""Argument-Fehler beim Einreichen sind kein „Stand unbekannt“ (#505, Folge aus #492).

Die Vorlagen (``repo-scripts/_default.json``, ``skripte/wache.py``) sagen: ``einreichen`` mit
Exit 2 → denselben Befehl erneut rufen. argparse endet bei falschen Argumenten aber ebenfalls
mit 2 — ein Tippfehler im Befehl würde so endlos wiederholt. Soll: falsche Argumente enden mit
``einreichen.AUFRUF`` (Aufruf falsch, nicht wiederholen), ``einreichen.UNBEKANNT`` bleibt 2.

Echter Aufruf von ``to_spawn.py`` per ``subprocess``; argparse bricht vor jedem git/gh-Aufruf ab.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import einreichen

#: Wert, den die Vorlagen als „Stand unbekannt – erneut rufen“ fest im Text nennen.
VORLAGEN_STAND_UNBEKANNT = 2


def _to_spawn(ordner: Path, *argumente: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), *argumente],
        check=False,
        cwd=ordner,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def test_stand_unbekannt_bleibt_exit_2_wie_in_den_vorlagen() -> None:
    assert einreichen.UNBEKANNT == VORLAGEN_STAND_UNBEKANNT
    assert einreichen.Ergebnis(einreichen.UNBEKANNT, "x").zeile.startswith("Stand unbekannt")
    assert einreichen.AUFRUF != einreichen.UNBEKANNT


@pytest.mark.parametrize(
    "argumente",
    [
        pytest.param(("einreichen",), id="nummer-fehlt"),
        pytest.param(("einreichen", "12", "--gibts-nicht"), id="unbekannte-option"),
        pytest.param(("einreichen", "12", "13"), id="zu-viele-argumente"),
    ],
)
def test_falsche_argumente_enden_mit_aufruf_nicht_mit_stand_unbekannt(
    tmp_path: Path, argumente: tuple[str, ...]
) -> None:
    lauf = _to_spawn(tmp_path, *argumente)
    assert lauf.returncode == einreichen.AUFRUF, (lauf.returncode, lauf.stderr[-500:])
    assert lauf.returncode != einreichen.UNBEKANNT
    assert "usage" in lauf.stderr.lower(), lauf.stderr[-500:]


def test_keine_nummer_endet_ebenfalls_mit_aufruf(tmp_path: Path) -> None:
    lauf = _to_spawn(tmp_path, "einreichen", "abc")
    assert lauf.returncode == einreichen.AUFRUF, (lauf.returncode, lauf.stdout, lauf.stderr[-500:])
    assert "keine Ticket-/Spec-Nummer" in lauf.stdout


def test_andere_unterbefehle_behalten_argparse_exit_2(tmp_path: Path) -> None:
    """Nur ``einreichen`` bekommt den eigenen Code; sonst bleibt argparse beim Üblichen."""
    lauf = _to_spawn(tmp_path, "blocker")
    assert lauf.returncode == 2, (lauf.returncode, lauf.stderr[-500:])
