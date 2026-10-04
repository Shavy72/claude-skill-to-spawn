"""Fixrunde 2 der Eingriffs-Leiter (#432): B1, B3, B4 aus der zweiten Prüfung.

Wie ``test_leiter_fix1_432``: gestellt ist nur die Außenwelt (Fenster, Uhr, tmux,
respawn). Leitstand und Bau-Log laufen echt in ``tmp_path``.
"""

from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tests"))

import test_aufseher_stand_430 as stand_test
import test_leiter_432 as leiter_test

from to_spawn import aufseher_stand, bau_log, leiter, leitstand, respawn

TICKET = leiter_test.TICKET
MINUTE = leiter_test.MINUTE
Spion = leiter_test.Spion
_lauf = leiter_test._lauf
_start_datei = leiter_test._start_datei


class FakeUmwelt(leiter_test.FakeUmwelt):
    """Wie im Alt-Test, zusätzlich mit jüngstem ``session_start`` aus dem Bau-Log."""

    def __init__(self, wt: Path, **kw: Any) -> None:
        super().__init__(wt, **kw)
        self.letzter_start: float | None = None

    def lage(self, spec: int, ticket: int, seit: float | None) -> leiter.Lage:
        lage = super().lage(spec, ticket, seit)
        felder = {f.name for f in dataclasses.fields(lage)}
        if "letzter_start" not in felder:  # Stand vor dem Fix: Rot-Lauf ehrlich halten
            return lage
        return dataclasses.replace(lage, letzter_start=self.letzter_start)


@pytest.fixture
def ordner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    wt = tmp_path / "wt"
    (wt / "docs" / "handoffs").mkdir(parents=True)
    return wt


def _stufe2(wt: Path) -> FakeUmwelt:
    """Stufe 2 über die Handoff-Grenze — mit Mindest-Ruhe (still ≥ MIN_RUHE_MIN)."""
    u = FakeUmwelt(wt, still_min=leiter.MIN_RUHE_MIN, kontext_k=260.0)
    _lauf(u)
    assert leitstand.leiter_eintrag(TICKET)[0] == 2
    u.wz.getippt.clear()
    u.uhr += 5 * MINUTE
    return u


# --- B3: Stufe 3 „respawn läuft“ vor dem Ablösen gemerkt ----------------------------------


