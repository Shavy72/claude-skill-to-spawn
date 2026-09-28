"""Tests für #285 — Wächter entscheidet Nacht-Checkpoints nach Doktrin.

Echt laufen: Git-Repo mit Bare-Origin, die CLIs ``skripte/capo.py``,
``to_spawn.py pruefen`` und ``skripte/bau.py``, echte Bau-Log-Dateien, echte
``docs/agents/entscheidungen_<S>.md``, echte Zustandsdatei. Gestellt ist nur
GitHub (``gh``-Ersatz aus #213/#206) und der Mail-Befehl (schreibt in eine Datei).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
import test_capo_folgerunde_284 as folge
import test_regularien_206 as reg
from test_capo_folgerunde_284 import tmux  # noqa: F401  (tmux = Fixture)
from test_regularien_206 import repo  # noqa: F401  (repo = Fixture)
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _capo,
    _gh_setzen,
    _gh_zustand,
    _iso,
    _konfig,
    _mails,
    _text,
    welt,
)

SKILL = Path(__file__).resolve().parent.parent
CHECKPOINT = "checkpoint:human"
FRAGE = "Frage: Soll der Abschalter sofort greifen?"
VORSCHLAG = "Vorschlag: sofort aus, weil die Doktrin Kosten vor Komfort stellt."


# --- Hilfen ------------------------------------------------------------------


def _kommentar(welt: dict[str, Path], nummer: str, autor: str, body: str, vor_min: float) -> None:
    """Einen Kommentar mit Autor und Zeitpunkt in den gestellten GitHub-Zustand legen."""
    daten = _gh_zustand(welt)
    daten.setdefault("kommentare", {}).setdefault(nummer, []).append(
        {
            "autor": autor,
            "body": body,
            "created_at": _iso(timedelta(minutes=vor_min)),
        }
    )
    welt["gh"].write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")


def _texte(welt: dict[str, Path], nummer: str) -> list[str]:
    roh = _gh_zustand(welt).get("kommentare", {}).get(nummer, [])
    return [e if isinstance(e, str) else str(e.get("body", "")) for e in roh]


def _labels(welt: dict[str, Path], nummer: str) -> list[str]:
    return list(_gh_zustand(welt)["issues"][nummer].get("labels") or [])


def _bau_log(welt: dict[str, Path], nummer: str) -> list[dict]:
    datei = welt["repo"] / ".to-spawn" / "bau_log" / f"{nummer}.jsonl"
    if not datei.is_file():
        return []
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z.strip()]


def _entscheidungen(welt: dict[str, Path]) -> str:
    datei = welt["repo"] / "docs" / "agents" / f"entscheidungen_{SPEC}.md"
    return datei.read_text(encoding="utf-8") if datei.is_file() else ""


def _checkpoint_stellen(welt: dict[str, Path], **zusatz: object) -> None:
    """#902 ist das offene Checkpoint-Ticket; #901 bleibt Ausgangsstand."""
    _konfig(welt, waechter={"karenz_minuten": 0, **zusatz})
    _gh_setzen(welt, "902", labels=[CHECKPOINT])


# --- a) 61 min ohne Antwort, Vorschlag da → Annahme --------------------------


