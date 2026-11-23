"""Weg-Test: Beleg über A, Route später nur mit Teilmenge B — echtes Git-Repo, echte Skripte."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REVIEW = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REVIEW))

from _gemeinsam import berechne_route  # noqa: E402
from review_beleg import schreiben  # noqa: E402


def _aendern(repo: Path) -> None:
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "README.md").write_text("# Test\nNeu\n", encoding="utf-8")


def _beleg_schreiben(repo: Path, tmp_path: Path, dateien: list[str]) -> dict:
    route = berechne_route(repo.as_posix(), "sitzung", files=dateien)
    entwurf = {k: route[k] for k in ("sha", "fingerprint", "stufe", "dateien")}
    entwurf["moment"] = "sitzung"
    pfad = tmp_path / "entwurf.json"
    pfad.write_text(json.dumps(entwurf), encoding="utf-8")
    assert schreiben(pfad.as_posix(), repo.as_posix()) == 0
    return route


def test_beleg_ueber_a_gilt_fuer_teilmenge_b(repo: Path, tmp_path: Path) -> None:
    _aendern(repo)
    _beleg_schreiben(repo, tmp_path, ["rechnen.py", "README.md"])
    teil = berechne_route(repo.as_posix(), "sitzung", files=["rechnen.py"])
    assert teil["beleg_gueltig"] is True


def test_teilmenge_nach_weiterer_aenderung_ungueltig(repo: Path, tmp_path: Path) -> None:
    _aendern(repo)
    _beleg_schreiben(repo, tmp_path, ["rechnen.py", "README.md"])
    (repo / "rechnen.py").write_text("def eins():\n    return 3\n", encoding="utf-8")
    teil = berechne_route(repo.as_posix(), "sitzung", files=["rechnen.py"])
    assert teil["beleg_gueltig"] is False
