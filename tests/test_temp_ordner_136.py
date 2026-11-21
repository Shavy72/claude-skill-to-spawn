"""Temp-Ordner auf der Platte statt RAM-Platte /tmp (#136, kleiner Schnitt).

Geprüft wird nur die Wahl des Ordners (:mod:`to_spawn.temp_ordner`) und dass die Aufrufer
(Prüf-Tür, Sandbox der Bau-Session, ``bau.py``, ``wache.py``) ihr ``TMPDIR`` daraus bekommen.
Das Räumen des Ordners ist #177.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

from to_spawn import config, nest, temp_ordner
from to_spawn.pruef import ausfuehren

SKILL = Path(__file__).resolve().parent.parent


def _maschine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inhalt: dict) -> None:
    datei = tmp_path / "maschine.json"
    datei.write_text(json.dumps(inhalt), encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(datei))
    monkeypatch.delenv("TO_SPAWN_TEMP_ORDNER", raising=False)


def test_vorgabe_liegt_im_home_nicht_im_system_temp() -> None:
    vorgabe = Path(config.MASCHINE_DEFAULTS["temp_ordner"]).expanduser()
    assert str(vorgabe).startswith(str(Path.home()))
    assert not str(vorgabe).startswith(tempfile.gettempdir())
    assert not str(vorgabe).startswith("/tmp")
    # kurz, weil Unix-Sockets unter pytest-tmp_path < 108 Zeichen brauchen
    assert len(str(config.MASCHINE_DEFAULTS["temp_ordner"])) <= temp_ordner.MAX_LAENGE


def test_pfad_kommt_aus_der_maschinen_liste_und_wird_angelegt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ziel = tmp_path / "platte" / "tmp-bau"
    _maschine(monkeypatch, tmp_path, {"temp_ordner": str(ziel)})
    assert temp_ordner.pfad() == ziel
    assert ziel.is_dir()


def test_test_tuer_geht_vor_der_maschinen_liste(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _maschine(monkeypatch, tmp_path, {"temp_ordner": str(tmp_path / "liste")})
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(tmp_path / "tuer"))
    assert temp_ordner.pfad() == tmp_path / "tuer"


@pytest.mark.parametrize("gefaehrlich", ["~", "/"])
def test_home_oder_wurzel_fallen_auf_die_vorgabe_zurueck(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gefaehrlich: str
) -> None:
    heim = tmp_path / "heim"
    heim.mkdir()
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("USERPROFILE", str(heim))
    _maschine(monkeypatch, tmp_path, {"temp_ordner": gefaehrlich})
    vorgabe = Path(str(config.MASCHINE_DEFAULTS["temp_ordner"])).expanduser()
    assert temp_ordner.pfad() == Path(str(vorgabe))
    assert vorgabe.is_dir() and heim in vorgabe.parents


def test_umgebung_setzt_alle_drei_variablen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(tmp_path / "t"))
    assert temp_ordner.umgebung() == {k: str(tmp_path / "t") for k in ("TMPDIR", "TEMP", "TMP")}


def test_umgebung_ist_leer_wenn_nicht_anlegbar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    datei = tmp_path / "datei"
    datei.write_text("x", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(datei / "drunter"))
    assert temp_ordner.umgebung() == {}


def test_pruef_lauf_hat_tmpdir_auf_dem_konfig_ordner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ziel = tmp_path / "platte"
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(ziel))
    monkeypatch.setenv("TMPDIR", "/tmp")
    code = "import tempfile; print(tempfile.gettempdir())"
    erg = ausfuehren.lauf([sys.executable, "-c", code], tmp_path)
    assert erg.ausgabe.strip() == str(ziel)


def test_sandbox_der_bau_session_darf_in_den_temp_ordner_schreiben(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ziel = tmp_path / "platte"
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(ziel))
    einst = nest.sandbox_einstellungen(tmp_path / "wt", tmp_path / "haupt", {}, home=tmp_path)
    assert str(ziel) in einst["filesystem"]["allowWrite"]


@pytest.mark.parametrize("skript", ["skripte/bau.py", "skripte/wache.py"])
def test_bau_und_aufseher_setzen_tmpdir_aus_der_einen_stelle(skript: str) -> None:
    quelle = (SKILL / skript).read_text(encoding="utf-8")
    assert "os.environ.update(temp_ordner.umgebung())" in quelle
    assert 'LOCALAPPDATA") or tempfile.gettempdir()) / "Temp"' not in quelle
