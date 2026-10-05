"""Wartendes bau.py sieht eine geänderte Session-Grenze (Vorfall 05.10.).

Am 05.10. wurde ``.to-spawn/config.json`` → ``speicher.max_sessions`` von 12 auf 20
gesetzt (21:58). bau.py-Fenster, die schon vorher in der Speicher-Warteschleife
standen, meldeten weiter „schon 13 Claude-Sessions (Obergrenze 12)“ und starteten
nie: die Konfig wurde einmal beim Start geladen und nie wieder gelesen.

Vertrag jetzt: :func:`speicher.auf_platz_warten` prüft jeden Zyklus mit den Grenzen,
die gerade in der Konfig-Datei stehen. Eine kaputte oder halb gespeicherte Datei
mitten im Warten ändert nichts: die letzten gültigen Werte gelten weiter, im Log
steht eine Warnung.

Echt sind Konfig-Datei, ``config.lade``, ``speicher.platz_frei`` und die
Warteschleife (auch über ``bau.py``). Ersetzt werden nur ``/proc`` und
``/proc/meminfo`` (13 Claude-Prozesse, 8 GB frei) über die Test-Türen des Moduls,
die Plattform (``linux``) und das Schlafen — das Schlafen ist der Moment, in dem
David die Datei ändert.
"""

from __future__ import annotations

import itertools
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"

if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import config, speicher  # noqa: E402

_ZAEHLER = itertools.count()
#: So viele Schlaf-Aufrufe, dann gilt die Schleife als hängend (alter Fehler: endlos).
_HAENGT_AB = 5


def _repo(tmp_path: Path, max_sessions: int) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    _schreibe_grenze(repo, max_sessions)
    return repo


def _schreibe_grenze(repo: Path, max_sessions: int) -> None:
    datei = repo / config.KONFIG_PFAD
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(
        json.dumps({"speicher": {"min_frei_mib": 2048, "max_sessions": max_sessions, "staffel_s": 20}}),
        encoding="utf-8",
    )


def _schreibe_kaputt(repo: Path) -> None:
    """Halb gespeicherte Datei, wie beim Editieren mitten im Schreiben."""
    (repo / config.KONFIG_PFAD).write_text('{"speicher": {"max_sessions": 2', encoding="utf-8")


