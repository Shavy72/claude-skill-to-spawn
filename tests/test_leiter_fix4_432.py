"""Fixrunde 4 der Eingriffs-Leiter (#432): D1/D2 und D3 aus der vierten Prüfung.

D1/D2: Scheitert nach gescheitertem ``abloesen`` das Merken von Stufe 5, darf der
Folgelauf nie „abschliessen“ — auch wenn das abgebrochene neue Fenster selbst einen
``session_start`` geschrieben hat und das Fenster „arbeitet“ meldet.
D3: Ein unlesbares Bau-Log ist in der Meldezeile von „kein Start“ unterscheidbar.

Gestellt ist nur die Außenwelt (Fenster, Uhr, tmux, respawn). Leitstand und Bau-Log
laufen echt in ``tmp_path``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tests"))

import test_aufseher_stand_430 as stand_test
import test_leiter_432 as leiter_test
import test_leiter_fix1_432 as fix1_test
import test_leiter_fix2_432 as fix2_test
import test_leiter_fix3_432 as fix3_test

from to_spawn import aufseher_stand, bau_log, leiter, leitstand, respawn

TICKET = leiter_test.TICKET
MINUTE = leiter_test.MINUTE
FakeUmwelt = fix2_test.FakeUmwelt
_lauf = leiter_test._lauf
_start_datei = leiter_test._start_datei
ordner = fix2_test.ordner
welt = fix1_test.welt


# --- D1/D2: gescheiterter respawn + Stufe-5-Schreibfehler schließt nie ab ---------------


def _respawn_scheitert_mit_start(
    wt: Path, monkeypatch: pytest.MonkeyPatch, kaputte_stufen: set[int]
) -> tuple[FakeUmwelt, set[int]]:
    """Stufe 2 → respawn; ``abloesen`` scheitert, das neue Fenster schreibt vorher
    noch einen ``session_start``. Merken der Stufen in ``kaputte_stufen`` wirft."""
    u = fix2_test._stufe2(wt)
    _start_datei(wt, u.uhr - MINUTE)

    def gescheitert(repo: Path, spec: int, ticket: int, **_kw: Any) -> respawn.Ergebnis:
        # Zwischen Stufe-3-Merken und Ende von abloesen: Fenster startet kurz an.
        u.letzter_start = u.uhr + 30
        u.uhr += 2 * MINUTE
        return respawn.Ergebnis(2, f"respawn #{ticket}: neues Fenster bricht ab")

    monkeypatch.setattr(respawn, "abloesen", gescheitert)
    echt = leitstand.setze_leiter_stufe
    aufgerufen: list[int] = []

    def teils_kaputt(ticket: int, stufe: int, seit: float | None) -> None:
        aufgerufen.append(stufe)
        # Erstes Merken von Stufe 3 (vor abloesen) gelingt immer.
        if stufe in kaputte_stufen and not (stufe == 3 and aufgerufen.count(3) == 1):
            raise leitstand.ZustandKaputt("Leitstand kaputt")
        echt(ticket, stufe, seit)

    monkeypatch.setattr(leitstand, "setze_leiter_stufe", teils_kaputt)
    return u, kaputte_stufen


def test_d2_folgelauf_nach_stufe5_schreibfehler_schliesst_nicht_ab(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    u, kaputt = _respawn_scheitert_mit_start(ordner, monkeypatch, {5})
    erg = _lauf(u)
    assert erg.exit == 1
    assert "Stufe 5 nicht gemerkt" in erg.zeile
    assert leitstand.leiter_eintrag(TICKET)[0] == 3

    kaputt.clear()  # Leitstand wieder schreibbar
    u.fenster = aufseher_stand.ARBEITET
    u.still_min = None
    u.uhr += MINUTE
    erg = _lauf(u)
    assert erg.exit == 1, erg.zeile
    assert "Stufe 3" in erg.zeile
    assert "Aufseher prüfen" in erg.zeile
    assert leitstand.leiter_eintrag(TICKET)[0] == 3
    assert leitstand.leiter_respawn(TICKET) is None
    assert u.wz.getippt == []


def test_d1_stufe3_nach_abloesen_neu_gemerkt_juenger_als_start(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    u, _ = _respawn_scheitert_mit_start(ordner, monkeypatch, {5})
    erg = _lauf(u)
    assert erg.exit == 1
    stufe, seit = leitstand.leiter_eintrag(TICKET)
    assert stufe == 3
    assert seit is not None and u.letzter_start is not None
    assert seit >= u.letzter_start


def test_d1_stufe5_und_stufe3_nicht_merkbar_exit1_keine_ausnahme(
    ordner: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    u, _ = _respawn_scheitert_mit_start(ordner, monkeypatch, {3, 5})
    gemerkt = leiter.Gemerkt(*leitstand.leiter_eintrag(TICKET))
    # Kein Netz der Tür: eine durchgereichte Ausnahme fiele hier sichtbar auf.
    erg = leiter._respawn(u, ordner, 1, TICKET, gemerkt, u.uhr)
    assert erg.exit == 1
    assert "Stufe 5 nicht gemerkt" in erg.zeile
    assert "Stufe 3 nicht neu gemerkt" in erg.zeile
    assert any("Stufe 3" in r.getMessage() for r in caplog.records)


# --- D3: Bau-Log unlesbar ist in der Zeile von „kein Start“ unterscheidbar --------------


def _lage(**kw: Any) -> leiter.Lage:
    basis: dict[str, Any] = {
        "offen": True,
        "fenster": aufseher_stand.ARBEITET,
        "still_min": None,
        "kontext_k": None,
        "prompt_datei": False,
        "ziel": "=spec-1:2",
        "letzter_start": None,
    }
    basis.update(kw)
    return leiter.Lage(**basis)


def test_d3_entscheide_stufe3_bau_log_unlesbar_nennt_ursache() -> None:
    jetzt = 1_800_000_000.0
    gemerkt = leiter.Gemerkt(3, jetzt - 5 * MINUTE)
    schritt = leiter.entscheide(_lage(log_unlesbar=True), gemerkt, jetzt, 250.0)
    assert schritt.aktion == "melden"
    assert "Bau-Log unlesbar" in schritt.grund
    assert "keine neue Session belegt" not in schritt.grund


def test_d3_entscheide_stufe3_ohne_start_bleibt_alte_zeile() -> None:
    jetzt = 1_800_000_000.0
    gemerkt = leiter.Gemerkt(3, jetzt - 5 * MINUTE)
    schritt = leiter.entscheide(_lage(), gemerkt, jetzt, 250.0)
    assert "keine neue Session belegt" in schritt.grund
    assert "Bau-Log unlesbar" not in schritt.grund


def test_d3_echte_lage_leser_wirft_log_unlesbar(
    welt: tuple[stand_test.FakeWelt, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    w, repo = welt

    def kaputt(*_a: Any, **_k: Any) -> float:
        raise OSError("Bau-Log gesperrt")

    stand_test.bau_log(repo, TICKET, fix3_test._start(stand_test.JETZT))
    monkeypatch.setattr(bau_log, "letzter_session_start", kaputt)
    lage = leiter._EchteUmwelt(repo, w.quellen(repo)).lage(
        stand_test.SPEC, TICKET, None
    )
    assert lage.letzter_start is None
    assert lage.log_unlesbar is True


def test_d3_echte_lage_ohne_bau_log_nicht_unlesbar(
    welt: tuple[stand_test.FakeWelt, Path],
) -> None:
    w, repo = welt
    lage = leiter._EchteUmwelt(repo, w.quellen(repo)).lage(
        stand_test.SPEC, TICKET, None
    )
    assert lage.letzter_start is None
    assert lage.log_unlesbar is False
