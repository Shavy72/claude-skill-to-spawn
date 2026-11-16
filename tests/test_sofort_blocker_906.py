"""``bau.py --sofort`` wartet auf offene native Blocker (#906, Punkt 3).

Vorfall: capo, spawn, respawn und Aufpasser starten ``bau.py <N> --sofort``. Bis jetzt
übersprang ``--sofort`` das Blocker-Warten ganz — blockierte Tickets starteten neu und
belegten Plätze.

Vertrag jetzt:
- ohne ``--sofort``: beide Prüfungen (Blocker zu + Commit ``(#N)`` auf dem Hauptzweig), fail-closed;
- mit ``--sofort``: nur Blocker, deren Issue nicht ``closed`` ist; gh fehlt oder Abfrage
  scheitert → Warnung und Start;
- ``--umzug``, ``--resume``, ``--probesitz``: kein Warten (laufende Arbeit).

Echt sind ``blocker_offen``, ``auf_blocker_warten`` und ``warte_modus``; ersetzt wird nur
``gh`` (Skript im PATH, liefert die Blocker-Liste) und das Schlafen.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import logging
import os
import sys
from argparse import Namespace
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

_ZAEHLER = itertools.count()


def _lade_bau(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    monkeypatch.setenv("TO_SPAWN_GH_REPO", "test/spiegel")
    modulname = f"bau_sofort_906_{next(_ZAEHLER)}"
    spec = importlib.util.spec_from_file_location(modulname, SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    bau = importlib.util.module_from_spec(spec)
    sys.modules[modulname] = bau
    try:
        spec.loader.exec_module(bau)
    finally:
        sys.modules.pop(modulname, None)
    bau.GH_REPO = "test/spiegel"
    return bau


def _fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, deps: object | None) -> None:
    """``gh`` im PATH: gibt ``deps`` als JSON aus; ``None`` = Abfrage scheitert (Exit 1)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    skript = bin_dir / "gh"
    if deps is None:
        skript.write_text("#!/bin/sh\necho 'HTTP 502' >&2\nexit 1\n", encoding="utf-8")
    else:
        daten = tmp_path / "deps.json"
        daten.write_text(json.dumps(deps), encoding="utf-8")
        skript.write_text(f"#!/bin/sh\ncat '{daten}'\n", encoding="utf-8")
    skript.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def _ohne_gh(monkeypatch: pytest.MonkeyPatch, bau: ModuleType) -> None:
    monkeypatch.setattr(bau.shutil, "which", lambda name: None if name == "gh" else "/bin/" + name)


# --- blocker_offen --------------------------------------------------------------


def test_nur_offene_meldet_offenen_blocker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    _fake_gh(tmp_path, monkeypatch, [{"number": 5, "state": "open"}])
    assert bau.blocker_offen("906", nur_offene=True) == ["#5 offen"]


def test_nur_offene_ignoriert_geschlossenen_blocker_ohne_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    _fake_gh(tmp_path, monkeypatch, [{"number": 5, "state": "closed"}])
    # Kein git-Repo unter tmp_path: würde die Commit-Prüfung laufen, käme „kein Commit“.
    assert bau.blocker_offen("906", nur_offene=True) == []


def test_voll_meldet_geschlossenen_blocker_ohne_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    _fake_gh(tmp_path, monkeypatch, [{"number": 5, "state": "closed"}])
    monkeypatch.setattr(bau.gh, "hauptzweig", lambda _repo: "master")
    gruende = bau.blocker_offen("906")
    assert len(gruende) == 1 and gruende[0].startswith("#5 zu, aber kein Commit")


def test_nur_offene_startet_bei_gh_fehler_mit_warnung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    _fake_gh(tmp_path, monkeypatch, None)
    with caplog.at_level(logging.WARNING):
        assert bau.blocker_offen("906", nur_offene=True) == []
    assert any("Blocker" in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    assert bau.blocker_offen("906") == ["Blocker-Abfrage fehlgeschlagen"]  # ohne --sofort fail-closed


def test_nur_offene_startet_ohne_gh_mit_warnung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    _ohne_gh(monkeypatch, bau)
    with caplog.at_level(logging.WARNING):
        assert bau.blocker_offen("906", nur_offene=True) == []
    assert any("gh fehlt" in r.getMessage() for r in caplog.records)
    assert bau.blocker_offen("906") == ["gh fehlt"]


# --- auf_blocker_warten -----------------------------------------------------------


def test_auf_blocker_warten_nur_offene_wartet_bis_blocker_zu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    _fake_gh(tmp_path, monkeypatch, [{"number": 5, "state": "open"}])
    schlaefe: list[int] = []

    def schlafen(sek: int) -> None:
        schlaefe.append(sek)
        _fake_gh(tmp_path, monkeypatch, [{"number": 5, "state": "closed"}])  # Blocker wird zugemacht

    bau.auf_blocker_warten("906", 60, nur_offene=True, schlafen=schlafen)
    assert schlaefe == [60]


# --- Entscheidungsstelle in main --------------------------------------------------


def _args(**kw: object) -> Namespace:
    basis: dict[str, object] = {
        "sofort": False,
        "umzug": None,
        "resume": None,
        "probesitz": False,
    }
    basis.update(kw)
    return Namespace(**basis)


@pytest.mark.parametrize(
    ("kw", "erwartet"),
    [
        ({}, "voll"),
        ({"sofort": True}, "nur_offen"),
        ({"sofort": True, "umzug": "b@s:p"}, None),
        ({"umzug": "b@s:p"}, None),
        ({"resume": "abc"}, None),
        ({"sofort": True, "resume": "abc"}, None),
        (
            {"sofort": True, "probesitz": True},
            None,
        ),  # main setzt bei --probesitz sofort=True
    ],
)
def test_warte_modus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kw: dict[str, object],
    erwartet: str | None,
) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    assert bau.warte_modus(_args(**kw)) == erwartet


# --- Aufruf in main: warte_modus → auf_blocker_warten ------------------------------


@pytest.mark.parametrize(
    ("kw", "erwartet"),
    [
        ({}, [("906", 60, False)]),
        ({"sofort": True}, [("906", 60, True)]),
        ({"sofort": True, "umzug": "b@s:p"}, []),
        ({"resume": "abc"}, []),
    ],
)
def test_blocker_schranke_ruft_warten_passend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kw: dict[str, object],
    erwartet: list[tuple[str, int, bool]],
) -> None:
    bau = _lade_bau(tmp_path, monkeypatch)
    aufrufe: list[tuple[str, int, bool]] = []
    monkeypatch.setattr(
        bau,
        "auf_blocker_warten",
        lambda t, takt, *, nur_offene=False: aufrufe.append((t, takt, nur_offene)),
    )
    bau.blocker_schranke("906", _args(takt=30, **kw))
    assert aufrufe == erwartet
