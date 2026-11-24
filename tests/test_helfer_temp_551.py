"""Fester Temp-Ort und Aufräum-Weg für Helfer (#551).

Helfer (Bauer, Prüfer) legen Temp-Ordner nur über ``helfer-temp anlegen <name>`` an und
räumen nur über ``helfer-temp aufraeumen`` auf — nie per Shell-Löschbefehl mit ``*``.
Der feste Ort wird über ``TO_SPAWN_TEMP_ORDNER`` auf ``tmp_path`` umgebogen.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from to_spawn import helfer_temp

SKILL = Path(__file__).resolve().parent.parent


@pytest.fixture
def ort(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(tmp_path / "tmp-bau"))
    return tmp_path / "tmp-bau" / "helfer"


def test_anlegen_liefert_ordner_unter_festem_ort(ort: Path) -> None:
    pfad = helfer_temp.anlegen("l207-551")
    assert pfad.is_dir()
    assert pfad.parent.resolve() == ort.resolve()
    assert helfer_temp.anlegen("l207-551") == pfad  # idempotent


def test_aufraeumen_loescht_genau_diesen(ort: Path) -> None:
    weg = helfer_temp.anlegen("weg")
    (weg / "unter").mkdir()
    (weg / "unter" / "datei.txt").write_text("x", encoding="utf-8")
    bleibt = helfer_temp.anlegen("bleibt")
    assert helfer_temp.aufraeumen("weg") is True
    assert not weg.exists()
    assert bleibt.is_dir()
    assert helfer_temp.aufraeumen("weg") is False  # schon weg: kein Fehler


@pytest.mark.parametrize("name", ["", " ", "..", ".", "a/b", "../x", "a\\b", "*", "tmp-*", "~"])
def test_boese_namen_abgelehnt_nichts_geloescht(ort: Path, tmp_path: Path, name: str) -> None:
    nachbar = helfer_temp.anlegen("nachbar")
    aussen = tmp_path / "x"
    aussen.mkdir()
    with pytest.raises(ValueError):
        helfer_temp.aufraeumen(name)
    with pytest.raises(ValueError):
        helfer_temp.anlegen(name)
    assert nachbar.is_dir() and aussen.is_dir() and ort.is_dir()


def test_symlink_nach_aussen_wird_nie_geloescht(ort: Path, tmp_path: Path) -> None:
    aussen = tmp_path / "fremd"
    aussen.mkdir()
    (aussen / "wichtig.txt").write_text("x", encoding="utf-8")
    ort.mkdir(parents=True)
    try:
        (ort / "falle").symlink_to(aussen, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink nicht anlegbar (Windows ohne Recht)")
    with pytest.raises(ValueError):
        helfer_temp.aufraeumen("falle")
    assert (aussen / "wichtig.txt").is_file()
    assert helfer_temp.aufraeumen_aelter_als(0, jetzt=time.time() + 86400 * 10) == []
    assert (aussen / "wichtig.txt").is_file()


def test_fester_ort_als_symlink_nach_aussen_wird_nie_geloescht(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    basis = tmp_path / "tmp-bau"
    basis.mkdir()
    aussen = tmp_path / "fremd"
    (aussen / "opfer").mkdir(parents=True)
    try:
        (basis / "helfer").symlink_to(aussen, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink nicht anlegbar (Windows ohne Recht)")
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(basis))
    with pytest.raises(ValueError):
        helfer_temp.aufraeumen("opfer")
    assert (aussen / "opfer").is_dir()


def test_aelter_als_tage_laesst_junge_stehen(ort: Path) -> None:
    alt = helfer_temp.anlegen("alt")
    jung = helfer_temp.anlegen("jung")
    vor_zehn_tagen = time.time() - 10 * 86400
    os.utime(alt, (vor_zehn_tagen, vor_zehn_tagen))
    (ort / "datei.txt").write_text("keine Ordner", encoding="utf-8")
    weg = helfer_temp.aufraeumen_aelter_als(7)
    assert weg == [alt]
    assert not alt.exists()
    assert jung.is_dir()
    assert (ort / "datei.txt").is_file()


def test_cli_anlegen_gibt_pfad_aus_und_aufraeumen(ort: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("to_spawn_cli_551", SKILL / "to_spawn.py")
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.main(["helfer-temp", "anlegen", "cli"]) == 0
    pfad = Path(capsys.readouterr().out.strip())
    assert pfad.is_dir() and pfad.parent.resolve() == ort.resolve()
    assert cli.main(["helfer-temp", "aufraeumen", "cli"]) == 0
    assert not pfad.exists()
    assert cli.main(["helfer-temp", "aufraeumen", "../x"]) == 1
    assert cli.main(["helfer-temp", "aufraeumen", "--aelter-als-tage", "3"]) == 0


def test_loeschen_steckt_im_hausmeister() -> None:
    text = (SKILL / "to_spawn" / "helfer_temp.py").read_text(encoding="utf-8")
    assert "rmtree(" not in text and "hausmeister" in text