def test_annahme_nach_frist_mit_vorschlag(welt: dict[str, Path]) -> None:
    _checkpoint_stellen(welt)
    _kommentar(welt, "902", "bau-bot", f"{FRAGE}\n\n{VORSCHLAG}", 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "#902 Checkpoint-Annahme" in ergebnis.stdout, _text(ergebnis)

    annahme = [t for t in _texte(welt, "902") if t.startswith("Wächter: Annahme nach")]
    assert len(annahme) == 1, _texte(welt, "902")
    assert "nach Doktrin — David kann kippen" in annahme[0]
    assert "sofort aus, weil die Doktrin" in annahme[0]

    zeilen = [z for z in _bau_log(welt, "902") if z.get("typ") == "entscheidung"]
    assert len(zeilen) == 1, _bau_log(welt, "902")
    assert "sofort aus" in zeilen[0]["wahl"] and zeilen[0]["grund"]

    text = _entscheidungen(welt)
    assert "| #902 |" in text and "sofort aus" in text, text
    assert _labels(welt, "902") == []
    assert "checkpoint_annahme" in [m.get("art") for m in _mails(welt)], _mails(welt)


# --- b) 30 min → nichts ------------------------------------------------------


def test_vor_der_frist_passiert_nichts(welt: dict[str, Path]) -> None:
    _checkpoint_stellen(welt)
    _kommentar(welt, "902", "bau-bot", f"{FRAGE}\n\n{VORSCHLAG}", 30)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "#902 Checkpoint wartet" in ergebnis.stdout, _text(ergebnis)
    assert [t for t in _texte(welt, "902") if t.startswith("Wächter:")] == []
    assert _labels(welt, "902") == [CHECKPOINT]
    assert _bau_log(welt, "902") == []
    assert _mails(welt) == []


# --- c) David hat geantwortet → nichts ---------------------------------------


def test_antwort_von_david_verhindert_annahme(welt: dict[str, Path]) -> None:
    _checkpoint_stellen(welt)
    _kommentar(welt, "902", "bau-bot", f"{FRAGE}\n\n{VORSCHLAG}", 90)
    _kommentar(welt, "902", "Shavy72", "Nein, erst am Morgen abschalten.", 10)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "hat geantwortet" in ergebnis.stdout, _text(ergebnis)
    assert [t for t in _texte(welt, "902") if t.startswith("Wächter: Annahme")] == []
    assert _labels(welt, "902") == [CHECKPOINT]
    assert _mails(welt) == []


# --- d) kein Vorschlag → keine Annahme, nur Meldung --------------------------


def test_ohne_vorschlag_keine_annahme_nur_meldung(welt: dict[str, Path]) -> None:
    _checkpoint_stellen(welt)
    _kommentar(welt, "902", "bau-bot", FRAGE, 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "kein Vorschlag" in ergebnis.stdout, _text(ergebnis)
    assert [t for t in _texte(welt, "902") if t.startswith("Wächter: Annahme")] == []
    assert _labels(welt, "902") == [CHECKPOINT]
    assert _bau_log(welt, "902") == []
    assert "checkpoint_offen" in [m.get("art") for m in _mails(welt)], _mails(welt)


# --- e) pruefen warnt bei Checkpoint in der blocked_by-Kette ------------------


def test_pruefen_warnt_bei_checkpoint_in_der_kette(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Origin als echte GitHub-URL: der Windows-Pfad des Bare-Remotes liefert keinen
    # owner/name-Slug (bekannter Windows-Stand von test_regularien_206).
    subprocess.run(
        ["git", "remote", "set-url", "origin", "https://github.com/test/wegwerf.git"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    reg._manifest_schreiben(repo, {t: reg._voll() for t in ("901", "902", "903")})
    monkeypatch.setenv("GH_STUB_BLOCKER", "902:901,903:902")
    reg._gh_daten(tmp_path, monkeypatch, {"902": {"labels": [CHECKPOINT]}})
    ergebnis = reg._cli(repo, "pruefen", str(reg.SPEC))
    # #325: Checkpoint mitten in der Kette ist seitdem eine Weigerung statt einer Warnung.
    assert ergebnis.returncode != 0, reg._ausgabe(ergebnis)
    assert "#902" in ergebnis.stdout and "#903" in ergebnis.stdout
    assert "Grill" in ergebnis.stdout, reg._ausgabe(ergebnis)


# --- f2) capo gibt der Folge-Runde ihren Auftrag mit -------------------------


def test_folge_runde_bekommt_auftrag_mit(welt: dict[str, Path], tmux: dict[str, Path]) -> None:
    folge._wieder_oeffnen_lassen(welt)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    starts = folge._starts(tmux)
    assert len(starts) == 1, folge._aufrufe(tmux)
    innen = starts[0][-1]
    assert "BAU_AUFTRAG=" in innen, innen
    assert "beweis_fehlt" in innen and "commit_ohne_nummer" in innen, innen
    assert innen.endswith("bau 901 --sofort"), innen


# --- f) bau --auftrag landet im Prompt ---------------------------------------


def test_auftrag_landet_im_prompt(tmp_path: Path) -> None:
    repo = tmp_path / "bau_repo"
    manifeste = repo / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    shutil.copy(SKILL / "repo-scripts" / "_default.json", manifeste / "_default.json")
    (manifeste / "spec-900.json").write_text(
        json.dumps({"spec": 900, "tickets": {"901": {"title": "Wegwerf"}}}),
        encoding="utf-8",
    )

    def lauf(*zusatz: str) -> str:
        fertig = subprocess.run(
            [
                sys.executable,
                str(SKILL / "skripte" / "bau.py"),
                "901",
                "--print-prompt",
                *zusatz,
            ],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "TO_SPAWN_REPO": str(repo)},
            timeout=120,
            check=False,
        )
        assert fertig.returncode == 0, fertig.stdout + fertig.stderr
        return fertig.stdout

    ohne = lauf()
    assert "## Auftrag dieser Runde" not in ohne
    mit = lauf("--auftrag", "beweis_fehlt — Belegseite fehlt")
    assert "## Auftrag dieser Runde" in mit
    assert "beweis_fehlt — Belegseite fehlt" in mit
    assert ohne.strip() in mit
