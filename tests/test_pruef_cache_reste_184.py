"""Prüf-Cache (#184, Reste aus Pull-Request #173): Nachstellung der Review-Befunde mit echtem git-Repo.

1. Ein per ``.gitignore`` versteckter Symlink auf einen Ordner darf nie Grün aus dem Cache bringen.
2. Wechselnde Laufausgaben (``lauf.log``) verhindern jeden Treffer — ausgelagert nach #219 (xfail).
3. Ein großes verschachteltes ``node_modules`` darf den Deckel nicht sprengen; dessen Paketliste zählt.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_pruef_tuer_3b import _git, _probe_py, _tuer, _zeile

from to_spawn.pruef import cache

_LIEST_WERTE = (
    "import json, pathlib\n\n"
    "def test_werte():\n"
    "    ort = pathlib.Path(__file__).parent.parent / 'daten' / 'werte.json'\n"
    "    assert json.loads(ort.read_text()) is True\n"
)


def _ausschliessen(probe: Path, muster: str) -> None:
    with (probe / ".git" / "info" / "exclude").open("a", encoding="utf-8") as datei:
        datei.write(f"\n{muster}\n")


def _ordner_aussen(tmp_path: Path) -> Path:
    aussen = tmp_path / "aussen"
    aussen.mkdir()
    (aussen / "werte.json").write_text("true", encoding="utf-8")
    return aussen


def test_ignorierter_symlink_auf_ordner_ist_kein_treffer(tmp_path: Path) -> None:
    aussen = _ordner_aussen(tmp_path)
    probe = _probe_py(tmp_path / "py")
    (probe / "tests" / "test_werte.py").write_text(_LIEST_WERTE, encoding="utf-8")
    _git(probe, "add", "-A")
    _git(probe, "commit", "-qm", "liest daten/")
    (probe / "daten").symlink_to(aussen, target_is_directory=True)
    _ausschliessen(probe, "daten")
    assert _zeile(_tuer(probe, "base", "HEAD"))["urteil"] == "grün"
    (aussen / "werte.json").write_text("false", encoding="utf-8")
    zeile = _zeile(_tuer(probe, "base", "HEAD"))
    assert (zeile["urteil"], zeile["cache_treffer"]) == ("rot", False)
    assert cache.schluessel(probe, "base", "HEAD") is None


def test_symlink_auf_ordner_in_ignoriertem_ordner_ist_kein_schluessel(tmp_path: Path) -> None:
    aussen = _ordner_aussen(tmp_path)
    probe = _probe_py(tmp_path / "py")
    (probe / "ablage").mkdir()
    (probe / "ablage" / "notiz.txt").write_text("x", encoding="utf-8")
    (probe / "ablage" / "daten").symlink_to(aussen, target_is_directory=True)
    _ausschliessen(probe, "ablage/")
    vorher = cache.schluessel(probe, "base", "HEAD")
    (aussen / "werte.json").write_text("false", encoding="utf-8")
    nachher = cache.schluessel(probe, "base", "HEAD")
    assert vorher is None or vorher != nachher


def test_ignorierter_symlink_auf_datei_zaehlt_ziel_und_ins_leere_ist_kein_schluessel(tmp_path: Path) -> None:
    aussen = _ordner_aussen(tmp_path)
    probe = _probe_py(tmp_path / "py")
    (probe / "werte.json").symlink_to(aussen / "werte.json")
    _ausschliessen(probe, "werte.json")
    vorher = cache.schluessel(probe, "base", "HEAD")
    (aussen / "werte.json").write_text("false", encoding="utf-8")
    assert cache.schluessel(probe, "base", "HEAD") not in (None, vorher)
    (aussen / "werte.json").unlink()
    assert cache.schluessel(probe, "base", "HEAD") is None


@pytest.mark.xfail(strict=True, reason="Trefferquote bei wechselnden Laufausgaben: Folge-Issue #219")
def test_wechselnde_laufausgabe_laesst_treffer_zu(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py")
    (probe / "tests" / "test_log.py").write_text(
        "import pathlib, time\n\n"
        "def test_schreibt_log():\n"
        "    (pathlib.Path(__file__).parent.parent / 'lauf.log').write_text(str(time.time()))\n",
        encoding="utf-8",
    )
    _git(probe, "add", "-A")
    _git(probe, "commit", "-qm", "schreibt lauf.log")
    _ausschliessen(probe, "*.log")
    treffer = [_zeile(_tuer(probe, "base", "HEAD"))["cache_treffer"] for _ in range(3)]
    assert any(treffer)


def test_grosses_verschachteltes_node_modules_sprengt_deckel_nicht(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py")
    module = probe / "web" / "node_modules"
    for nr in range(cache._VERSTECKT_MAX_DATEIEN + 100):
        paket = module / f"p{nr // 50}"
        paket.mkdir(parents=True, exist_ok=True)
        (paket / f"d{nr}.js").write_text("x", encoding="utf-8")
    (module / ".package-lock.json").write_text('{"v": 1}', encoding="utf-8")
    _ausschliessen(probe, "node_modules/")
    vorher = cache.schluessel(probe, "base", "HEAD")
    assert vorher is not None
    (module / ".package-lock.json").write_text('{"v": 2}', encoding="utf-8")
    assert cache.schluessel(probe, "base", "HEAD") not in (None, vorher)
