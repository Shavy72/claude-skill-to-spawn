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

from pathlib import Path

import pytest

from to_spawn import config, komplett_lauf, pruef
from to_spawn.pruef import leiter
from to_spawn.pruefplan import Moment

SPEC = 496


@pytest.fixture
def zaehl(tmp_path: Path) -> Path:
    return tmp_path / "zaehl.txt"


def test_pruef_tuer_fuehrt_eigenes_skript_im_bau_ticket_nicht_aus(
    tmp_path: Path, zaehl: Path, monkeypatch: pytest.MonkeyPatch, app_repo_komplett, komplett_laeufe
) -> None:
    repo = app_repo_komplett(tmp_path, zaehl)
    monkeypatch.setenv("TO_SPAWN_TICKET", "13")
    urteil = pruef.pruefen(repo, "base", "HEAD", art="komplett")
    assert komplett_laeufe(zaehl) == 0, "eigenes Komplett-Skript lief aus einem Bau-Ticket"
    assert urteil.exit == leiter.GATE_FEHLER, urteil.zeile
    assert "schnelle Prüfung" in urteil.zeile["fehler_kurz"], urteil.zeile


def test_komplett_lauf_fuehrt_eigenes_skript_im_bau_ticket_nicht_aus(
    tmp_path: Path, zaehl: Path, monkeypatch: pytest.MonkeyPatch, app_repo_komplett, komplett_laeufe
) -> None:
    repo = app_repo_komplett(tmp_path, zaehl)
    monkeypatch.setenv("TO_SPAWN_TICKET", "")
    erg = komplett_lauf.komplett_einmal(repo, SPEC, Moment.VOR_LIVE, config.lade(repo))
    assert not erg.ok and not erg.gelaufen, erg
    assert komplett_laeufe(zaehl) == 0
    assert komplett_lauf.lies_merker(repo, SPEC) is None


def test_eine_stelle_entscheidet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    assert komplett_lauf.bau_ticket_ablehnung(SPEC, "test") is None
    monkeypatch.setenv("TO_SPAWN_TICKET", "13")
    grund = komplett_lauf.bau_ticket_ablehnung(SPEC, "test")
    assert grund and "schnelle Prüfung" in grund and f"komplett {SPEC} --moment vor-live" in grund
    ohne_spec = komplett_lauf.bau_ticket_ablehnung(None, "test")
    assert ohne_spec and "komplett <S> --moment vor-live" in ohne_spec


def test_ohne_bau_ticket_laeuft_das_eigene_skript(
    tmp_path: Path, zaehl: Path, app_repo_komplett, komplett_laeufe
) -> None:
    repo = app_repo_komplett(tmp_path, zaehl)
    urteil = pruef.pruefen(repo, "base", "HEAD", art="komplett")
    assert urteil.exit == leiter.GRUEN, urteil.zeile
    assert komplett_laeufe(zaehl) == 1
    erg = komplett_lauf.komplett_einmal(repo, SPEC, Moment.VOR_LIVE, config.lade(repo))
    assert erg.ok and erg.gelaufen, erg
    assert komplett_laeufe(zaehl) == 2


def test_ohne_bau_ticket_laeuft_das_eigene_skript_auch_mit_staging(
    tmp_path: Path, zaehl: Path, app_repo_komplett, komplett_laeufe
) -> None:
    """#504: Die Sperre trifft nur Bau-Tickets, ein berechtigter Lauf wird auch mit Staging nicht abgelehnt."""
    repo = app_repo_komplett(tmp_path, zaehl, mit_staging=True)
    assert config.hat_staging(config.lade(repo))
    urteil = pruef.pruefen(repo, "base", "HEAD", art="komplett")
    assert urteil.exit == leiter.GRUEN, urteil.zeile
    assert komplett_laeufe(zaehl) == 1
    erg = komplett_lauf.komplett_einmal(repo, SPEC, Moment.VOR_LIVE, config.lade(repo))
    assert erg.ok and erg.gelaufen, erg
    assert komplett_laeufe(zaehl) == 2
