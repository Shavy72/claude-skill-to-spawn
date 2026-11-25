"""Datei-Größe: Code-Dateien, die über die Grenze wachsen, landen in `datei_groesse` (VERTRAG §4)."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

REVIEW = Path(__file__).resolve().parent.parent
if str(REVIEW) not in sys.path:
    sys.path.insert(0, str(REVIEW))

from _gemeinsam import berechne_route  # noqa: E402
from review_hilfen import git  # noqa: E402


def schreiben(repo: Path, pfad: str, zeilen: int) -> None:
    (repo / pfad).write_text("".join(f"x_{i} = {i}\n" for i in range(zeilen)), encoding="utf-8")


def committen(repo: Path, pfad: str, zeilen: int) -> None:
    schreiben(repo, pfad, zeilen)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"{pfad} {zeilen}")


def groesse(repo: Path, **kw: object) -> list[dict[str, object]]:
    return berechne_route(str(repo), "ticket", **kw)["datei_groesse"]  # type: ignore[arg-type]


def test_ueber_1000(repo: Path) -> None:
    committen(repo, "gross.py", 990)
    schreiben(repo, "gross.py", 1010)
    assert groesse(repo) == [{"datei": "gross.py", "vorher": 990, "nachher": 1010, "art": "ueber_1000"}]


def test_waechst_gross(repo: Path) -> None:
    committen(repo, "gross.py", 1200)
    schreiben(repo, "gross.py", 1400)
    assert groesse(repo) == [{"datei": "gross.py", "vorher": 1200, "nachher": 1400, "art": "waechst_gross"}]


def test_kleiner_zuwachs_still(repo: Path) -> None:
    committen(repo, "gross.py", 1200)
    schreiben(repo, "gross.py", 1250)
    assert groesse(repo) == []


def test_testdatei_still(repo: Path) -> None:
    committen(repo, "test_gross.py", 990)
    schreiben(repo, "test_gross.py", 1100)
    assert groesse(repo) == []


def test_neue_datei_staged(repo: Path) -> None:
    schreiben(repo, "neu.py", 1100)
    git(repo, "add", "neu.py")
    assert groesse(repo, staged=True) == [{"datei": "neu.py", "vorher": 0, "nachher": 1100, "art": "ueber_1000"}]


def test_neue_datei_untracked_files(repo: Path) -> None:
    schreiben(repo, "neu.py", 1100)
    assert groesse(repo, files=["neu.py"]) == [{"datei": "neu.py", "vorher": 0, "nachher": 1100, "art": "ueber_1000"}]


def test_base_modus(repo: Path) -> None:
    committen(repo, "gross.py", 990)
    git(repo, "checkout", "-q", "-b", "zweig")
    committen(repo, "gross.py", 1010)
    base = git(repo, "rev-parse", "HEAD~1").strip()
    assert groesse(repo, base=base) == [{"datei": "gross.py", "vorher": 990, "nachher": 1010, "art": "ueber_1000"}]


def test_config_override(repo: Path, umgebung: Callable[..., Path]) -> None:
    config = umgebung()
    inhalt = json.loads(config.read_text(encoding="utf-8"))
    inhalt["datei_groesse"] = {"grenze": 500, "zuwachs": 10}
    config.write_text(json.dumps(inhalt), encoding="utf-8")
    committen(repo, "a.py", 400)
    committen(repo, "b.py", 600)
    schreiben(repo, "a.py", 600)
    schreiben(repo, "b.py", 620)
    assert groesse(repo) == [
        {"datei": "a.py", "vorher": 400, "nachher": 600, "art": "ueber_1000"},
        {"datei": "b.py", "vorher": 600, "nachher": 620, "art": "waechst_gross"},
    ]
