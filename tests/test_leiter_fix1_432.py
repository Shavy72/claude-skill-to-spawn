"""Fixrunde 1 der Eingriffs-Leiter (#432): F1–F9 aus der Prüfung.

Wie ``test_leiter_432``: gestellt ist nur die Außenwelt. Für die echte Umwelt (F8)
liefern aufgezeichnete tmux-/gh-Ausgaben die Quellen (``FakeWelt`` aus dem
Aufseher-Stand-Test); Bau-Log, Leitstand und Prompt-Dateien laufen echt in tmp_path.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tests"))

import test_aufseher_stand_430 as stand_test  # noqa: E402
import test_leiter_432 as leiter_test  # noqa: E402
import test_respawn_431 as respawn_test  # noqa: E402

from to_spawn import aufseher_stand, bau_log, leiter, leitstand, respawn  # noqa: E402

TICKET = leiter_test.TICKET
ZIEL = leiter_test.ZIEL
MINUTE = leiter_test.MINUTE
FakeUmwelt = leiter_test.FakeUmwelt
FakeWerkzeug = leiter_test.FakeWerkzeug
Spion = leiter_test.Spion
_lauf = leiter_test._lauf
_start_datei = leiter_test._start_datei


@pytest.fixture
def ordner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    wt = tmp_path / "wt"
    (wt / "docs" / "handoffs").mkdir(parents=True)
    return wt


def _stufe2(wt: Path) -> FakeUmwelt:
    """Stufe 2 über die Handoff-Grenze — mit Mindest-Ruhe (still 2 min)."""
    u = FakeUmwelt(wt, still_min=leiter.MIN_RUHE_MIN, kontext_k=260.0)
    _lauf(u)
    assert leitstand.leiter_eintrag(TICKET)[0] == 2
    u.wz.getippt.clear()
    u.uhr += 5 * MINUTE
    return u


def _respawn_umgebung(tmp_path: Path, mp: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Wie Fixture ``umgebung`` in ``test_respawn_431``."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    mp.setenv("BAU_WT_DIR", str(tmp_path / "wt-basis"))
    wt = tmp_path / "wt-basis" / f"wt-{respawn_test.TICKET}"
    wt.mkdir(parents=True)
    return repo, wt


def _stand_repo(tmp_path: Path, mp: pytest.MonkeyPatch) -> Path:
    """Wie Fixture ``repo`` in ``test_aufseher_stand_430`` (Worktree-Basis ins Leere)."""
    r = tmp_path / "repo"
    (r / ".git").mkdir(parents=True)
    (r / ".to-spawn" / "config.json").parent.mkdir()
    (r / ".to-spawn" / "config.json").write_text(
        json.dumps({"worktree_basis": str(tmp_path / "wts")}), encoding="utf-8"
    )
    mp.setenv("TO_SPAWN_REPO", str(r))
    return r


# --- F1: Mindest-Ruhe + Kontext nur der aktuellen Session -------------------------------


def test_f1_handoff_grenze_still_1_min_nichts(ordner: Path) -> None:
    u = FakeUmwelt(ordner, still_min=1, kontext_k=260.0)
    erg = _lauf(u)
    assert erg.exit == 0
    assert u.wz.getippt == []
    assert leitstand.leiter_eintrag(TICKET)[0] == 0
    assert leiter.MIN_RUHE_MIN == 2


def test_f1_handoff_grenze_still_2_min_handoff(ordner: Path) -> None:
    u = FakeUmwelt(ordner, still_min=2, kontext_k=260.0)
    erg = _lauf(u)
    assert erg.exit == 0
    assert leitstand.leiter_eintrag(TICKET) == (2, u.uhr)
    assert len(u.wz.getippt) == 1


def _ts(zeit: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(zeit))


def _ende(zeit: float, sid: str, spitze_k: float) -> dict[str, Any]:
    return {
        "ts": _ts(zeit),
        "typ": "session_ende",
        "session_id": sid,
        "kontext": {"spitze": int(spitze_k * 1000)},
    }


def test_f1_bau_log_kontext_juengste_session_nach_zeit(tmp_path: Path) -> None:
    t = 1_800_000_000.0
    stand_test.bau_log(
        tmp_path,
        TICKET,
        _ende(t, "alt", 200.0),
        _ende(t + 60, "alt", 260.0),
        _ende(t + 600, "neu", 40.0),
        _ende(t + 660, "neu", 55.0),
    )
    assert bau_log.kontext_aktuell_k(tmp_path, TICKET) == 55.0
    # Respawn bei t+300: die alte Session (260k) zählt nicht mehr.
    assert bau_log.kontext_aktuell_k(tmp_path, TICKET, nach=t + 300) == 55.0
    # Nach dem Respawn noch keine Zeile der neuen Session → unbekannt, nicht 260k.
    assert bau_log.kontext_aktuell_k(tmp_path, TICKET, nach=t + 700) is None


def test_f1_bau_log_kontext_ohne_zeilen(tmp_path: Path) -> None:
    assert bau_log.kontext_aktuell_k(tmp_path, TICKET) is None


def test_f1_respawn_merkt_zeit(ordner: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(respawn, "abloesen", Spion())
    u = _stufe2(ordner)
    _start_datei(ordner, u.uhr - MINUTE)
    erg = _lauf(u)
    assert erg.exit == 0
    assert leitstand.leiter_eintrag(TICKET)[0] == 0
    assert leitstand.leiter_respawn(TICKET) == u.uhr


# --- F2: kein doppelter Handoff-Auftrag ----------------------------------------------------


def test_f2_leiter_gibt_handoff_seit_weiter(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spion = Spion()
    monkeypatch.setattr(respawn, "abloesen", spion)
    u = _stufe2(ordner)
    seit = leitstand.leiter_eintrag(TICKET)[1]
    _start_datei(ordner, u.uhr - MINUTE)
    _lauf(u)
    assert spion.aufrufe[0][3]["handoff_seit"] == seit


def test_f2_abloesen_ueberspringt_a_bei_frischen_dateien(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, wt = _respawn_umgebung(tmp_path, monkeypatch)
    fake = respawn_test.FakeWerkzeug(wt)
    fake._dateien_anlegen()  # die Leiter hat den Auftrag schon getippt
    erg = respawn.abloesen(
        repo, respawn_test.SPEC, respawn_test.TICKET, werkzeug=fake,
        warte_max=600, handoff_seit=fake.zeit - 60,
    )
    assert erg.exit == 0, erg.zeile
    getippt = [a for a in fake.aufrufe if a[0] == "tippen"]
    assert all("HANDOFF_" not in a[2] for a in getippt), getippt
    assert fake.namen()[0] == "fenster_starten"


def test_f2_abloesen_tippt_a_wenn_dateien_alt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, wt = _respawn_umgebung(tmp_path, monkeypatch)
    fake = respawn_test.FakeWerkzeug(wt)
    erg = respawn.abloesen(
        repo, respawn_test.SPEC, respawn_test.TICKET, werkzeug=fake,
        warte_max=600, handoff_seit=fake.zeit - 60,
    )
    assert erg.exit == 0, erg.zeile
    assert fake.namen()[0] == "tippen"


def test_f2_eine_pfadquelle_relativ(ordner: Path) -> None:
    jetzt = time.time()
    text = respawn.handoff_auftrag_fuer(ordner, TICKET, jetzt)
    tag = time.strftime("%Y-%m-%d", time.localtime(jetzt))
    assert f"docs/handoffs/HANDOFF_{tag}_{TICKET}.md" in text
    assert f"docs/handoffs/START_{tag}_{TICKET}.txt" in text
    assert str(ordner) not in text


# --- F3: respawn gescheitert → Stufe 5 ---------------------------------------------------


def test_f3_respawn_gescheitert_stufe5_kein_zweiter_versuch(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spion = Spion(exit_code=2)
    monkeypatch.setattr(respawn, "abloesen", spion)
    u = _stufe2(ordner)
    _start_datei(ordner, u.uhr - MINUTE)
    erg = _lauf(u)
    assert erg.exit == 2
    assert leitstand.leiter_eintrag(TICKET) == (5, u.uhr)
    u.uhr += 7 * MINUTE
    erg = _lauf(u)
    assert erg.exit == 1
    assert len(spion.aufrufe) == 1
    assert u.wz.getippt == []
    assert "Stufe 5 respawn gescheitert vor 7 min — Aufseher prüfen" in erg.zeile
    assert leitstand.leiter_eintrag(TICKET)[0] == 5


def test_f3_stufe5_arbeitet_wieder_zurueck(ordner: Path) -> None:
    leitstand.setze_leiter_stufe(TICKET, 5, time.time())
    u = FakeUmwelt(ordner, fenster=aufseher_stand.ARBEITET, still_min=None)
    erg = _lauf(u)
    assert erg.exit == 0
    assert leitstand.leiter_eintrag(TICKET) == (0, None)


# --- F4: Stufe 2 wartet höchstens 30 min --------------------------------------------------


@pytest.mark.parametrize(("minuten", "exit_code"), [(29, 0), (30, 1)])
def test_f4_stufe2_wartegrenze(ordner: Path, minuten: int, exit_code: int) -> None:
    u = _stufe2(ordner)
    u.uhr += (minuten - 5) * MINUTE
    erg = _lauf(u)
    assert erg.exit == exit_code
    assert u.wz.getippt == []
    assert leiter.WARTE_MAX_MIN == respawn.WARTE_MAX_VORGABE / 60 == 30
    if exit_code:
        assert f"wartet {minuten} min auf Prompt-Datei — Aufseher prüfen" in erg.zeile


# --- F5: /exit erst nach Mindest-Ruhe -----------------------------------------------------


@pytest.mark.parametrize(("still", "getippt"), [(1, []), (2, [(ZIEL, "/exit")])])
def test_f5_exit_mindest_ruhe(
    ordner: Path, still: int, getippt: list[tuple[str, str]]
) -> None:
    u = FakeUmwelt(ordner, still_min=still, offen=False)
    erg = _lauf(u)
    assert erg.exit == 0
    assert u.wz.getippt == getippt


# --- F6: erst merken, dann tippen ---------------------------------------------------------


def test_f6_tippen_scheitert_stufe_gemerkt_kein_zweites_tippen(ordner: Path) -> None:
    u = FakeUmwelt(ordner, still_min=30, werkzeug=FakeWerkzeug(wirft=True))
    erg = _lauf(u)
    assert erg.exit == 1
    assert "Stufe 1 gemerkt, Tippen gescheitert" in erg.zeile
    assert leitstand.leiter_eintrag(TICKET) == (1, u.uhr)
    u.wz.wirft = False
    u.uhr += MINUTE
    u.still_min = 31
    _lauf(u)
    assert u.wz.getippt == []


def test_f6_leitstand_fehler_nichts_getippt(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def kaputt(*_a: Any, **_k: Any) -> None:
        raise OSError("Leitstand nicht schreibbar")

    monkeypatch.setattr(leitstand, "setze_leiter_stufe", kaputt)
    u = FakeUmwelt(ordner, still_min=30)
    erg = _lauf(u)
    assert erg.exit == 1
    assert u.wz.getippt == []


# --- F7: nur öffentliche Aufseher-Stand-Funktionen ----------------------------------------


def test_f7_leiter_ohne_private_aufseher_funktionen() -> None:
    quelle = (SKILL / "to_spawn" / "leiter.py").read_text(encoding="utf-8")
    assert "aufseher_stand._" not in quelle
    assert "aufseher_stand.ticket_lage(" in quelle


# --- F8: echte Umwelt mit aufgezeichneten Quellen ----------------------------------------


@pytest.fixture
def welt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[stand_test.FakeWelt, Path]:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    monkeypatch.setenv("TO_SPAWN_AUFSEHER_STAND", str(tmp_path / "stand"))
    repo = _stand_repo(tmp_path, monkeypatch)
    w = stand_test.FakeWelt()
    jetzt = stand_test.JETZT
    for n, minuten in ((431, 0), (TICKET, 25)):
        w.ticket(n)
        w.fenster[n] = (jetzt - minuten * 60, stand_test.bildschirm("✻ Baked for 40s"))
    return w, repo


def test_f8_echte_lage_richtiges_ziel_je_ticket(
    welt: tuple[stand_test.FakeWelt, Path],
) -> None:
    w, repo = welt
    u = leiter._EchteUmwelt(repo, w.quellen(repo))
    lage = u.lage(stand_test.SPEC, TICKET, None)
    assert lage.ziel == f"=spec-{stand_test.SPEC}:2"
    assert lage.fenster == aufseher_stand.STILL
    assert lage.still_min == 25
    assert lage.offen is True
    assert u.lage(stand_test.SPEC, 431, None).ziel == f"=spec-{stand_test.SPEC}:1"


def test_f8_echte_lage_kontext_nach_respawn(
    welt: tuple[stand_test.FakeWelt, Path],
) -> None:
    w, repo = welt
    t = stand_test.JETZT
    stand_test.bau_log(repo, TICKET, _ende(t - 3600, "alt", 260.0))
    u = leiter._EchteUmwelt(repo, w.quellen(repo))
    assert u.lage(stand_test.SPEC, TICKET, None).kontext_k == 260.0
    leitstand.merke_leiter_respawn(TICKET, t - 600)
    assert u.lage(stand_test.SPEC, TICKET, None).kontext_k is None
    # Leiter: alter Spitzenwert 260k nach Respawn → keine neue Handoff-Anforderung.
    gemerkt = leiter.Gemerkt(0, None)
    lage = u.lage(stand_test.SPEC, TICKET, None)
    assert leiter.entscheide(lage, gemerkt, t, 250.0).aktion == "anstupsen"
    stand_test.bau_log(repo, TICKET, _ende(t - 60, "neu", 80.0))
    assert u.lage(stand_test.SPEC, TICKET, None).kontext_k == 80.0


def test_f8_kontext_typeerror_unbekannt(
    welt: tuple[stand_test.FakeWelt, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    w, repo = welt

    def kaputt(*_a: Any, **_k: Any) -> float:
        raise TypeError("float() argument must be a string or a real number")

    stand_test.bau_log(repo, TICKET, _ende(stand_test.JETZT, "x", 10.0))
    monkeypatch.setattr(bau_log, "kontext_aktuell_k", kaputt)
    u = leiter._EchteUmwelt(repo, w.quellen(repo))
    assert u.lage(stand_test.SPEC, TICKET, None).kontext_k is None


def test_f8_ticket_lage_kein_sub_issue(welt: tuple[stand_test.FakeWelt, Path]) -> None:
    w, repo = welt
    blick = aufseher_stand.ticket_lage(stand_test.SPEC, 999, w.quellen(repo))
    assert blick.lage is None and blick.ziel is None


# --- F9: fehlende Wege --------------------------------------------------------------------


def test_f9_stufe4_wieder_offen_zurueck(ordner: Path) -> None:
    leitstand.setze_leiter_stufe(TICKET, 4, time.time())
    u = FakeUmwelt(ordner, still_min=5, offen=True)
    erg = _lauf(u)
    assert erg.exit == 0
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    assert u.wz.getippt == []


def test_f9_sperre_belegt(ordner: Path) -> None:
    halter = leitstand.versuche(f"leiter-{TICKET}", "test")
    assert halter is not None
    with halter:
        u = FakeUmwelt(ordner, still_min=30)
        erg = _lauf(u)
    assert erg.exit == 0
    assert "läuft schon — nichts getan" in erg.zeile
    assert u.wz.getippt == []


def test_f9_stufe1_kontext_grenze_handoff(ordner: Path) -> None:
    u = FakeUmwelt(ordner, still_min=20)
    _lauf(u)
    u.wz.getippt.clear()
    u.uhr += 3 * MINUTE
    u.still_min = 23
    u.kontext_k = 251.0
    erg = _lauf(u)
    assert erg.exit == 0
    assert leitstand.leiter_eintrag(TICKET) == (2, u.uhr)
    assert len(u.wz.getippt) == 1


# --- Doku -----------------------------------------------------------------------------------


def test_skill_md_nennt_stufe5_ruhe_und_wartegrenze() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    abschnitt = text[text.index("Eingriffs-Leiter") :]
    for wort in ("Stufe 5", "Mindest-Ruhe", "30 min"):
        assert wort in abschnitt, wort
