"""#1456 / #870 Fix-Runde 1: warte-lauf trennt Fehler (Exit 2) von Frist (Exit 1) und misst Zeit per Uhr."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from to_spawn import prozessbaum

REPO = Path(__file__).resolve().parents[1]


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO / "to_spawn.py"), "warte-lauf", *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="liest /proc")
def test_ungueltiges_regex_ist_exit_2_ohne_traceback() -> None:
    erg = _cli("(", "--frist", "0")
    assert erg.returncode == 2
    assert "Traceback" not in erg.stderr
    assert "Regex" in erg.stderr


def test_windows_ist_exit_2_mit_klarer_meldung(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(prozessbaum, "_WINDOWS", True)
    assert prozessbaum.warte_lauf_befehl("x", 0) == 2
    err = capsys.readouterr().err
    assert "Linux" in err and "Traceback" not in err


def test_hilfe_nennt_exit_codes() -> None:
    hilfe = subprocess.run(
        [sys.executable, str(REPO / "to_spawn.py"), "warte-lauf", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    ).stdout
    assert "Exit" in hilfe and "2" in hilfe


def test_pids_ausgabe_und_wartemenge_aus_einer_abfrage(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    aufrufe: list[int] = []

    def fake(muster: str) -> list[int]:
        aufrufe.append(1)
        return [111] if len(aufrufe) == 1 else [222]

    monkeypatch.setattr(prozessbaum, "laufende", fake)
    monkeypatch.setattr(prozessbaum, "_WINDOWS", False)
    assert prozessbaum.warte_lauf_befehl("x", 0) == 0  # gewartet wird auf die ausgegebene 111, nicht auf 222
    assert capsys.readouterr().out.split() == ["111"]


def test_frist_zaehlt_uhrzeit_nicht_durchlaeufe(monkeypatch: pytest.MonkeyPatch) -> None:
    def langsam(muster: str) -> list[int]:
        time.sleep(0.3)  # jede Abfrage kostet Zeit
        return [1]

    monkeypatch.setattr(prozessbaum, "laufende", langsam)
    monkeypatch.setattr(prozessbaum, "_WINDOWS", False)
    start = time.monotonic()
    assert prozessbaum.warte_auf_lauf("x", frist_s=1.0, takt_s=0.1, pids={1}) is False
    assert time.monotonic() - start < 1.8
