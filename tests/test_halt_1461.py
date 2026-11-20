"""Ticket #1461 (Folge #1444): Fehler-Zweig von ``halt.pruefen`` läuft in die 24-h-Frist;
``anlegen`` löscht einen liegengebliebenen Merker.

Echte Halt-Datei, echter Merker; die Attrappe ist nur der erzwungene Programmfehler.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import halt


@pytest.fixture
def klon(tmp_path: Path) -> Path:
    repo = tmp_path / "klon"
    (repo / ".to-spawn").mkdir(parents=True)
    return repo


def _bis_spaeter() -> halt.Bedingung:
    return halt.Bedingung("bis", (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat())


def _vor_frist() -> str:
    return (datetime.now(timezone.utc) - halt.ALT_FRIST - timedelta(minutes=1)).isoformat()


def _merker_altern(klon: Path) -> None:
    merker = klon / halt.MERKER_DATEI
    stand = json.loads(merker.read_text(encoding="utf-8"))
    stand["seit"] = _vor_frist()
    merker.write_text(json.dumps(stand), encoding="utf-8")


def _kaputt(*_a: object, **_k: object) -> None:
    raise RuntimeError("unerwartet")


@pytest.mark.parametrize("stelle", ["_erfuellt", "_lies"])
def test_dauerhafter_fehler_hebt_nach_frist_ab_erster_beobachtung_auf(
    klon: Path, monkeypatch: pytest.MonkeyPatch, stelle: str
) -> None:
    halt.anlegen(klon, "Nachtruhe", _bis_spaeter())
    monkeypatch.setattr(halt, stelle, _kaputt)
    assert halt.pruefen(klon) is not None
    assert (klon / halt.DATEI).exists()
    assert (klon / halt.MERKER_DATEI).exists(), "Fehler-Zweig muss die erste Beobachtung merken"
    assert halt.pruefen(klon) is not None  # zweiter Takt vor Ablauf: hält weiter
    _merker_altern(klon)
    assert halt.pruefen(klon, aufheben=False) is None  # Trockenlauf: würde aufheben …
    assert (klon / halt.DATEI).exists()  # … ändert aber nichts
    assert halt.pruefen(klon) is None
    assert not (klon / halt.DATEI).exists()


def test_alter_merker_greift_nicht_bei_neuem_halt(klon: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (klon / halt.MERKER_DATEI).write_text(
        json.dumps({"halt_seit": "unbekannt", "seit": _vor_frist()}), encoding="utf-8"
    )
    halt.anlegen(klon, "Nachtruhe", _bis_spaeter())
    assert not (klon / halt.MERKER_DATEI).exists()
    monkeypatch.setattr(halt, "_lies", _kaputt)  # Schlüssel „unbekannt“ wie der Rest-Merker
    assert halt.pruefen(klon) is not None
    assert (klon / halt.DATEI).exists()
    _merker_altern(klon)  # eigene Frist abgelaufen ⇒ fällt
    assert halt.pruefen(klon) is None
    assert not (klon / halt.DATEI).exists()