@pytest.fixture
def bau_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux mit 13 laufenden Claude-Sessions und 8 GB freiem Speicher."""
    proc = tmp_path / "proc"
    proc.mkdir()
    for pid in range(100, 113):
        (proc / str(pid)).mkdir()
        (proc / str(pid) / "cmdline").write_bytes(b"/home/bau/.local/bin/claude\0--resume\0x\0")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16000000 kB\nMemAvailable: 8388608 kB\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PROC", str(proc))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    # Nur ``speicher`` sieht Linux — global ``sys.platform`` zu ändern würde Module, die
    # während des Tests erst geladen werden (bau.py-Kette), dauerhaft auf Linux festlegen.
    monkeypatch.setattr(speicher, "sys", SimpleNamespace(platform="linux", stderr=sys.stderr))


def _schlaf_mit_aenderungen(aenderungen: list[Callable[[], None]]) -> tuple[Callable[[float], None], list[float]]:
    """Schlafen, das bei Aufruf n die n-te Änderung ausführt und nach ``_HAENGT_AB`` abbricht."""
    aufrufe: list[float] = []

    def schlafen(sekunden: float) -> None:
        aufrufe.append(sekunden)
        if len(aufrufe) > _HAENGT_AB:
            raise AssertionError(f"Warteschleife hängt: {len(aufrufe)} Zyklen ohne Start")
        if len(aufrufe) <= len(aenderungen):
            aenderungen[len(aufrufe) - 1]()

    return schlafen, aufrufe


def _mit_protokoll(gesehen: list[int]) -> Callable[[dict], tuple[bool, str]]:
    """Echtes ``platz_frei``, merkt sich die Obergrenze jedes Zyklus."""

    def pruefen(konfig: dict) -> tuple[bool, str]:
        gesehen.append(speicher.grenzen(konfig)["max_sessions"])
        return speicher.platz_frei(konfig)

    return pruefen


def test_neue_grenze_waehrend_des_wartens_gilt_im_naechsten_zyklus(tmp_path: Path, bau_server: None) -> None:
    repo = _repo(tmp_path, max_sessions=12)
    konfig = config.lade(repo)
    assert speicher.platz_frei(konfig) == (False, "schon 13 Claude-Sessions (Obergrenze 12)")

    schlafen, aufrufe = _schlaf_mit_aenderungen([lambda: _schreibe_grenze(repo, 20)])
    zyklen = speicher.auf_platz_warten(konfig, wer="bau 999", schlafen=schlafen, takt_s=60)

    assert zyklen == 1, zyklen
    assert aufrufe == [60], aufrufe


def test_kaputte_datei_im_warten_behaelt_letzte_gueltige_grenze(
    tmp_path: Path, bau_server: None, caplog: pytest.LogCaptureFixture
) -> None:
    repo = _repo(tmp_path, max_sessions=12)
    konfig = config.lade(repo)
    gesehen: list[int] = []
    schlafen, _ = _schlaf_mit_aenderungen([lambda: _schreibe_kaputt(repo), lambda: _schreibe_grenze(repo, 20)])

    with caplog.at_level(logging.WARNING, logger="to_spawn.config"):
        zyklen = speicher.auf_platz_warten(konfig, wer="bau 999", pruefen=_mit_protokoll(gesehen), schlafen=schlafen)

    # Zyklus 2: Datei kaputt → weiter 12, nicht die Vorgabe 6 und kein Absturz.
    assert gesehen == [12, 12, 20], gesehen
    assert zyklen == 2, zyklen
    warnungen = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("bisherige Werte gelten weiter" in w for w in warnungen), warnungen


def test_bau_py_wartet_nicht_mehr_auf_alte_grenze(
    tmp_path: Path, bau_server: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weg wie in bau.py: Konfig beim Start laden, später warten (dazwischen Blocker-Wartezeit)."""
    import importlib.util

    repo = _repo(tmp_path, max_sessions=12)
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    modulname = f"bau_grenze_frisch_{next(_ZAEHLER)}"
    spec = importlib.util.spec_from_file_location(modulname, SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    bau = importlib.util.module_from_spec(spec)
    sys.modules[modulname] = bau
    try:
        spec.loader.exec_module(bau)
    finally:
        sys.modules.pop(modulname, None)

    konfig = bau.config.lade(bau.REPO)  # wie main() beim Start
    _schreibe_grenze(repo, 20)  # David ändert die Grenze, während bau.py noch auf Blocker wartet
    schlafen, aufrufe = _schlaf_mit_aenderungen([])

    zyklen = bau.auf_speicher_warten(konfig, wer="bau 999", schlafen=schlafen)

    assert zyklen == 0, zyklen
    assert aufrufe == [], aufrufe


def test_geladene_konfig_bleibt_ein_normales_dict(tmp_path: Path) -> None:
    repo = _repo(tmp_path, max_sessions=12)
    konfig = config.lade(repo)
    assert isinstance(konfig, dict)
    assert konfig["speicher"]["max_sessions"] == 12
    assert json.loads(json.dumps(konfig)) == konfig
    assert dict(konfig) == konfig


def test_frisch_laesst_fremde_dicts_unveraendert() -> None:
    eigen = {"speicher": {"max_sessions": 3}}
    assert config.frisch(eigen) is eigen
    assert config.frisch(None) is None


def test_geloeschte_datei_im_warten_gilt_wie_beim_laden(tmp_path: Path, bau_server: None) -> None:
    """Datei weg → Vorgaben wie bei ``config.lade`` (max_sessions 6), kein Festhalten an 20."""
    repo = _repo(tmp_path, max_sessions=20)
    datei = repo / config.KONFIG_PFAD
    # 8 GB frei < 999999 MiB Mindestmaß: die Schleife wartet in jedem Zyklus, bis der Hänge-Schutz greift.
    datei.write_text(json.dumps({"speicher": {"min_frei_mib": 999999, "max_sessions": 20}}), encoding="utf-8")
    konfig = config.lade(repo)
    gesehen: list[int] = []
    schlafen, _ = _schlaf_mit_aenderungen([datei.unlink])

    with pytest.raises(AssertionError, match="hängt"):
        speicher.auf_platz_warten(konfig, pruefen=_mit_protokoll(gesehen), schlafen=schlafen)

    assert gesehen[:2] == [20, config.DEFAULTS["speicher"]["max_sessions"]], gesehen


def test_kaputte_datei_warnt_einmal_je_strecke(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    repo = _repo(tmp_path, max_sessions=12)
    konfig = config.lade(repo)
    _schreibe_kaputt(repo)
    with caplog.at_level(logging.WARNING, logger="to_spawn.config"):
        for _ in range(3):
            konfig = config.frisch(konfig)
    assert konfig["speicher"]["max_sessions"] == 12
    assert sum("bisherige Werte" in r.getMessage() for r in caplog.records) == 1
