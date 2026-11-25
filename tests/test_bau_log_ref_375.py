"""Bau-Log-Leser vom Ref und Entscheidungs-Rückfall in ``bau_log`` (#375, Folge #364).

Prüft:
- ``bau_log.haupt_ref`` und ``bau_log.log_vom_ref`` lesen Refs ohne Import von ``capo``
  (mit Zeitgrenze aus ``gh.GIT_ZEIT_S``).
- ``briefkasten`` importiert ``capo`` weder statisch noch zur Laufzeit.
- ``bau_log.entscheidung_felder`` ist die einzige Stelle für den Rückfall alter Felder
  (``text`` → Frage, ``entscheidungen`` → Wahl); ``briefkasten.eintrag_aus_bau_log``
  und ``capo.entscheidung_teile`` nutzen beide diese Funktion.
"""

from __future__ import annotations

import ast
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import bau_log, briefkasten, capo, gh

SKILL = Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _init_repo(repo: Path, zweig: str = "main") -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", zweig)
    return repo


def _importiert_capo(datei: Path) -> list[str]:
    """Alle Stellen in ``datei``, an denen ``capo`` importiert wird (auch innerhalb von Funktionen)."""
    baum = ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
    treffer: list[str] = []
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            for alias in knoten.names:
                if alias.name == "capo" or alias.name.endswith(".capo"):
                    treffer.append(f"Z.{knoten.lineno}: import {alias.name}")
        elif isinstance(knoten, ast.ImportFrom):
            modul = knoten.module or ""
            if modul == "capo" or modul.endswith(".capo"):
                treffer.append(f"Z.{knoten.lineno}: from {modul} import ...")
            for alias in knoten.names:
                if alias.name == "capo":
                    treffer.append(f"Z.{knoten.lineno}: from {modul} import capo")
    return treffer


# --- 1. Keine Abhängigkeit auf capo -------------------------------------------


def test_briefkasten_und_bau_log_importieren_capo_nicht() -> None:
    assert _importiert_capo(SKILL / "to_spawn" / "briefkasten.py") == []
    assert _importiert_capo(SKILL / "to_spawn" / "bau_log.py") == []


