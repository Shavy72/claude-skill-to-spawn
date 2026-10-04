"""Fixrunde 3 der Eingriffs-Leiter (#432): C1 und C2 aus der dritten Prüfung.

C1: Stufe 3 („respawn läuft“) + Fenster arbeitet schließt nur ab, wenn eine neue
Session belegt ist (``session_start`` jünger als der gemerkte Eingriff); sonst
Exit 1. Ein Schreibfehler beim Merken von Stufe 5 wird gefangen (Exit 1).
C2: ``_EchteUmwelt`` liest ``letzter_start`` aus dem echten Bau-Log.

Gestellt ist nur die Außenwelt (Fenster, Uhr, tmux, respawn). Leitstand und Bau-Log
laufen echt in ``tmp_path``.
"""

from __future__ import annotations

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
import test_leiter_fix1_432 as fix1_test
import test_leiter_fix2_432 as fix2_test

from to_spawn import aufseher_stand, bau_log, leiter, leitstand, respawn

TICKET = leiter_test.TICKET
MINUTE = leiter_test.MINUTE
FakeUmwelt = fix2_test.FakeUmwelt
_lauf = leiter_test._lauf
_start_datei = leiter_test._start_datei
ordner = fix2_test.ordner
welt = fix1_test.welt


# --- C1: Stufe 3 + arbeitet schließt nur mit belegter neuer Session ab --------------------


def _stufe3_arbeitet(wt: Path, start_versatz: float | None) -> tuple[FakeUmwelt, float]:
    seit = time.time() - 4 * MINUTE
    leitstand.setze_leiter_stufe(TICKET, 3, seit)
    u = FakeUmwelt(wt, fenster=aufseher_stand.ARBEITET, still_min=None)
    u.letzter_start = None if start_versatz is None else seit + start_versatz
    return u, seit


def test_c1_stufe3_arbeitet_neue_session_belegt_abgeschlossen(ordner: Path) -> None:
    u, seit = _stufe3_arbeitet(ordner, 2 * MINUTE)
    erg = _lauf(u)
    assert erg.exit == 0
    assert erg.zeile.startswith(f"leiter #{TICKET}: Stufe 0")
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    assert leitstand.leiter_respawn(TICKET) == seit
    assert u.wz.getippt == []


@pytest.mark.parametrize("versatz", [None, -2 * MINUTE, 0.0])
def test_c1_stufe3_arbeitet_ohne_neue_session_meldet(
    ordner: Path, versatz: float | None
) -> None:
    u, seit = _stufe3_arbeitet(ordner, versatz)
    erg = _lauf(u)
    assert erg.exit == 1
    assert "Stufe 3" in erg.zeile
    assert "keine neue Session belegt" in erg.zeile
    assert "Aufseher prüfen" in erg.zeile
    assert leitstand.leiter_eintrag(TICKET) == (3, seit)
    assert leitstand.leiter_respawn(TICKET) is None
    assert u.wz.getippt == []


def test_c1_entscheide_stufe3_arbeitet_ohne_start_kein_abschluss() -> None:
    jetzt = 1_800_000_000.0
    gemerkt = leiter.Gemerkt(3, jetzt - 5 * MINUTE)
    lage = leiter.Lage(
        offen=True,
        fenster=aufseher_stand.ARBEITET,
        still_min=None,
        kontext_k=None,
        prompt_datei=False,
        ziel="=spec-1:2",
        letzter_start=None,
    )
    assert leiter.entscheide(lage, gemerkt, jetzt, 250.0).aktion == "melden"


def test_c1_stufe5_schreibfehler_exit1_keine_ausnahme(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gescheitert(repo: Path, spec: int, ticket: int, **_kw: Any) -> respawn.Ergebnis:
        return respawn.Ergebnis(2, f"respawn #{ticket}: neues Fenster startet nicht")

    monkeypatch.setattr(respawn, "abloesen", gescheitert)
    u = fix2_test._stufe2(ordner)
    _start_datei(ordner, u.uhr - MINUTE)
    echt = leitstand.setze_leiter_stufe

    def stufe5_kaputt(ticket: int, stufe: int, seit: float | None) -> None:
        if stufe == 5:
            raise leitstand.ZustandKaputt("Leitstand kaputt")
        echt(ticket, stufe, seit)

    monkeypatch.setattr(leitstand, "setze_leiter_stufe", stufe5_kaputt)
    # Kein Netz der Tür: eine durchgereichte Ausnahme fiele hier sichtbar auf.
    erg = leiter._respawn(
        u, ordner, 1, TICKET, leiter.Gemerkt(2, u.uhr - MINUTE), u.uhr
    )
    assert erg.exit == 1
    assert "Stufe 5 nicht gemerkt" in erg.zeile
    assert leitstand.leiter_eintrag(TICKET)[0] == 3


# --- C2: echte Umwelt liest letzter_start aus dem Bau-Log --------------------------------


def _start(zeit: float) -> dict[str, Any]:
    return {"ts": fix1_test._ts(zeit), "typ": "session_start", "session_id": "neu"}


def test_c2_echte_lage_letzter_start_aus_bau_log(
    welt: tuple[stand_test.FakeWelt, Path],
) -> None:
    w, repo = welt
    t = stand_test.JETZT
    stand_test.bau_log(repo, TICKET, _start(t - 3600), _start(t - 120))
    u = leiter._EchteUmwelt(repo, w.quellen(repo))
    assert u.lage(stand_test.SPEC, TICKET, None).letzter_start == int(t - 120)


def test_c2_echte_lage_ohne_bau_log_kein_start(
    welt: tuple[stand_test.FakeWelt, Path],
) -> None:
    w, repo = welt
    u = leiter._EchteUmwelt(repo, w.quellen(repo))
    assert u.lage(stand_test.SPEC, TICKET, None).letzter_start is None


def test_c2_echte_lage_bau_log_unlesbar_kein_start(
    welt: tuple[stand_test.FakeWelt, Path],
) -> None:
    w, repo = welt
    datei = repo / ".to-spawn" / "bau_log" / f"{TICKET}.jsonl"
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_bytes(b"\xff\xfe kaputt {nicht json\n")
    u = leiter._EchteUmwelt(repo, w.quellen(repo))
    assert u.lage(stand_test.SPEC, TICKET, None).letzter_start is None


def test_c2_echte_lage_leser_wirft_kein_start(
    welt: tuple[stand_test.FakeWelt, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    w, repo = welt

    def kaputt(*_a: Any, **_k: Any) -> float:
        raise OSError("Bau-Log gesperrt")

    stand_test.bau_log(repo, TICKET, _start(stand_test.JETZT))
    monkeypatch.setattr(bau_log, "letzter_session_start", kaputt)
    u = leiter._EchteUmwelt(repo, w.quellen(repo))
    assert u.lage(stand_test.SPEC, TICKET, None).letzter_start is None
