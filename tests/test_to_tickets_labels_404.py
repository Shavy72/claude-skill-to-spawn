"""Tests für #404: Einrichter legt ``ready-for-agent`` und ``checkpoint:human`` idempotent an.

Prüft in einem frischen Wegwerf-Repo (echtes Git, gestellte ``gh``-CLI über
``TO_SPAWN_GH_STUB`` mit Zustand in einer JSON-Datei):
1. Beide Labels fehlen → erster Einrichter-Lauf legt beide ohne ``--force`` an,
   zweiter Lauf ruft kein ``label create`` mehr auf und ändert nichts.
2. Ein bestehendes ``checkpoint:human`` bleibt unverändert, nur ``ready-for-agent``
   wird ergänzt; ``--pruefen`` meldet vorher Abweichung (Exit 3) und danach Exit 0.
3. Ein eigenes ``regularien.checkpoint_label`` wird zusammen mit ``ready-for-agent``
   angelegt.
4. ``kette/skills/to-tickets/SKILL.md`` ruft ``to_spawn.py einrichten`` vor dem
   ersten Issue-Anlegen auf.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from to_spawn import config, einrichten

SKILL = Path(__file__).resolve().parents[1]
TO_TICKETS = SKILL / "kette" / "skills" / "to-tickets" / "SKILL.md"


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ort,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _baue_label_stub(tmp_path: Path, zustand_datei: Path) -> Path:
    """``gh``-Attrappe mit echtem Label-Zustand (``api`` + ``label create``)."""
    stub = tmp_path / "gh_label_stub.py"
    stub.write_text(
        f"""
import json
import sys
import urllib.parse
from pathlib import Path

DATEI = Path({str(zustand_datei)!r})
args = sys.argv[1:]
daten = json.loads(DATEI.read_text(encoding="utf-8"))
daten["aufrufe"].append(args)

if args[:1] == ["api"] and len(args) >= 2 and "/labels/" in args[1]:
    name = urllib.parse.unquote(args[1].rsplit("/labels/", 1)[1])
    DATEI.write_text(json.dumps(daten), encoding="utf-8")
    sys.exit(0 if name in daten["labels"] else 1)

if args[:2] == ["label", "create"] and len(args) >= 3:
    name = args[2]
    farbe = args[args.index("--color") + 1] if "--color" in args else ""
    beschreibung = args[args.index("--description") + 1] if "--description" in args else ""
    daten["creates"].append(args)
    if name in daten["labels"] and "--force" not in args:
        DATEI.write_text(json.dumps(daten), encoding="utf-8")
        sys.exit(1)
    daten["labels"][name] = {{"color": farbe, "description": beschreibung}}
    DATEI.write_text(json.dumps(daten), encoding="utf-8")
    sys.exit(0)

