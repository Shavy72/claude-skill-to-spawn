"""Rückbau der Übergangsregel ``pruefplan.eingerichtet()`` (#451).

Nachdem das App-Repo ``pruefung.schnell``, ``pruefung.komplett`` und ``live.befehl`` trägt,
entfällt die Übergangsregel aus #425 Teil B: fehlende Gruppe ``pruefung`` ist kein „alter Ablauf“
mit Warn-Zeile mehr, sondern eine Einrichtungs-Lücke (Gate-Fehler beim Einreichen, Sperre vor
Komplett-Prüfung und Live-Schritt).
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import pytest

from to_spawn import config, einreichen, einrichtungs_ticket, komplett_lauf, live_tuer, pruefplan
from to_spawn.pruefplan import Moment


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
    )


def _python_repo_ohne_pruefung(ordner: Path) -> Path:
    """Kleines Python-Repo ohne Gruppe ``pruefung`` in ``.to-spawn/config.json``."""
    ordner.mkdir()
    (ordner / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (ordner / "rechnen.py").write_text("def plus(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    (ordner / "tests").mkdir()
    (ordner / "tests" / "test_rechnen.py").write_text(
        "def test_ok() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "base")
    _git(ordner, "tag", "base")
    (ordner / "neu.py").write_text("def neu() -> int:\n    return 1\n", encoding="utf-8")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "ticket")
    return ordner


def test_uebergangsregel_und_warnzeile_entfernt() -> None:
    """``pruefplan.eingerichtet`` und ``pruefplan.NICHT_EINGERICHTET`` existieren nicht mehr."""
    assert not hasattr(pruefplan, "eingerichtet")
    assert not hasattr(pruefplan, "NICHT_EINGERICHTET")


def test_einreichen_ohne_pruefung_ist_gate_fehler_ohne_warnzeile(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Ohne Gruppe ``pruefung`` läuft ``einreichen._pruefung`` strikt als ``schnell``."""
    repo = _python_repo_ohne_pruefung(tmp_path / "repo")
    with caplog.at_level(logging.WARNING), pytest.raises(einreichen._Rot) as rot:
        einreichen._pruefung(repo, "base")
    assert "Exit 2" in rot.value.text
    assert "pruefung.schnell" in rot.value.text
    zeilen = (repo / ".to-spawn" / "pruef" / "protokoll.jsonl").read_text(encoding="utf-8").splitlines()
    letzte = json.loads(zeilen[-1])
    assert letzte["urteil"] == "gate-fehler", letzte
    assert letzte["art"] == "schnell", letzte
    assert "warnung" not in letzte, letzte
    assert not any("alter Ablauf" in r.getMessage() for r in caplog.records)


def test_komplett_lauf_und_nur_lesen_ohne_pruefung_sperren(tmp_path: Path) -> None:
    """Ohne Gruppe ``pruefung`` melden ``komplett_einmal`` und ``nur_lesen`` Einrichtungs-Lücke."""
    repo = _python_repo_ohne_pruefung(tmp_path / "repo")
    konfig = config._mische(config.DEFAULTS, {"staging": {"url": "http://127.0.0.1:8001/", "ohne": False}})
    for moment in (Moment.ALLE_AUF_STAGING, Moment.VOR_LIVE):
        erg = komplett_lauf.komplett_einmal(repo, 451, moment, konfig)
        assert not erg.ok and not erg.frei, erg
        assert "pruefung.komplett" in erg.grund, erg
        probe = komplett_lauf.nur_lesen(repo, 451, moment, konfig)
        assert not probe.ok and not probe.frei, probe
        assert "pruefung.komplett" in probe.grund, probe
    assert komplett_lauf.lies_merker(repo, 451) is None


def test_live_tuer_ohne_pruefung_blockiert_live(tmp_path: Path) -> None:
    """Ohne Gruppe ``pruefung`` lässt die Live-Tür den Live-Befehl nicht starten."""
    repo = _python_repo_ohne_pruefung(tmp_path / "repo")
    konfig = config._mische(
        config.DEFAULTS,
        {"staging": {"ohne": True}, "live": {"befehl": "echo sollte-nicht-laufen"}},
    )
    code, zeilen = live_tuer.starte(repo, 451, konfig)
    text = "\n".join(zeilen)
    assert code == 1, text
    assert "pruefung.komplett" in text, text


def test_fehlende_gruppe_pruefung_ist_einrichtungs_luecke(tmp_path: Path) -> None:
    """Fehlende Gruppe ``pruefung`` taucht über ``einrichtungs_ticket.luecken`` als Lücke auf."""
    repo = _python_repo_ohne_pruefung(tmp_path / "repo")
    konfig = config._mische(config.DEFAULTS, {"staging": {"ohne": True}, "live": {"ohne": True}})
    fehlt = einrichtungs_ticket.luecken(repo, konfig)
    assert "pruefung.schnell" in fehlt
    assert "pruefung.komplett" in fehlt
