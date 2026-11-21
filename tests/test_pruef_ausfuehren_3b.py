"""Stufe 0 Cache und schnelleres Ausführen (L3 3b-3, #102).

Echt laufen: CLI, git, pytest (und pytest-xdist, falls installiert) im frischen Probe-Repo. Die
Cache-Ablage liegt je Test unter ``tmp_path`` (Fixture ``_pruef_cache_im_test`` in conftest).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from test_pruef_tuer_3b import _git, _probe_py, _tuer, _zeile

from to_spawn.pruef import ausfuehren, cache


def _eintraege() -> list[Path]:
    ordner = cache.ordner()
    return sorted(ordner.glob("*.json")) if ordner.is_dir() else []


def test_zweiter_lauf_desselben_stands_ist_cache_treffer(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py")
    erst = _zeile(_tuer(probe, "base", "HEAD"))
    assert (erst["urteil"], erst["cache_treffer"]) == ("grün", False)
    assert len(_eintraege()) == 1
    assert not str(_eintraege()[0]).startswith(str(probe)), "Ablage nie im Arbeitsbaum"

    start = time.monotonic()
    erg = _tuer(probe, "base", "HEAD")
    dauer = time.monotonic() - start
    assert erg.returncode == 0, erg.stdout + erg.stderr
    zweit = _zeile(erg)
    assert (zweit["urteil"], zweit["cache_treffer"]) == ("grün", True)
    assert [s["name"] for s in zweit["stufen"]] == ["cache"]
    assert dauer < 5, f"Cache-Treffer brauchte {dauer:.1f} s"
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=probe,
        capture_output=True,
        text=True,
        check=True,
    )
    assert not [z for z in status.stdout.splitlines() if "to-spawn" in z or ".json" in z]


def test_lock_datei_geaendert_ist_kein_treffer(tmp_path: Path) -> None:
    # Versteckte (per .gitignore) Lock-Datei: der Tree bleibt gleich, nur der Lock-Hash ändert sich.
    probe = _probe_py(tmp_path / "py")
    (probe / ".gitignore").write_text("requirements.txt\n", encoding="utf-8")
    _git(probe, "add", ".gitignore")
    _git(probe, "commit", "-qm", "ignore")
    (probe / "requirements.txt").write_text("pytest==1\n", encoding="utf-8")
    assert _zeile(_tuer(probe, "base", "HEAD"))["cache_treffer"] is False
    (probe / "requirements.txt").write_text("pytest==2\n", encoding="utf-8")
    zeile = _zeile(_tuer(probe, "base", "HEAD"))
    assert (zeile["urteil"], zeile["cache_treffer"]) == ("grün", False)


def test_pruef_konfig_geaendert_ist_kein_treffer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _probe_py(tmp_path / "py")
    vorher = cache.schluessel(probe, "base", "HEAD")
    assert vorher is not None
    (probe / ".to-spawn").mkdir()
    (probe / ".to-spawn" / "config.json").write_text("{}", encoding="utf-8")
    assert cache.schluessel(probe, "base", "HEAD") != vorher
    (probe / ".to-spawn" / "config.json").unlink()
    assert cache.schluessel(probe, "base", "HEAD") == vorher
    # PYTEST_ADDOPTS ändert, was pytest prüft → Tür lässt neu laufen.
    assert _zeile(_tuer(probe, "base", "HEAD"))["cache_treffer"] is False
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p no:randomly")
    assert _zeile(_tuer(probe, "base", "HEAD"))["cache_treffer"] is False


def test_andere_basis_ist_kein_treffer(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py")
    for name in ("neu.py", "neu2.py"):  # zwei Commits: HEAD~1 ist nicht ``base``
        (probe / name).write_text("X = 1\n", encoding="utf-8")
        _git(probe, "add", name)
        _git(probe, "commit", "-qm", name)
    assert _zeile(_tuer(probe, "base", "HEAD"))["cache_treffer"] is False
    assert _zeile(_tuer(probe, "HEAD~1", "HEAD"))["cache_treffer"] is False
    assert _zeile(_tuer(probe, "base", "HEAD"))["cache_treffer"] is True


def test_roter_lauf_wird_nie_gemerkt(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py", test_text="assert addiere(2, 2) == 5")
    for _ in range(2):
        erg = _tuer(probe, "base", "HEAD")
        assert erg.returncode == 1, erg.stdout + erg.stderr
        assert _zeile(erg)["cache_treffer"] is False
    assert _eintraege() == []


def test_nur_gruen_wird_gemerkt_kein_wackler_kein_gate_fehler() -> None:
    for urteil in ("wackler", "rot", "gate-fehler"):
        cache.merke("a" * 64, {"urteil": urteil, "cache_treffer": False})
    assert _eintraege() == []
    assert cache.treffer(None) is False
    cache.merke(None, {"urteil": "grün"})
    assert _eintraege() == []


def test_uncommittete_aenderung_ist_kein_treffer(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py")
    assert _zeile(_tuer(probe, "base", "HEAD"))["urteil"] == "grün"
    (probe / "rechnen.py").write_text("def addiere(a, b):\n    return 0\n", encoding="utf-8")
    erg = _tuer(probe, "base", "HEAD")
    assert erg.returncode == 2, erg.stdout + erg.stderr
    assert _zeile(erg)["cache_treffer"] is False


@pytest.mark.parametrize("kaputt", ["muell", "fremd", "ordner", "verfallen"])
def test_kaputter_eintrag_ist_kein_treffer_und_kein_absturz(tmp_path: Path, kaputt: str) -> None:
    probe = _probe_py(tmp_path / "py")
    assert _zeile(_tuer(probe, "base", "HEAD"))["cache_treffer"] is False
    (eintrag,) = _eintraege()
    if kaputt == "muell":
        eintrag.write_bytes(b"\xff{kein json")
    elif kaputt == "fremd":
        daten = json.loads(eintrag.read_text(encoding="utf-8"))
        eintrag.write_text(json.dumps({**daten, "schluessel": "b" * 64}), encoding="utf-8")
    elif kaputt == "ordner":
        eintrag.unlink()
        eintrag.mkdir()  # unlesbar auf jeder Plattform (IsADirectory/PermissionError)
    else:
        daten = json.loads(eintrag.read_text(encoding="utf-8"))
        daten["zeit"] -= cache.VERFALL_S + 60
        eintrag.write_text(json.dumps(daten), encoding="utf-8")
    erg = _tuer(probe, "base", "HEAD")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    zeile = _zeile(erg)
    assert (zeile["urteil"], zeile["cache_treffer"]) == ("grün", False)
    assert zeile["stufen"][-1]["name"] == "voll"


def test_worker_unter_ram_deckel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meminfo = tmp_path / "meminfo"
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    monkeypatch.setattr(ausfuehren.os, "cpu_count", lambda: 16)
    meminfo.write_text(f"MemAvailable: {3 * ausfuehren.JE_WORKER_MIB * 1024} kB\n", encoding="utf-8")
    assert ausfuehren.worker() == 3
    meminfo.write_text("MemAvailable: 1024 kB\n", encoding="utf-8")
    assert ausfuehren.worker() == 1
    meminfo.unlink()  # unbekannt (z. B. Windows) → vorsichtiger Festwert
    assert ausfuehren.worker() == ausfuehren.OHNE_RAM_WERT
    monkeypatch.setattr(ausfuehren.os, "cpu_count", lambda: None)
    assert ausfuehren.worker() == 1


def test_voll_lauf_parallel_mit_worksteal_nur_wenn_repo_xdist_nennt(tmp_path: Path) -> None:
    pytest.importorskip("xdist")
    probe = _probe_py(tmp_path / "py")
    seriell = _zeile(_tuer(probe, "base", "HEAD"))
    assert "-n " not in seriell["stufen"][-1]["befehl"], "ohne Zusage des Repos nie parallel"
    (probe / "requirements.txt").write_text("pytest-xdist\n", encoding="utf-8")
    _git(probe, "add", "requirements.txt")
    _git(probe, "commit", "-qm", "xdist")
    zeile = _zeile(_tuer(probe, "base", "HEAD"))
    assert zeile["urteil"] == "grün"
    if ausfuehren.worker() > 1:
        assert "--dist worksteal" in zeile["stufen"][-1]["befehl"]
        assert "+xdist" in zeile["werkzeug"][0]