DATEI.write_text(json.dumps(daten), encoding="utf-8")
sys.exit(0)
""".lstrip(),
        encoding="utf-8",
    )
    return stub


def _frisches_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    labels: dict[str, dict[str, str]] | None = None,
) -> tuple[Path, Path]:
    ort = tmp_path / "frisch"
    ort.mkdir()
    _git(ort, "init", "-q", "-b", "main")
    _git(ort, "config", "user.email", "t@t")
    _git(ort, "config", "user.name", "t")
    _git(ort, "remote", "add", "origin", "https://github.com/test-org/frisch.git")
    (ort / "README.md").write_text("frisch\n", encoding="utf-8")
    _git(ort, "add", ".")
    _git(ort, "commit", "-q", "-m", "start")
    zustand = tmp_path / "gh_zustand.json"
    zustand.write_text(
        json.dumps({"labels": dict(labels or {}), "aufrufe": [], "creates": []}),
        encoding="utf-8",
    )
    stub = _baue_label_stub(tmp_path, zustand)
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    return ort, zustand


def _lies_zustand(datei: Path) -> dict[str, Any]:
    return json.loads(datei.read_text(encoding="utf-8"))


def _lauf(repo: Path, pruefen: bool = False) -> tuple[int, str]:
    ausgabe = io.StringIO()
    code = einrichten.einrichten(repo, pruefen=pruefen, ausgabe=ausgabe)
    return code, ausgabe.getvalue()


def test_frisches_repo_legt_beide_labels_idempotent_an(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#404 Abnahme: frisches Repo ohne Labels → beide entstehen, zweiter Lauf ändert nichts."""
    repo, zustand_datei = _frisches_repo(tmp_path, monkeypatch)

    code, text = _lauf(repo)
    assert code == 0, text
    assert "checkpoint_label: angelegt" in text, text

    stand_1 = _lies_zustand(zustand_datei)
    assert "ready-for-agent" in stand_1["labels"], stand_1["labels"]
    assert "checkpoint:human" in stand_1["labels"], stand_1["labels"]
    assert len(stand_1["creates"]) == 2, stand_1["creates"]
    assert all("--force" not in c for c in stand_1["creates"]), stand_1["creates"]

    # Zweiter Lauf: beide Labels existieren schon — kein weiteres create, stand.json unverändert.
    stand_json = repo / einrichten.STAND_PFAD
    mtime_vorher = stand_json.stat().st_mtime_ns
    code2, text2 = _lauf(repo)
    assert code2 == 0, text2
    assert "checkpoint_label: da_aktuell" in text2, text2
    assert stand_json.stat().st_mtime_ns == mtime_vorher
    stand_2 = _lies_zustand(zustand_datei)
    assert len(stand_2["creates"]) == 2, stand_2["creates"]

    code_pruef, text_pruef = _lauf(repo, pruefen=True)
    assert code_pruef == 0, text_pruef
    assert "ready-for-agent" in text_pruef and "checkpoint:human" in text_pruef


def test_vorhandenes_checkpoint_label_bleibt_unveraendert_ready_wird_ergaenzt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ist ``checkpoint:human`` schon da, bleibt Farbe/Beschreibung erhalten; ``ready-for-agent`` wird ergänzt."""
    eigen = {"color": "112233", "description": "Eigene Beschreibung"}
    repo, zustand_datei = _frisches_repo(
        tmp_path, monkeypatch, labels={"checkpoint:human": eigen}
    )

    code_vorher, text_vorher = _lauf(repo, pruefen=True)
    assert code_vorher == einrichten.EXIT_ABWEICHUNG, text_vorher
    assert "ready-for-agent" in text_vorher, text_vorher

    code, text = _lauf(repo)
    assert code == 0, text
    stand = _lies_zustand(zustand_datei)
    assert stand["labels"]["checkpoint:human"] == eigen
    assert "ready-for-agent" in stand["labels"]
    assert len(stand["creates"]) == 1, stand["creates"]
    assert stand["creates"][0][2] == "ready-for-agent"

    code_danach, text_danach = _lauf(repo, pruefen=True)
    assert code_danach == 0, text_danach


def test_angepasstes_checkpoint_label_und_ready_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eigenes ``regularien.checkpoint_label`` aus der Repo-Konfig wird zusammen mit ``ready-for-agent`` angelegt."""
    repo, zustand_datei = _frisches_repo(tmp_path, monkeypatch)
    konfig_pfad = repo / config.KONFIG_PFAD
    konfig_pfad.parent.mkdir(parents=True)
    konfig_pfad.write_text(
        json.dumps({"regularien": {"checkpoint_label": "abnahme:nutzer"}}),
        encoding="utf-8",
    )

    code, text = _lauf(repo)
    assert code == 0, text
    stand = _lies_zustand(zustand_datei)
    assert set(stand["labels"]) == {"abnahme:nutzer", "ready-for-agent"}


def test_to_tickets_skill_ruft_einrichten_vor_erstem_issue_auf() -> None:
    """``kette/skills/to-tickets/SKILL.md`` ruft ``to_spawn.py einrichten`` vor dem ersten Anlegen auf."""
    text = TO_TICKETS.read_text(encoding="utf-8")
    schritt_5 = text[text.index("### 5.") :]
    assert "to_spawn.py einrichten" in schritt_5, "to_spawn.py einrichten fehlt in Schritt 5"
    assert schritt_5.index("to_spawn.py einrichten") < schritt_5.index(
        "Issue im Repo dieses Arbeitsordners anlegen"
    ), "einrichten muss vor dem ersten Issue-Anlegen stehen"