def test_briefkasten_liste_laedt_capo_nicht_zur_laufzeit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    log_datei = repo / bau_log.log_im_repo(repo, 7)
    log_datei.parent.mkdir(parents=True, exist_ok=True)
    zeile = {
        "typ": "entscheidung",
        "ts": "2026-11-25T10:00:00+00:00",
        "frage": "Speicher?",
        "wahl": "SQLite",
        "grund": "einfach",
    }
    log_datei.write_text(json.dumps(zeile, ensure_ascii=False) + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "log")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "rm", "-q", str(log_datei))
    _git(repo, "commit", "-q", "-m", "ohne log")

    skript = (
        "import sys; from pathlib import Path; "
        "from to_spawn import briefkasten; "
        f"erg = briefkasten.liste(['7'], hauptbaum=Path({str(repo)!r})); "
        "assert [e['was'] for e in erg] == ['Speicher? → SQLite'], erg; "
        "assert 'to_spawn.capo' not in sys.modules, 'capo wurde unerwartet geladen'"
    )
    lauf = subprocess.run(
        [sys.executable, "-c", skript],
        cwd=SKILL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stderr or lauf.stdout


# --- 2. bau_log.haupt_ref und bau_log.log_vom_ref -----------------------------


def test_bau_log_haupt_ref_vorhanden_und_fehlend(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "leer")
    assert bau_log.haupt_ref(repo) is None

    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert bau_log.haupt_ref(repo) == "origin/main"


def test_bau_log_log_vom_ref_liest_ref_und_warnt_kaputte_zeile(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / ".to-spawn").mkdir(parents=True)
    (repo / ".to-spawn" / "config.json").write_text(json.dumps({"pfade": {"bau_log": "eigen/logs"}}), encoding="utf-8")
    log_datei = repo / "eigen" / "logs" / "42.jsonl"
    log_datei.parent.mkdir(parents=True)
    log_datei.write_text(json.dumps({"typ": "session_start"}) + "\n{kaputt\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "log")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "rm", "-q", str(log_datei))
    _git(repo, "commit", "-q", "-m", "weg")

    with caplog.at_level(logging.WARNING, logger="to_spawn.bau_log"):
        zeilen = bau_log.log_vom_ref(repo, "origin/main", 42)
    assert zeilen == [{"typ": "session_start"}, {"typ": "kaputt"}]
    assert "Kaputte Bau-Log-Zeile" in caplog.text
    assert bau_log.log_vom_ref(repo, "origin/main", 999) == []


def test_bau_log_ref_nutzt_git_zeitgrenze_und_faellt_sicher_zurueck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gesehen: list[tuple[list[str], float | None]] = []

    def fake_git(args: list[str], cwd: Path, timeout: float | None = None) -> tuple[int, str]:
        gesehen.append((args, timeout))
        return 124, ""

    monkeypatch.setattr(gh, "_git_ausgabe", fake_git)
    monkeypatch.setattr(gh, "hauptzweig", lambda _r: "main")
    assert bau_log.haupt_ref(tmp_path) is None
    assert bau_log.log_vom_ref(tmp_path, "origin/main", 7) == []
    assert len(gesehen) == 2
    assert all(t == gh.GIT_ZEIT_S for _, t in gesehen)


# --- 3. Rückfall alter Felder an einer Stelle (bau_log.entscheidung_felder) ---


def test_entscheidung_felder_neu_und_rueckfall() -> None:
    assert bau_log.entscheidung_felder({"frage": "NeuF", "wahl": "NeuW", "grund": "G"}) == ("NeuF", "NeuW", "G")
    assert bau_log.entscheidung_felder({"text": "AltF", "entscheidungen": "AltW", "grund": "AltG"}) == (
        "AltF",
        "AltW",
        "AltG",
    )
    assert bau_log.entscheidung_felder({"frage": "NeuF", "text": "AltF", "wahl": "NeuW", "entscheidungen": "AltW"}) == (
        "NeuF",
        "NeuW",
        "",
    )
    assert bau_log.entscheidung_felder({}) == ("", "", "")


def test_briefkasten_und_capo_nutzen_dieselbe_entscheidung_felder_funktion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alt_zeile = {
        "typ": "entscheidung",
        "ts": "2026-11-25T10:00:00+00:00",
        "text": "Alte Frage?",
        "entscheidungen": "Alte Wahl",
        "grund": "Alter Grund",
    }
    eintrag = briefkasten.eintrag_aus_bau_log(7, alt_zeile)
    assert eintrag["was"] == "Alte Frage? → Alte Wahl"
    assert eintrag["warum"] == "Alter Grund"
    assert capo.entscheidung_teile(alt_zeile) == ("Alte Frage?", "Alte Wahl", "Alter Grund")

    # Beide Aufrufer müssen tatsächlich über bau_log.entscheidung_felder laufen.
    aufrufe: list[dict[str, object]] = []

    def spion(z: dict[str, object]) -> tuple[str, str, str]:
        aufrufe.append(z)
        return ("SpionF", "SpionW", "SpionG")

    monkeypatch.setattr(bau_log, "entscheidung_felder", spion)
    assert briefkasten.eintrag_aus_bau_log(7, alt_zeile)["was"] == "SpionF → SpionW"
    assert capo.entscheidung_teile(alt_zeile) == ("SpionF", "SpionW", "SpionG")
    assert len(aufrufe) == 2


def test_capo_weiterleitung_delegiert_an_bau_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bau_log, "haupt_ref", lambda _r: "origin/spezial")
    monkeypatch.setattr(bau_log, "log_vom_ref", lambda _r, ref, t: [{"ref": ref, "t": t}])
    assert capo.haupt_ref(tmp_path) == "origin/spezial"
    assert capo.log_vom_ref(tmp_path, "origin/spezial", 12) == [{"ref": "origin/spezial", "t": 12}]