def test_b3_merken_vor_abloesen_scheitert_nichts_abgeloest(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spion = Spion()
    monkeypatch.setattr(respawn, "abloesen", spion)
    u = _stufe2(ordner)
    _start_datei(ordner, u.uhr - MINUTE)

    def kaputt(*_a: Any, **_k: Any) -> None:
        raise OSError("Leitstand nicht schreibbar")

    monkeypatch.setattr(leitstand, "setze_leiter_stufe", kaputt)
    erg = _lauf(u)
    assert erg.exit == 1
    assert spion.aufrufe == []


def test_b3_stufe3_waehrend_abloesen_gemerkt(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gesehen: list[tuple[int, float | None]] = []

    def spion(repo: Path, spec: int, ticket: int, **_kw: Any) -> respawn.Ergebnis:
        gesehen.append(leitstand.leiter_eintrag(ticket))
        return respawn.Ergebnis(0, f"respawn #{ticket}: Testzeile")

    monkeypatch.setattr(respawn, "abloesen", spion)
    u = _stufe2(ordner)
    _start_datei(ordner, u.uhr - MINUTE)
    erg = _lauf(u)
    assert erg.exit == 0
    assert gesehen == [(3, u.uhr)]
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    assert leitstand.leiter_respawn(TICKET) == u.uhr


def test_b3_abschluss_scheitert_kein_zweites_abloesen(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    echt = leitstand.aendere_zustand
    aufrufe: list[int] = []

    def kaputt(*_a: Any, **_k: Any) -> dict:
        raise OSError("Leitstand nicht schreibbar")

    def spion(repo: Path, spec: int, ticket: int, **_kw: Any) -> respawn.Ergebnis:
        aufrufe.append(ticket)
        # Ab jetzt scheitert jeder Leitstand-Schreibvorgang (Abschluss).
        monkeypatch.setattr(leitstand, "aendere_zustand", kaputt)
        return respawn.Ergebnis(0, f"respawn #{ticket}: Testzeile")

    monkeypatch.setattr(respawn, "abloesen", spion)
    u = _stufe2(ordner)
    _start_datei(ordner, u.uhr - MINUTE)
    erg = _lauf(u)
    assert erg.exit == 1
    assert "Stufe 3" in erg.zeile
    monkeypatch.setattr(leitstand, "aendere_zustand", echt)

    u.uhr += 3 * MINUTE
    erg = _lauf(u)
    assert aufrufe == [TICKET]
    assert erg.exit == 1
    assert "Stufe 3" in erg.zeile and "Aufseher prüfen" in erg.zeile
    assert u.wz.getippt == []


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Fixrunde 3 C1: Abschluss nur mit belegter neuer Session (test_leiter_fix3_432)",
)
def test_b3_stufe3_fenster_arbeitet_abschluss_nachgeholt(ordner: Path) -> None:
    seit = time.time() - 4 * MINUTE
    leitstand.setze_leiter_stufe(TICKET, 3, seit)
    u = FakeUmwelt(ordner, fenster=aufseher_stand.ARBEITET, still_min=None)
    erg = _lauf(u)
    assert erg.exit == 0
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    assert leitstand.leiter_respawn(TICKET) == seit


def test_b3_schliesse_respawn_ein_schreibvorgang(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    echt = leitstand.aendere_zustand
    zaehler: list[int] = []

    def zaehlend(fn: Any) -> dict:
        zaehler.append(1)
        return echt(fn)

    leitstand.setze_leiter_stufe(TICKET, 3, 100.0)
    monkeypatch.setattr(leitstand, "aendere_zustand", zaehlend)
    leitstand.schliesse_leiter_respawn(TICKET, 123.0)
    assert zaehler == [1]
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    assert leitstand.leiter_respawn(TICKET) == 123.0


# --- B1: Stufe 5 zurück, wenn das Ticket neu beginnt ---------------------------------------


def test_b1_stufe5_neuer_session_start_zurueck(ordner: Path) -> None:
    seit = time.time() - 10 * MINUTE
    leitstand.setze_leiter_stufe(TICKET, 5, seit)
    u = FakeUmwelt(ordner, still_min=5)
    u.letzter_start = seit + 2 * MINUTE
    erg = _lauf(u)
    assert erg.exit == 0
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    assert u.wz.getippt == []


def test_b1_stufe5_alter_session_start_bleibt(ordner: Path) -> None:
    seit = time.time() - 10 * MINUTE
    leitstand.setze_leiter_stufe(TICKET, 5, seit)
    u = FakeUmwelt(ordner, still_min=5)
    u.letzter_start = seit - 2 * MINUTE
    erg = _lauf(u)
    assert erg.exit == 1
    assert leitstand.leiter_eintrag(TICKET)[0] == 5
    assert u.wz.getippt == []


def test_b1_stufe5_zeit_erst_nach_abloesen(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startet das abgebrochene neue Fenster während abloesen, setzt das nicht zurück."""
    u = _stufe2(ordner)
    _start_datei(ordner, u.uhr - MINUTE)
    beginn = u.uhr

    def spion(repo: Path, spec: int, ticket: int, **_kw: Any) -> respawn.Ergebnis:
        u.letzter_start = u.uhr + MINUTE  # neues Fenster schreibt session_start …
        u.uhr += 3 * MINUTE  # … und abloesen scheitert erst später
        return respawn.Ergebnis(2, f"respawn #{ticket}: gescheitert")

    monkeypatch.setattr(respawn, "abloesen", spion)
    erg = _lauf(u)
    assert erg.exit == 2
    assert leitstand.leiter_eintrag(TICKET) == (5, beginn + 3 * MINUTE)
    u.uhr += MINUTE
    erg = _lauf(u)
    assert erg.exit == 1
    assert leitstand.leiter_eintrag(TICKET)[0] == 5


def test_b1_bau_log_letzter_session_start(tmp_path: Path) -> None:
    t = 1_800_000_000.0

    def zeile(zeit: float, typ: str) -> dict[str, Any]:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(zeit))
        return {"ts": ts, "typ": typ}

    assert bau_log.letzter_session_start(tmp_path, TICKET) is None
    stand_test.bau_log(
        tmp_path,
        TICKET,
        zeile(t, "session_start"),
        zeile(t + 300, "session_start"),
        zeile(t + 600, "session_ende"),
    )
    assert bau_log.letzter_session_start(tmp_path, TICKET) == t + 300


# --- B4: Ersatz für die xfail-Alt-Tests mit Mindest-Ruhe ------------------------------------


def test_b4_prompt_datei_alt_nichts(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spion = Spion()
    monkeypatch.setattr(respawn, "abloesen", spion)
    u = _stufe2(ordner)
    seit = leitstand.leiter_eintrag(TICKET)[1]
    assert seit is not None
    _start_datei(ordner, seit - 10 * MINUTE)
    erg = _lauf(u)
    assert spion.aufrufe == []
    assert erg.exit == 0
    assert leitstand.leiter_eintrag(TICKET)[0] == 2


def test_b4_stufe2_arbeitet_bleibt(ordner: Path) -> None:
    u = _stufe2(ordner)
    u.fenster = aufseher_stand.ARBEITET
    erg = _lauf(u)
    assert erg.exit == 0
    assert u.wz.getippt == []
    assert leitstand.leiter_eintrag(TICKET)[0] == 2
