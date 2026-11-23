"""#279 P1: Baustein ``to_spawn.ticket_zustand`` + Stellen in ``gh.py`` und ``aufpasser.py``.

Die zwei Fragen: ``fertig_gebaut`` (zu ODER ``staging:fertig``) und ``endgueltig_zu`` (zu).
Weg-Tests am Aufpasser nutzen die Welt aus ``test_aufpasser_236`` (echtes tmux auf eigenem
Socket, echtes git); gestellt ist nur ``gh`` (externer Dienst, ``tests/hilfen/gh_stub.py``).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from test_aufpasser_236 import (  # noqa: F401 — ``welt`` ist ein Fixture
    SITZUNG,
    SPEC,
    Welt,
    _origin_hat_branch,
    fenster_namen,
    tmux,
    welt,
)

from to_spawn import gh, ticket_zustand

SKILL = Path(__file__).resolve().parent.parent

# --- Baustein: Rechenlogik über alle Daten-Formen ---------------------------

FAELLE: list[tuple[str, dict[str, Any], str]] = [
    ("rest_offen", {"state": "open", "labels": []}, "offen"),
    ("cli_offen", {"state": "OPEN", "labels": [{"name": "bug"}]}, "offen"),
    ("rest_label_objekt", {"state": "open", "labels": [{"name": "staging:fertig"}]}, "fertig_gebaut"),
    ("cli_label_text", {"state": "OPEN", "labels": ["staging:fertig"]}, "fertig_gebaut"),
    ("aufpasser_label_menge", {"state": "OPEN", "labels": {"staging:fertig"}}, "fertig_gebaut"),
    ("rest_zu", {"state": "closed"}, "zu"),
    ("cli_zu_mit_label", {"state": "CLOSED", "labels": ["staging:fertig"]}, "zu"),
    ("rest_verworfen", {"state": "closed", "state_reason": "not_planned"}, "verworfen"),
    ("cli_verworfen", {"state": "CLOSED", "stateReason": "NOT_PLANNED"}, "verworfen"),
    ("cli_erledigt", {"state": "CLOSED", "stateReason": "COMPLETED"}, "zu"),
    ("rest_duplikat", {"state": "closed", "state_reason": "duplicate"}, "verworfen"),
    ("cli_duplikat", {"state": "CLOSED", "stateReason": "DUPLICATE"}, "verworfen"),
    ("ohne_felder", {}, "offen"),
]


@pytest.mark.parametrize(("ticket", "erwartet"), [(t, e) for _, t, e in FAELLE], ids=[n for n, _, _ in FAELLE])
def test_stufe_und_die_zwei_fragen(ticket: dict[str, Any], erwartet: str) -> None:
    assert ticket_zustand.stufe(ticket) == erwartet
    assert ticket_zustand.fertig_gebaut(ticket) is (erwartet != "offen")
    assert ticket_zustand.endgueltig_zu(ticket) is (erwartet in {"zu", "verworfen"})


def test_wartet_auf_live_nur_ohne_freigabe() -> None:
    fertig = {"state": "OPEN", "labels": ["staging:fertig"]}
    assert ticket_zustand.wartet_auf_live(fertig, live_freigegeben=False) is True
    assert ticket_zustand.wartet_auf_live(fertig, live_freigegeben=True) is False
    assert ticket_zustand.wartet_auf_live({"state": "OPEN"}, live_freigegeben=False) is False


def test_lesen_holt_stufe_felder_und_faellt_bei_fehler_auf_none(monkeypatch: pytest.MonkeyPatch) -> None:
    gefragt: list[list[str]] = []

    def antwort(args: list[str], cwd: Path | None = None, timeout: float | None = None) -> Any:
        gefragt.append(args)
        return {"state": "OPEN", "labels": [{"name": "staging:fertig"}]} if args[2] == "7" else None

    monkeypatch.setattr(gh, "json_lauf", antwort)
    daten = ticket_zustand.lesen(7)
    assert daten is not None and ticket_zustand.stufe(daten) == "fertig_gebaut"
    assert ticket_zustand.lesen(8) is None
    assert "state,labels,stateReason" in gefragt[0]


# --- gh.py: Blocker-Regel fragt den Baustein (Abnahme 4) ---------------------


def test_blocker_offen_mit_staging_fertig_wird_losgelassen() -> None:
    deps = [
        {"number": 3, "state": "open", "labels": [{"name": "staging:fertig"}]},
        {"number": 4, "state": "open", "labels": []},
        {"number": 5, "state": "closed"},
    ]
    assert gh.offene_blocker(deps) == [4]
    assert gh.blocker_gruende(SKILL, deps, mit_commit=False) == ["#4 offen"]


def test_label_wissen_nur_im_baustein() -> None:
    """``staging:fertig`` und die Zustands-Werte stehen als Text nur im Baustein."""
    quelle = (SKILL / "to_spawn" / "gh.py").read_text(encoding="utf-8")
    assert '"staging:fertig"' not in quelle
    assert '"closed"' not in quelle and '"OPEN"' not in quelle
    aufpasser = (SKILL / "to_spawn" / "aufpasser.py").read_text(encoding="utf-8")
    assert '"CLOSED"' not in aufpasser and "staging:fertig" not in aufpasser


# --- aufpasser.py: Weg-Tests --------------------------------------------------


def _labels(w: Welt, monkeypatch: pytest.MonkeyPatch, daten: dict[str, list[str]]) -> None:
    datei = w.tmp / "gh_daten.json"
    datei.write_text(json.dumps({n: {"labels": namen} for n, namen in daten.items()}), encoding="utf-8")
    monkeypatch.setenv("GH_STUB_DATEN", str(datei))


def test_fall_0305_fertig_gebautes_ticket_startet_nicht(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Abnahme 3 / Stelle 4: offen + ``staging:fertig`` ohne Fenster → kein Start;
    offenes Ticket ohne Label wird weiter gestartet."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002|9003")
    _labels(welt, monkeypatch, {"9002": ["staging:fertig"]})
    assert welt.lauf(hang_min=90) == 0
    time.sleep(1)
    assert sorted(fenster_namen()) == ["bau 9003", f"wache {SPEC}"]
    assert welt.protokoll_bau.read_text(encoding="utf-8").splitlines() == ["9003"]


def test_stilles_fenster_fertig_gebaut_wird_gesichert_geschlossen(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stelle 5: stilles Fenster eines fertig gebauten Tickets geht zu, Sicherung vorher."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002")
    _labels(welt, monkeypatch, {"9002": ["staging:fertig"]})
    welt.worktree(9002, schmutzig=True)
    welt.fenster_still(9002, "sleep 3600")
    welt.stand_setzen("bau 9002", seit=time.time() - 20 * 60, hash=welt.hash_von("bau 9002"))
    assert welt.lauf(hang_min=90) == 0
    time.sleep(0.5)
    assert fenster_namen() == [f"wache {SPEC}"]
    assert _origin_hat_branch(welt, 9002)


def test_aufseher_startet_nach_solange_nicht_endgueltig_zu(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stelle 6: alle Tickets nur fertig gebaut (offen) → Aufseher wird weiter gebraucht."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002")
    _labels(welt, monkeypatch, {"9002": ["staging:fertig"]})
    tmux("new-window", "-d", "-t", f"={SITZUNG}", "-n", "platzhalter", "-c", str(welt.repo), "sleep 3600")
    tmux("kill-window", "-t", f"={SITZUNG}:wache {SPEC}")
    assert welt.lauf("--wache-vorlage", f"{welt.bin}/bau_protokoll.sh {{s}}{{resume}}") == 0
    time.sleep(1)
    assert f"wache {SPEC}" in fenster_namen()
    assert "bau 9002" not in fenster_namen()
    assert welt.protokoll_bau.read_text(encoding="utf-8").splitlines() == [SPEC]  # nur der Aufseher
