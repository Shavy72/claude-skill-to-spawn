"""#279 P3: capo und Wächter-Takt fragen ``ticket_zustand`` statt selbst „offen/zu“ zu raten.

(1) Ein fertig gebautes Ticket (offen + Staging-Label) ist nie „Session verwaist“.
(2) Der Wächter-Takt reicht Fragen fertig gebauter Tickets nicht als offene Fragen weiter
    und meldet sie nicht als „neu geschlossen“ (das gilt erst dem echten Schluss).
(3) Kaputte oder fehlende Antwort der Zustands-Abfrage: kein Absturz, kein Alarm —
    der Takt endet mit Log-/FEHLER-Zeile, Claude startet nicht, Merker bleibt.

Welten wie in ``test_waechter_213`` (echtes Git + ``skripte/capo.py``, gestellt nur ``gh``)
und ``test_waechter_takt_316`` (echter ``to_spawn.py takt``, echte gh-Aufzeichnung von #293).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from test_waechter_213 import (  # noqa: F401 — ``welt`` ist ein Fixture
    _capo,
    _gh_setzen,
    _iso,
    _kommentare,
    _mails,
    _text,
    welt,
)
from test_waechter_takt_316 import (
    CLAUDE_AUFZ,
    FIXTURE,
    HILFEN,
    _aufrufe,
    _frage,
    _git,
    _merker,
    _takt,
)

from to_spawn import capo, ticket_zustand

FERTIG = ticket_zustand.STAGING_FERTIG_LABEL


# --- (1) capo: fertig gebaut ist nie verwaist ------------------------------------------------


def test_regel_verwaist_fertig_gebaut_nie_verwaist() -> None:
    jetzt = datetime.now(timezone.utc)
    alt = (jetzt - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    issue: dict[str, Any] = {
        "state": "open",
        "assignees": [{"login": "bau"}],
        "updated_at": alt,
    }
    # Gegenprobe: ohne Label ist dasselbe Ticket verwaist.
    assert capo.regel_verwaist(5, issue, [], [], None, 3.0, jetzt) is not None
    fertig = {**issue, "labels": [{"name": FERTIG}]}
    assert capo.regel_verwaist(5, fertig, [], [], None, 3.0, jetzt) is None


def test_capo_meldet_fertig_gebautes_ticket_nicht_als_verwaist(
    welt: dict[str, Path],  # noqa: F811
) -> None:
    _gh_setzen(
        welt,
        "902",
        assignees=["bau"],
        updated_at=_iso(timedelta(hours=5)),
        labels=[FERTIG],
    )
    lauf = _capo(welt)
    assert "session_verwaist" not in lauf.stdout, _text(lauf)
    assert _kommentare(welt, "902") == []
    assert _mails(welt) == []


# --- (2) Wächter-Takt ------------------------------------------------------------------------


@pytest.fixture
def takt_welt(tmp_path: Path) -> dict[str, Any]:
    """Wie ``test_waechter_takt_316.welt``, aber mit eigener Kopie der gh-Aufzeichnung."""
    aufz = tmp_path / "aufzeichnung"
    shutil.copytree(FIXTURE, aufz)
    repo = tmp_path / "repo"
    ziel = repo / "docs" / "agents" / "bau_log"
    ziel.mkdir(parents=True)
    for datei in (FIXTURE / "bau_log").glob("*.jsonl"):
        shutil.copy(datei, ziel / datei.name)
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps({"hauptzweig": "master", "wt_basis": str(tmp_path / "wts")}),
        encoding="utf-8",
    )
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "add", "docs")
    _git(repo, "commit", "-q", "-m", "bau-logs #293")
    _git(
        repo, "remote", "add", "origin", (tmp_path / "fern" / "o" / "r.git").as_posix()
    )
    _git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    env = dict(os.environ)
    env.update(
        {
            "TO_SPAWN_LEITSTAND_ORDNER": str(tmp_path / "leitstand"),
            "TO_SPAWN_WAECHTER_ORDNER": str(tmp_path / "waechter"),
            "TO_SPAWN_GH_STUB": str(HILFEN / "gh_aufzeichnung_316.py"),
            "GH_AUFZEICHNUNG_ORDNER": str(aufz),
            "CLAUDE_AUFZEICHNUNG": str(tmp_path / "claude_aufruf.jsonl"),
            "CLAUDE_AUFZEICHNUNG_SPEC": "293",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return {
        "repo": repo,
        "env": env,
        "tmp": tmp_path,
        "aufz": aufz / "sub_issues_293.json",
    }


def _label_297(takt_welt: dict[str, Any]) -> None:
    datei: Path = takt_welt["aufz"]
    daten = json.loads(datei.read_text(encoding="utf-8"))
    for issue in daten:
        if issue["number"] == 297:
            issue["labels"].append({"name": FERTIG})
    datei.write_text(json.dumps(daten), encoding="utf-8")


def _merker_setzen(takt_welt: dict[str, Any], merker: dict[str, Any]) -> None:
    ordner = Path(takt_welt["env"]["TO_SPAWN_LEITSTAND_ORDNER"])
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "zustand.json").write_text(
        json.dumps({"takt": {"293": merker}}), encoding="utf-8"
    )


def test_takt_erster_lauf_frage_am_fertig_gebauten_ticket_geht_nicht_an_claude(
    takt_welt: dict[str, Any],
) -> None:
    _label_297(takt_welt)
    _frage(takt_welt, 297, "Frage am fertig gebauten Ticket")
    probe = _takt(takt_welt, "--dry-run")
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "Frage am fertig gebauten Ticket" not in probe.stdout, probe.stdout
    assert "0 zu entscheiden" in probe.stdout, probe.stdout


def test_takt_fertig_gebaut_ist_nicht_neu_geschlossen(
    takt_welt: dict[str, Any],
) -> None:
    _label_297(takt_welt)
    alt = {"zeilen": [], "zu": [296, 298, 299, 300], "verstoesse": [], "fertig": False}
    _merker_setzen(takt_welt, alt)
    lauf = _takt(takt_welt, "--claude", str(CLAUDE_AUFZ))
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    prompts = " ".join(a["stdin"] for a in _aufrufe(takt_welt))
    assert "#297 neu geschlossen" not in prompts + lauf.stdout
    assert 297 not in ((_merker(takt_welt) or {}).get("zu") or [])


# --- (3) Fehlerfall der Zustands-Abfrage ----------------------------------------------------


@pytest.mark.parametrize(
    "inhalt",
    [
        None,  # gh weg: Aufzeichnung fehlt → Exit 1
        "kein json",
        json.dumps({"message": "Not Found"}),
        json.dumps([{"title": "ohne Nummer und Zustand"}]),
        json.dumps([{"number": "abc", "state": "open"}]),
        json.dumps(["nur Text"]),
    ],
)
def test_takt_kaputte_zustands_abfrage_kein_absturz_kein_alarm(
    takt_welt: dict[str, Any], inhalt: str | None
) -> None:
    datei: Path = takt_welt["aufz"]
    if inhalt is None:
        datei.unlink()
    else:
        datei.write_text(inhalt, encoding="utf-8")
    alt = {"zeilen": ["x"], "zu": [296], "verstoesse": [], "fertig": False}
    _merker_setzen(takt_welt, alt)
    lauf = _takt(takt_welt, "--claude", str(CLAUDE_AUFZ))
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert "Traceback" not in lauf.stderr, lauf.stderr
    assert "FEHLER" in lauf.stdout and "nicht lesbar" in lauf.stdout, lauf.stdout
    assert not _aufrufe(takt_welt)  # kein Claude, also keine Meldung an den Nutzer
    assert _merker(takt_welt) == alt


def test_capo_kaputte_zustands_abfrage_kein_absturz(tmp_path: Path) -> None:
    """``capo.kinder`` liefert bei kaputten Einträgen ``None`` statt halber Liste."""
    stub = tmp_path / "gh_kaputt.py"
    stub.write_text('import sys\nprint(\'[{"title": "x"}]\')\n', encoding="utf-8")
    env = {**os.environ, "TO_SPAWN_GH_STUB": str(stub)}
    code = (
        "from to_spawn import capo\nassert capo.kinder('o/r', 1) is None\nprint('ok')\n"
    )
    lauf = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert lauf.stdout.strip() == "ok", lauf.stdout + lauf.stderr


# --- (4) Fix-Runde PR 302: duplicate gilt als verworfen, eine Regel im Baustein ---------------


def _schliessen_297(takt_welt: dict[str, Any], grund: str) -> None:
    datei: Path = takt_welt["aufz"]
    daten = json.loads(datei.read_text(encoding="utf-8"))
    for issue in daten:
        if issue["number"] == 297:
            issue["state"] = "closed"
            issue["state_reason"] = grund
    datei.write_text(json.dumps(daten), encoding="utf-8")


@pytest.mark.parametrize(
    ("grund", "meldet"),
    [("duplicate", False), ("not_planned", False), ("completed", True)],
)
def test_takt_neu_geschlossen_nur_bei_echtem_schluss(
    takt_welt: dict[str, Any], grund: str, meldet: bool
) -> None:
    """Verworfen (nicht geplant, Duplikat) hat keine Belegseite — keine Meldung."""
    _schliessen_297(takt_welt, grund)
    alt = {"zeilen": [], "zu": [296, 298, 299, 300], "verstoesse": [], "fertig": False}
    _merker_setzen(takt_welt, alt)
    lauf = _takt(takt_welt, "--claude", str(CLAUDE_AUFZ))
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    prompts = " ".join(a["stdin"] for a in _aufrufe(takt_welt))
    assert ("#297 neu geschlossen" in prompts + lauf.stdout) is meldet, lauf.stdout
    assert (297 in ((_merker(takt_welt) or {}).get("zu") or [])) is meldet


def test_capo_checkpoint_blocker_fertig_gebaut_gibt_frei(
    welt: dict[str, Path],  # noqa: F811
) -> None:
    """Blocker offen, aber grün auf Staging: Checkpoint läuft wie bei geschlossenem Blocker."""
    from test_capo_checkpoint_285 import (
        FRAGE,
        VORSCHLAG,
        _checkpoint_stellen,
        _kommentar,
    )
    from test_waechter_213 import _gh_zustand

    _checkpoint_stellen(welt)
    daten = _gh_zustand(welt)
    daten.setdefault("blocker", {})["902"] = [
        {"number": 428, "state": "closed"},
        {"number": 429, "state": "open", "labels": [{"name": FERTIG}]},
    ]
    welt["gh"].write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    _kommentar(welt, "902", "bau-bot", f"{FRAGE}\n\n{VORSCHLAG}", 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "#902 Checkpoint-Annahme" in ergebnis.stdout, _text(ergebnis)


def test_capo_kinder_ohne_liste_ist_none(tmp_path: Path) -> None:
    """Zweiter Fall: GitHub-Fehlerobjekt statt Liste → ``None``, kein Absturz."""
    stub = tmp_path / "gh_fehler.py"
    stub.write_text("print('{\"message\": \"Not Found\"}')\n", encoding="utf-8")
    env = {**os.environ, "TO_SPAWN_GH_STUB": str(stub)}
    code = "from to_spawn import capo\nassert capo.kinder('o/r', 1) is None\nprint('ok')\n"
    lauf = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert lauf.stdout.strip() == "ok", lauf.stdout + lauf.stderr
