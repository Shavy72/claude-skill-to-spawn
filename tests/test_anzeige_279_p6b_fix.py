"""#279 P6b Fix-Runde: Prüfer-Befunde zu PR 307 — je Befund ein Test.

Mischpfad der Seite (Live-Dokument ohne ``stufe``), eine Stelle für „endgültig zu“,
„gebaut um“ mit mehreren Label-Ereignissen, Zeitbalken ohne Ende, Leiter bei
unlesbarem GitHub, Checkpoint-Kreis, Label-Literal nur im Baustein, Aufpasser bei
hängendem ``gh``. capo-Fälle stehen in ``test_anzeige_279_p6b_fix_capo.py``.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tests"))

import test_aufseher_stand_430 as stand_test  # noqa: E402
from test_anzeige_279_p6b import _gebaut_issue, _js, _node, ls, ohne_node  # noqa: E402, F401
from test_leiter_fix1_432 import TICKET, welt  # noqa: E402, F401

from to_spawn import aufpasser, aufseher_stand, gh, leiter, manifest, ticket_zustand  # noqa: E402

LABEL = ticket_zustand.STAGING_FERTIG_LABEL
MISCH_VORSPANN = (
    "const zahl = (v, d) => (typeof v === 'number' && Number.isFinite(v) ? v : d);\n"
    "const STUFEN = ['offen', 'fertig_gebaut', 'zu', 'verworfen'];\n"
    "const LIVE_FELDER = ['zustand', 'stufe', 'ist_k', 'phase', 'fortschritt', 'start', 'ende', 'gebaut'];\n"
    "let tickets = [], statTickets = [], liveAktuell = null, gewaehlt = null;\n"
    "const renderAlles = () => {};\n"
)


# --- Punkt 1 + 3: Mischpfad erbt keine Stufe ----------------------------------


@ohne_node
def test_seite_mische_live_ohne_stufe_leitet_aus_live_feldern_ab() -> None:
    """Altes Dokument mit ``ende`` → zu; Lesefehler mit behaltenem ``ende`` → nicht „offen“."""
    js = (
        MISCH_VORSPANN
        + _js("normTicket")
        + _js("mische")
        + "\nstatTickets = [1, 2, 3, 4].map((nr) => normTicket({ nr, zustand: 'wartet' }, nr));\n"
        "liveAktuell = { tickets: {\n"
        "  '1': { zustand: 'fertig', ende: '2026-11-23T10:00:00+01:00' },\n"  # altes Dokument
        "  '2': { zustand: 'läuft', ist_k: 12, ende: '2026-11-23T10:00:00+01:00' },\n"  # Lesefehler
        "  '3': { zustand: 'fertig', stufe: 'fertig_gebaut' },\n"
        "  '4': { zustand: 'wartet', stufe: 'offen' },\n"
        "} };\n"
        "mische();\n"
        "for (const t of tickets) console.log(t.nr + ':' + t.stufe);\n"
    )
    assert _node(js) == ["1:zu", "2:zu", "3:fertig_gebaut", "4:offen"]


# --- Punkt 2: „endgültig zu“ an genau einer Stelle -----------------------------


def test_endgueltig_zu_stufen_eine_stelle() -> None:
    assert ticket_zustand.ENDGUELTIG_ZU_STUFEN == frozenset({"zu", "verworfen"})
    assert not hasattr(aufseher_stand, "_ENDGUELTIG_ZU")
    quelle = (SKILL / "to_spawn" / "aufseher_stand.py").read_text(encoding="utf-8")
    assert "ticket_zustand.ENDGUELTIG_ZU_STUFEN" in quelle
    for s in ticket_zustand.STUFEN:
        ticket = {"state": "open"} if s == "offen" else {"state": "open", "labels": [LABEL]}
        if s in ("zu", "verworfen"):
            ticket = {"state": "closed", "stateReason": "not_planned" if s == "verworfen" else "completed"}
        assert ticket_zustand.stufe(ticket) == s
        assert ticket_zustand.endgueltig_zu(ticket) is aufseher_stand.TicketLage(1, s, "x").endgueltig_zu


def test_ticket_lage_hat_keine_alt_frage_offen() -> None:
    assert not hasattr(aufseher_stand.TicketLage, "offen")


# --- Punkt 4 + 5: „gebaut um“ -------------------------------------------------


def test_gebaut_um_letztes_label_ereignis_zaehlt(ls: ModuleType) -> None:
    issue = _gebaut_issue("2026-11-23T10:30:00Z")
    issue["timelineItems"]["nodes"].insert(0, {"createdAt": "2026-11-23T09:00:00Z", "label": {"name": LABEL}})
    assert ls.gebaut_um(issue, []) == ls.lokal_iso("2026-11-23T10:30:00Z")


def test_gebaut_um_ersatz_und_fehlen_werden_geloggt(ls: ModuleType, caplog: pytest.LogCaptureFixture) -> None:
    zeilen = [{"typ": "session_ende", "ts": "2026-11-23T08:40:00+00:00"}]
    with caplog.at_level(logging.INFO, logger="leitstand_seite"):
        assert ls.gebaut_um(_gebaut_issue(None), zeilen) == ls.lokal_iso("2026-11-23T08:40:00+00:00")
    assert any("Ersatz" in r.getMessage() for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="leitstand_seite"):
        assert ls.gebaut_um(_gebaut_issue(None), []) is None
    assert any("unbekannt" in r.getMessage() for r in caplog.records)


@ohne_node
def test_seite_zeitbalken_waechst_nur_solange_gebaut_wird() -> None:
    js = (
        _js("fertigUm")
        + _js("laeuftNoch")
        + "\nconsole.log(laeuftNoch({ stufe: 'offen', ende: null, gebaut: null }));\n"
        "console.log(laeuftNoch({ stufe: 'fertig_gebaut', ende: null, gebaut: null }));\n"
        "console.log(laeuftNoch({ stufe: 'fertig_gebaut', ende: null, gebaut: '2026-11-23T09:00:00Z' }));\n"
        "console.log(laeuftNoch({ stufe: 'zu', ende: null, gebaut: null }));\n"
    )
    assert _node(js) == ["true", "false", "false", "false"]
    seite = (SKILL / "leitstand" / "seite.html").read_text(encoding="utf-8")
    assert "fertigUm(t) == null) {" not in seite  # Zeitbalken im Takt fragt laeuftNoch


# --- Punkt 7: Leiter bei unlesbarem GitHub --------------------------------------


def _leiter_schritt(w: stand_test.FakeWelt, repo: Path) -> tuple[leiter.Lage, leiter.Schritt]:
    lage = leiter._EchteUmwelt(repo, w.quellen(repo)).lage(stand_test.SPEC, TICKET, None)
    return lage, leiter.entscheide(lage, leiter.Gemerkt(0, None), w.uhr, 1000.0)


def test_leiter_github_unlesbar_tippt_kein_exit(welt: tuple[stand_test.FakeWelt, Path]) -> None:  # noqa: F811
    """GitHub → Aufseher-Stand → Leiter: ohne Zustand kein ``/exit`` (Gegenprobe: zu → ``/exit``)."""
    w, repo = welt
    w.tickets[TICKET]["state"] = "closed"
    lage, schritt = _leiter_schritt(w, repo)
    assert (lage.stufe, schritt.aktion) == ("zu", "exit")  # Weg trägt bis zum /exit
    for kaputt in (None, "", "unbekannt"):
        w.tickets[TICKET]["state"] = kaputt
        lage, schritt = _leiter_schritt(w, repo)
        assert lage.stufe is None
        assert schritt.aktion != "exit", schritt
    del w.tickets[TICKET]["state"]
    lage, schritt = _leiter_schritt(w, repo)
    assert lage.stufe is None and schritt.aktion != "exit"


# --- Punkt 8: ``_stufe`` mit strip --------------------------------------------


def test_aufseher_stufe_state_mit_leerzeichen() -> None:
    assert aufseher_stand._stufe({"state": " OPEN "}) == "offen"
    assert aufseher_stand._stufe({"state": "closed\n"}) == "zu"
    assert aufseher_stand._stufe({"state": " "}) is None


# --- Punkt 10: Checkpoint-Kreis ------------------------------------------------


def test_manifest_checkpoint_kreis_901_902_wird_gemeldet() -> None:
    bericht = manifest.Bericht(spec="900", tickets=["901", "902"])
    github: dict[str, dict[str, Any] | None] = {
        "901": {"state": "OPEN", "labels": [], "_kanten": [{"number": 902}]},
        "902": {"state": "OPEN", "labels": [{"name": "checkpoint"}], "_kanten": [{"number": 901}]},
    }
    manifest._pruefe_checkpoint(bericht, github, ["902"])
    assert any("#902 steht mitten in der Kette: blockiert #901" in f for f in bericht.fehler), bericht.fehler


# --- Punkt 11: Label-Literal nur im Baustein -----------------------------------


def test_label_literal_staging_fertig_nur_in_ticket_zustand() -> None:
    literal = re.compile(r"""["']staging:fertig["']""")
    treffer = [
        f"{p.relative_to(SKILL)}:{i}"
        for p in sorted((SKILL / "to_spawn").rglob("*.py"))
        if p.name != "ticket_zustand.py"
        for i, zeile in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if literal.search(zeile)
    ]
    assert treffer == []
    assert literal.search((SKILL / "to_spawn" / "ticket_zustand.py").read_text(encoding="utf-8"))


# --- Punkt 12: Aufpasser bei hängendem gh --------------------------------------


def test_aufpasser_ticket_lesen_gh_haengt_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def haengt(*_a: Any, **k: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="gh", timeout=k.get("timeout") or 1)

    monkeypatch.setattr(gh, "gh_befehl", lambda: ["gh"])
    monkeypatch.setattr(gh.subprocess, "run", haengt)
    wache = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "zustand"))
    with pytest.raises(RuntimeError, match="#7"):
        wache.ticket_lesen(7, tmp_path)
    assert wache.start_erlaubt("9", tmp_path, 7) is False  # kein Start
    assert ticket_zustand.fertig_gebaut({}) is False  # Ersatz ``{}`` im Schließ-Weg: nichts schließen
