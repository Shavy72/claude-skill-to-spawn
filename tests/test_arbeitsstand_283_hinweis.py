"""Nachprüfung #283: ``bau.py`` meldet einen veralteten Arbeitsordner nur, der Erst-Start zieht nach.

Echter git (bare-Repo als ``origin``, Klon ``N`` Commits dahinter), keine Attrappen. Die
``bau.py``-Fälle laufen über den echten Start-Weg ``skripte/bau.py <N>``: ein dort wieder
eingebauter Abbruch oder ein Nachziehen macht sie rot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.test_arbeitsstand_283 import SKILL, VORLAGE, N, _git, _laeuft, klon  # noqa: F401

HINTER = f"{N} Commits hinter origin/main"


def _bau(klon: Path, *extra: str):  # noqa: F811
    fertig = _laeuft(
        [sys.executable, str(SKILL / "skripte" / "bau.py"), "7", *extra],
        klon,
        TO_SPAWN_REPO=str(klon),
        TO_SPAWN_GH_REPO="test-org/app",
    )
    return fertig, fertig.stdout + fertig.stderr


def _erst_start(klon: Path):  # noqa: F811
    fertig = _laeuft([sys.executable, str(SKILL / "to_spawn.py"), "arbeitsstand"], klon, TO_SPAWN_REPO=str(klon))
    return fertig, fertig.stdout + fertig.stderr


def _hinweis_zeilen(ausgabe: str, muster: str) -> list[str]:
    return [z for z in ausgabe.splitlines() if muster in z]


def _kein_stand_abbruch(ausgabe: str) -> None:
    assert "Kein Start: Arbeitsordner" not in ausgabe and "Kein Start: Stand" not in ausgabe, ausgabe


def test_bau_hinter_mit_lokaler_aenderung_nur_hinweis(klon: Path) -> None:  # noqa: F811
    """Fall 1: hinter origin + lokale Änderung → eine Hinweis-Zeile, kein Abbruch, nichts bewegt."""
    (klon / VORLAGE).write_text('{"prompt_template": "von Hand"}', encoding="utf-8")
    vorher = _git(klon, "rev-parse", "HEAD")
    _, ausgabe = _bau(klon)
    _kein_stand_abbruch(ausgabe)
    assert len(_hinweis_zeilen(ausgabe, HINTER)) == 1, ausgabe
    assert _git(klon, "rev-parse", "HEAD") == vorher
    assert (klon / VORLAGE).read_text(encoding="utf-8") == '{"prompt_template": "von Hand"}'


@pytest.mark.parametrize("weg", ["kein_origin", "fetch_scheitert"])
def test_bau_stand_nicht_pruefbar_nur_warnung(klon: Path, tmp_path: Path, weg: str) -> None:  # noqa: F811
    """Fall 2a/2b: kein origin bzw. fetch scheitert → eine Warn-Zeile, kein Abbruch."""
    if weg == "kein_origin":
        _git(klon, "remote", "remove", "origin")
    else:
        _git(klon, "remote", "set-url", "origin", str(tmp_path / "gibt-es-nicht.git"))
    vorher = _git(klon, "rev-parse", "HEAD")
    _, ausgabe = _bau(klon)
    _kein_stand_abbruch(ausgabe)
    assert len(_hinweis_zeilen(ausgabe, "nicht prüfbar")) == 1, ausgabe
    assert _git(klon, "rev-parse", "HEAD") == vorher


@pytest.mark.parametrize("weg", ["ticket_zweig", "losgeloest"])
def test_bau_nicht_auf_hauptzweig_kein_abbruch(klon: Path, weg: str) -> None:  # noqa: F811
    """Fall 3a/3b: Ticket-Zweig bzw. abgehängter HEAD, hinter origin → Hinweis, kein Abbruch."""
    if weg == "ticket_zweig":
        _git(klon, "switch", "-q", "-c", "ticket/7-probe")
    else:
        _git(klon, "switch", "-q", "--detach")
    vorher = _git(klon, "rev-parse", "HEAD")
    _, ausgabe = _bau(klon)
    _kein_stand_abbruch(ausgabe)
    assert len(_hinweis_zeilen(ausgabe, HINTER)) == 1, ausgabe
    assert _git(klon, "rev-parse", "HEAD") == vorher


@pytest.mark.parametrize("extra", [(), ("--resume", "abc")])
def test_bau_sauberer_klon_hinter_wird_nicht_vorgespult(klon: Path, extra: tuple[str, ...]) -> None:  # noqa: F811
    """Fall 3c: sauberer Klon hinter origin → Start und ``--resume`` bewegen HEAD nicht."""
    vorher = _git(klon, "rev-parse", "HEAD")
    _, ausgabe = _bau(klon, *extra)
    _kein_stand_abbruch(ausgabe)
    assert "nachgezogen" not in ausgabe, ausgabe
    assert len(_hinweis_zeilen(ausgabe, HINTER)) == 1, ausgabe
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_erst_start_fetch_fehler_warnt_und_laeuft_weiter(klon: Path, tmp_path: Path) -> None:  # noqa: F811
    """Erst-Start: fetch scheitert → Warn-Zeile, Exit 0 (spawn_srv.sh/spawn_local.ps1 starten weiter)."""
    _git(klon, "remote", "set-url", "origin", str(tmp_path / "gibt-es-nicht.git"))
    fertig, ausgabe = _erst_start(klon)
    assert fertig.returncode == 0, ausgabe
    assert len(_hinweis_zeilen(ausgabe, "nicht prüfbar")) == 1, ausgabe


@pytest.mark.parametrize("weg", ["ticket_zweig", "losgeloest"])
def test_erst_start_nicht_auf_hauptzweig_kein_abbruch(klon: Path, weg: str) -> None:  # noqa: F811
    """Erst-Start: Ticket-Zweig/abgehängter HEAD → Hinweis, Exit 0, HEAD bleibt."""
    if weg == "ticket_zweig":
        _git(klon, "switch", "-q", "-c", "ticket/7-probe")
    else:
        _git(klon, "switch", "-q", "--detach")
    vorher = _git(klon, "rev-parse", "HEAD")
    fertig, ausgabe = _erst_start(klon)
    assert fertig.returncode == 0, ausgabe
    assert len(_hinweis_zeilen(ausgabe, HINTER)) == 1, ausgabe
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_erst_start_zieht_sauberen_klon_nach(klon: Path) -> None:  # noqa: F811
    """Erst-Start (``to_spawn.py arbeitsstand``) zieht einen sauberen Klon weiterhin nach."""
    fertig, ausgabe = _erst_start(klon)
    assert fertig.returncode == 0 and "nachgezogen" in ausgabe, ausgabe
    assert _git(klon, "rev-parse", "HEAD") == _git(klon, "rev-parse", "origin/main")
