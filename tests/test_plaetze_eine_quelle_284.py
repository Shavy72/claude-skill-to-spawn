"""#284/#624: Rechner- und Gesamtgrenze hängen nur am Arbeitsspeicher, nie an ``max_sessions``.

Messung 25.11.2026 (Bau-Server, 16 GB): 12 arbeitend / 24 insgesamt je 16 GB sind tragbar.
Vorher ergab dieselbe Maschine je nach Repo-Ordner 9/18 oder 30/60. Attrappe ist nur ``/proc``,
``meminfo`` und der Sessions-Ordner; ``speicher`` ist echt.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_obergrenze_rechner_460_fix1 import _EIGEN_TMUX, _claude, _welt

from to_spawn import speicher

_GB = 1024 * 1024


def _konfig(max_sessions: int, min_frei_mib: int = 0) -> dict:
    return {"speicher": {"max_sessions": max_sessions, "min_frei_mib": min_frei_mib, "staffel_s": 0}}


def test_16_gb_ergibt_12_und_24() -> None:
    assert speicher.rechner_grenze(16 * _GB) == 12
    assert speicher.gesamt_grenze(16 * _GB) == 24
    assert speicher.rechner_grenze(None) == 12  # ohne MemTotal gilt 16 GB
    assert speicher.rechner_grenze(16_300_000) == 12  # echter 16-GB-Rechner meldet etwas weniger


def test_skaliert_mit_arbeitsspeicher() -> None:
    assert speicher.rechner_grenze(8 * _GB) == 6
    assert speicher.gesamt_grenze(8 * _GB) == 12
    assert speicher.rechner_grenze(32 * _GB) == 24
    assert speicher.gesamt_grenze(32 * _GB) == 48


@pytest.mark.parametrize("max_sessions", [2, 6, 20, 30])
def test_grenze_unabhaengig_von_repo_konfig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_sessions: int) -> None:
    """11 arbeitende fremde: frei bei 12; 12 → „Rechner voll … Obergrenze 12“, egal welches max_sessions."""
    proc, sessions = _welt(tmp_path, monkeypatch, tmux=_EIGEN_TMUX)
    for pid in range(301, 312):
        _claude(proc, sessions, pid, "busy")
    frei, text = speicher.platz_frei_hier(_konfig(max_sessions))
    assert frei and "Rechner 11 von 12" in text and "insgesamt 11 von 24" in text, text
    _claude(proc, sessions, 312, "busy")
    frei, text = speicher.platz_frei_hier(_konfig(max_sessions))
    assert (frei, text) == (False, "Rechner voll: schon 12 Claude-Sessions auf dem Rechner (Obergrenze 12)")


def test_gesamtgrenze_24_meldet_richtige_zahl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc, sessions = _welt(tmp_path, monkeypatch, tmux=_EIGEN_TMUX)
    for pid in range(301, 325):
        _claude(proc, sessions, pid, "idle")
    frei, text = speicher.platz_frei_hier(_konfig(30))
    assert not frei and "schon 24 Claude-Sessions insgesamt" in text and "Obergrenze 24)" in text, text


def test_min_frei_blockt_unabhaengig_von_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _welt(tmp_path, monkeypatch, tmux=_EIGEN_TMUX)  # MemAvailable 10240 MiB, keine Session
    frei, text = speicher.platz_frei_hier(_konfig(30, min_frei_mib=20000))
    assert not frei and text.startswith("Speicher knapp"), text
