"""#496 Teil A: Die Bau-Ticket-Sperre der Komplett-Prüfung greift unabhängig vom Skript des Repos.

Echte App-Repos tragen in ``pruefung.komplett`` ein eigenes Skript, das von der Sperre nichts weiß
(anders als die Probe-Vorlage ``skripte/pruefen_komplett.py``, #474). Die Sperre sitzt darum an
EINER Stelle im Skill (:func:`to_spawn.komplett_lauf.bau_ticket_ablehnung`); jeder Skill-Weg, der
den Komplett-Befehl ausführt, fragt dort: ``komplett_lauf`` selbst und die Prüf-Tür
``pruef.pruefen(art="komplett")``.

Echt ohne Attrappen: git-Repo mit eigenem Shell-Skript als Komplett-Befehl, das jede Ausführung
in eine Zähldatei schreibt. Bau-Session = ``TO_SPAWN_TICKET`` gesetzt (wie ``bau.py`` es setzt).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from to_spawn import config, komplett_lauf, pruef
from to_spawn.pruef import leiter
from to_spawn.pruefplan import Moment

SPEC = 496


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
    )


def _app_repo(tmp_path: Path, zaehl: Path) -> Path:
    """App-Repo, dessen Komplett-Befehl ein eigenes Skript ohne jede Sperre ist."""
    repo = tmp_path / "app"
    (repo / "skripte").mkdir(parents=True)
    (repo / "skripte" / "alles_pruefen.sh").write_text(
        f"#!/usr/bin/env bash\necho lauf >> '{zaehl.as_posix()}'\n", encoding="utf-8"
    )
    (repo / ".to-spawn").mkdir()
    konfig = {
        "staging": {"ohne": True},
        "pruefung": {"schnell": "true", "komplett": "bash skripte/alles_pruefen.sh"},
    }
    (repo / ".to-spawn" / "config.json").write_text(json.dumps(konfig), encoding="utf-8")
    (repo / ".gitignore").write_text(".to-spawn/pruef/\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "tag", "base")
    return repo


def _laeufe(zaehl: Path) -> int:
    return len(zaehl.read_text(encoding="utf-8").splitlines()) if zaehl.exists() else 0


@pytest.fixture
def zaehl(tmp_path: Path) -> Path:
    return tmp_path / "zaehl.txt"


def test_pruef_tuer_fuehrt_eigenes_skript_im_bau_ticket_nicht_aus(
    tmp_path: Path, zaehl: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _app_repo(tmp_path, zaehl)
    monkeypatch.setenv("TO_SPAWN_TICKET", "13")
    urteil = pruef.pruefen(repo, "base", "HEAD", art="komplett")
    assert _laeufe(zaehl) == 0, "eigenes Komplett-Skript lief aus einem Bau-Ticket"
    assert urteil.exit == leiter.GATE_FEHLER, urteil.zeile
    assert "schnelle Prüfung" in urteil.zeile["fehler_kurz"], urteil.zeile


def test_komplett_lauf_fuehrt_eigenes_skript_im_bau_ticket_nicht_aus(
    tmp_path: Path, zaehl: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _app_repo(tmp_path, zaehl)
    monkeypatch.setenv("TO_SPAWN_TICKET", "")
    erg = komplett_lauf.komplett_einmal(repo, SPEC, Moment.VOR_LIVE, config.lade(repo))
    assert not erg.ok and not erg.gelaufen, erg
    assert _laeufe(zaehl) == 0
    assert komplett_lauf.lies_merker(repo, SPEC) is None


def test_eine_stelle_entscheidet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    assert komplett_lauf.bau_ticket_ablehnung(SPEC, "test") is None
    monkeypatch.setenv("TO_SPAWN_TICKET", "13")
    grund = komplett_lauf.bau_ticket_ablehnung(SPEC, "test")
    assert grund and "schnelle Prüfung" in grund and f"komplett {SPEC} --moment vor-live" in grund
    ohne_spec = komplett_lauf.bau_ticket_ablehnung(None, "test")
    assert ohne_spec and "komplett <S> --moment vor-live" in ohne_spec


def test_ohne_bau_ticket_laeuft_das_eigene_skript(tmp_path: Path, zaehl: Path) -> None:
    repo = _app_repo(tmp_path, zaehl)
    urteil = pruef.pruefen(repo, "base", "HEAD", art="komplett")
    assert urteil.exit == leiter.GRUEN, urteil.zeile
    assert _laeufe(zaehl) == 1
    erg = komplett_lauf.komplett_einmal(repo, SPEC, Moment.VOR_LIVE, config.lade(repo))
    assert erg.ok and erg.gelaufen, erg
    assert _laeufe(zaehl) == 2
