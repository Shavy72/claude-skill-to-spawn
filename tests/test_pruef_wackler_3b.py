"""Wackler im Prüf-Werkzeugkasten: rote Tests genau einmal wiederholen (L3 3b-4, #103).

Echt laufen: CLI, git, pytest im frischen Probe-Repo. Der Wackel-Test zählt seine Läufe in einer
Datei außerhalb des Repos: erster Lauf rot, zweiter grün. Nichts ist gestellt.
"""

from __future__ import annotations

import json
from pathlib import Path

import test_pruef_tuer_3b as tuer


def _probe_mit_zaehler(tmp_path: Path, bedingung: str) -> tuple[Path, Path]:
    """Probe-Repo mit ``test_addiere``; ``bedingung`` (über ``lauf``) entscheidet über Grün."""
    zaehler = tmp_path / "zaehler.txt"
    zaehler.write_text("0", encoding="utf-8")
    test_text = (
        f"z = pathlib.Path({str(zaehler)!r}); lauf = int(z.read_text()) + 1; "
        f"z.write_text(str(lauf)); assert {bedingung}"
    )
    return tuer._probe_py(tmp_path / "py", test_text=test_text), zaehler


def test_erst_rot_dann_gruen_ist_wackler_nie_still_gruen(tmp_path: Path) -> None:
    probe, zaehler = _probe_mit_zaehler(tmp_path, "lauf >= 2")
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "wackler"
    assert [w["test"] for w in zeile["wackler"]] == [
        "tests/test_rechnen.py::test_addiere"
    ]
    assert zaehler.read_text(encoding="utf-8") == "2"
    stufe = zeile["stufen"][0]
    assert stufe["wiederholung_s"] > 0
    assert "--lf" in stufe["wiederholung"]
    liste = probe / ".to-spawn" / "pruef" / "wackler.jsonl"
    eintrag = json.loads(liste.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert eintrag["test"] == "tests/test_rechnen.py::test_addiere"
    assert {"zeit", "basis", "ziel", "adapter"} <= eintrag.keys()


def test_zweimal_rot_bleibt_rot_nach_genau_einer_wiederholung(tmp_path: Path) -> None:
    probe, zaehler = _probe_mit_zaehler(tmp_path, "lauf < 0")
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 1, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "rot"
    assert zeile["wackler"] == []
    assert zaehler.read_text(encoding="utf-8") == "2"
    assert zeile["stufen"][0]["wiederholung_s"] > 0
    assert not (probe / ".to-spawn" / "pruef" / "wackler.jsonl").exists()


def test_alles_gruen_ohne_wiederholung(tmp_path: Path) -> None:
    probe, zaehler = _probe_mit_zaehler(tmp_path, "lauf >= 1")
    erg = tuer._tuer(probe, "base", "HEAD")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    zeile = tuer._zeile(erg)
    assert zeile["urteil"] == "grün"
    assert zeile["wackler"] == []
    assert zaehler.read_text(encoding="utf-8") == "1"
    assert zeile["stufen"][0]["wiederholung_s"] == 0
