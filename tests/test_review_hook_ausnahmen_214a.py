"""Die Ausnahmen für ``review-hook/`` (1:1-Übernahme, #214 Teil A) sind kein Loch (#214).

Neue Dateien oder neue Stellen unter ``review-hook/`` mit dem alten Rollenwort oder einer rohen
Start-Stelle werden weiter gefunden. Dazu die Rest-Fälle des Installers aus der Prüfung.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aufseher_428 as aufseher  # noqa: E402
import test_starter_1406 as starter_test  # noqa: E402

from to_spawn import review_hook  # noqa: E402

_ROH_START = 'import subprocess\ndef neu():\n    subprocess.Popen(["x"])\n'


def test_wache_ausnahme_gilt_nur_fuer_die_eine_zeile(monkeypatch: pytest.MonkeyPatch) -> None:
    assert aufseher.AUSNAHME_ZEILEN == frozenset()
    rel = "review-hook/stop.py"
    zeile = '"""Beispielzeile (nachts liest die Wache das'
    monkeypatch.setattr(aufseher, "AUSNAHME_ZEILEN", frozenset({(rel, zeile)}))
    assert aufseher._funde_in_zeilen(rel, [zeile]) == []
    assert aufseher._funde_in_zeilen("review-hook/neu.py", [zeile]) == ["review-hook/neu.py:1: Wache"]
    assert aufseher._funde_in_zeilen(rel, ["die Wache startet"]) == [f"{rel}:1: Wache"]


def test_rest_start_faengt_neue_start_stellen_im_review_hook(tmp_path: Path) -> None:
    for rel in ("review-hook/neu.py", "review-hook/_hookbasis.py", "review-hook/tests/test_neu.py"):
        datei = tmp_path / rel
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_text(_ROH_START, encoding="utf-8")
    neu = starter_test._start_stellen(tmp_path) - starter_test.KEIN_START - starter_test.REST_START
    assert neu == {
        ("review-hook/neu.py", "neu"),
        ("review-hook/_hookbasis.py", "neu"),
        ("review-hook/tests/test_neu.py", "neu"),
    }


@pytest.mark.parametrize("inhalt", ["null", "[]", "5"])
def test_stand_datei_ohne_objekt_gilt_als_kein_stand(inhalt: str, tmp_path: Path) -> None:
    ziel = tmp_path / "hooks" / "review"
    ziel.mkdir(parents=True)
    (ziel / review_hook.STAND_DATEI).write_text(inhalt, encoding="utf-8")
    (ziel / "merker.py").write_text("# fremd\n", encoding="utf-8")
    bericht = review_hook.installiere(ziel, zeit="20261122-040000")
    assert (tmp_path / "hooks" / "_alt" / "review-20261122-040000" / "merker.py").read_text(encoding="utf-8") == (
        "# fremd\n"
    )
    assert isinstance(json.loads((ziel / review_hook.STAND_DATEI).read_text(encoding="utf-8")), dict)
    assert any("merker.py" in z and "gesichert" in z for z in bericht), bericht


@pytest.mark.skipif(sys.platform == "win32", reason="Links brauchen unter Windows Sonderrechte")
def test_link_im_ziel_wird_nie_durchgeschrieben(tmp_path: Path) -> None:
    ziel = tmp_path / "hooks" / "review"
    ziel.mkdir(parents=True)
    aussen = tmp_path / "aussen.py"
    aussen.write_text("# außen\n", encoding="utf-8")
    (ziel / "stop.py").symlink_to(aussen)
    (ziel / "merker.py").symlink_to(tmp_path / "tot.py")  # toter Link
    bericht = review_hook.installiere(ziel)
    assert aussen.read_text(encoding="utf-8") == "# außen\n"
    assert not (tmp_path / "tot.py").exists()
    assert (ziel / "stop.py").is_symlink() and (ziel / "merker.py").is_symlink()
    assert {"abweichend, Link nicht angefasst: stop.py", "abweichend, Link nicht angefasst: merker.py"} <= set(bericht)
    assert "stop.py" not in json.loads((ziel / review_hook.STAND_DATEI).read_text(encoding="utf-8"))


def test_schreiben_laesst_keine_temp_datei_liegen(tmp_path: Path) -> None:
    ziel = tmp_path / "hooks" / "review"
    review_hook.installiere(ziel)
    assert not [p for p in ziel.rglob(".*.neu")]
