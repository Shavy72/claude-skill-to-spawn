"""#424: Keine privaten Projektwörter des Review-Werkzeugs außerhalb von ``review/``.

Folge aus #395 / #403: ``test_review_quelle_329.py`` prüft ``review/``; dieser Test
prüft den gesamten übrigen getrackten Repo-Stand gegen dieselbe private Wortliste
(``PROJEKTWOERTER`` aus ``~/.claude/review/config.json``) — ohne die Wörter selbst im
öffentlichen Repo zu nennen. Die Mechanik-Tests laufen auch ohne private Config (z. B. CI).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_review_quelle_329 import PROJEKTWOERTER, name_muster

from conftest import repo_dateien
from to_spawn import inventur

SKILL = Path(__file__).resolve().parents[1]


def projektwoerter_funde_ausserhalb_review(wurzel: Path, woerter: frozenset[str]) -> list[str]:
    """Alle ``datei:zeile``-Treffer außerhalb von ``review/``; nennt das getroffene Wort nie."""
    if not woerter:
        return []
    muster = name_muster(woerter)
    funde: list[str] = []
    for pfad in repo_dateien(wurzel):
        rel = pfad.relative_to(wurzel).as_posix()
        if rel.startswith("review/"):
            continue
        try:
            roh = pfad.read_bytes()
        except OSError:
            continue
        if b"\x00" in roh:
            continue
        text = roh.decode("utf-8", errors="replace")
        for nr, zeile in enumerate(text.splitlines(), 1):
            if muster.search(zeile):
                funde.append(f"{rel}:{nr}")
    return funde


@pytest.mark.skipif(not PROJEKTWOERTER, reason="keine privaten Projektwörter in ~/.claude/review/")
def test_repo_ausserhalb_review_ohne_projektwoerter() -> None:
    """#424: Projektwörter der privaten Config stehen nirgends im Repo außerhalb von ``review/``."""
    funde = projektwoerter_funde_ausserhalb_review(SKILL, PROJEKTWOERTER)
    assert funde == [], f"Projektwort aus der privaten Config außerhalb von review/: {funde}"


def test_mechanik_meldet_neues_projektwort_ausserhalb_review(tmp_path: Path) -> None:
    """Mechanik-Test mit Kunstwörtern: erkennt neue Treffer außerhalb ``review/``, ignoriert ``review/``."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "to_spawn").mkdir()
    (tmp_path / "tests" / "hilfen").mkdir(parents=True)
    (tmp_path / "review").mkdir()

    (tmp_path / "docs" / "beispiel.md").write_text("Sauber\nDienst: funkmast_api\n", encoding="utf-8")
    (tmp_path / "to_spawn" / "modul.py").write_text("# lagerhalle_db.DB_PATH\nx = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "hilfen" / "log.jsonl").write_text(
        '{"text": "ok"}\n{"text": "Funkmast_API"}\n', encoding="utf-8"
    )
    (tmp_path / "review" / "eigene.md").write_text("funkmast_api\n", encoding="utf-8")
    (tmp_path / "docs" / "bin.dat").write_bytes(b"\x00funkmast_api\x00")

    funde = projektwoerter_funde_ausserhalb_review(tmp_path, frozenset({"funkmast_api", "lagerhalle_db"}))
    assert funde == [
        "docs/beispiel.md:2",
        "tests/hilfen/log.jsonl:2",
        "to_spawn/modul.py:1",
    ]


def test_inventur_liest_extra_projektwoerter_aus_konfig(tmp_path: Path) -> None:
    """#424: Zusätzliche Projekt-Wörter aus privater Review-Config oder Repo-Config ordnen unter ``projekt`` ein."""
    repo = tmp_path / "beispiel-repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"funkmast": {}, "kranwerk": {}}}),
        encoding="utf-8",
    )
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps({"inventur": {"projekt_woerter": ["kranwerk"]}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)

    heim = tmp_path / "heim"
    (heim / ".claude" / "skills").mkdir(parents=True)
    (heim / ".claude" / "review").mkdir(parents=True)
    (heim / ".claude" / "review" / "config.json").write_text(
        json.dumps({"projekt_woerter": {"kosten": ["funkmast"]}}),
        encoding="utf-8",
    )
    (heim / ".claude.json").write_text("{}", encoding="utf-8")

    inv = inventur.erstelle(
        repo,
        claude_home=heim / ".claude",
        home_json=heim / ".claude.json",
        home=heim,
        environ={"PATH": ""},
    )
    je_name = {w.name: w for w in inv.werkzeuge}
    assert je_name["funkmast"].kategorie == "projekt"
    assert je_name["kranwerk"].kategorie == "projekt"
