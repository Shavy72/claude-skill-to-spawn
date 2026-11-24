"""#432: Blocker-Prüfung im Sekundentakt; fetch erst wenn alle Blocker zu; Abfrage-Fehler = blockiert."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from test_blocker_staging_1527 import _dep, _fake_gh, _lade_bau

from conftest import attrappe


def _args(bau, takt: int | None = None) -> argparse.Namespace:
    t = bau.TAKT_STANDARD_S if takt is None else takt
    return argparse.Namespace(
        umzug=None, resume=False, probesitz=False, sofort=False, takt=t
    )


def test_blocker_frei_nach_15s_startet_binnen_35s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    uhr = [0.0]
    monkeypatch.setattr(
        bau, "blocker_offen", lambda *_a, **_k: ["#5 offen"] if uhr[0] < 15 else []
    )
    orig = bau.auf_blocker_warten

    def schlafen(s: float) -> None:
        uhr[0] += s

    monkeypatch.setattr(
        bau,
        "auf_blocker_warten",
        lambda t, takt, **k: orig(t, takt, schlafen=schlafen, **k),
    )
    bau.blocker_schranke("432", _args(bau))
    assert 15 <= uhr[0] <= 35


def test_takt_untergrenze_5s(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    gesehen: list[int] = []
    monkeypatch.setattr(
        bau, "auf_blocker_warten", lambda _t, takt, **_k: gesehen.append(takt)
    )
    bau.blocker_schranke("432", _args(bau, 1))
    assert gesehen == [5]


def test_kein_fetch_solange_blocker_offen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    _fake_gh(tmp_path, monkeypatch, [_dep(5, "closed"), _dep(6, "open")])
    aufrufe: list[list[str]] = []

    def git(args, *_a, **_k):
        aufrufe.append(list(args))
        return 0, ""

    monkeypatch.setattr(bau.gh, "_git_ausgabe", git)
    monkeypatch.setattr(bau.gh, "hauptzweig", lambda _repo: "master")
    assert bau.blocker_offen("432") == ["#6 offen"]
    assert aufrufe == []


def test_abfrage_fehler_bleibt_blockiert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    attrappe(bin_dir, "gh", "import sys\nsys.stderr.write('rate limit')\nsys.exit(1)\n")
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ.get('PATH', '')}")
    assert bau.blocker_offen("432") == ["Blocker-Abfrage fehlgeschlagen"]
