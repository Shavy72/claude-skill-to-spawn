"""Nachfolger ab fälligem Handoff ohne START-Datei (#1426, Teil A).

Eine Bau-Session schreibt und committet ihren Handoff, aber keine
``START_*_<N>.txt``. Ist ihr Fenster weg oder still, startet die Leiter die
Folge-Session trotzdem — über denselben Weg wie #542 (:mod:`to_spawn.eigener_handoff`,
``spawn.neustart``). Echt: Git-Worktree, Bau-Log, Leitstand, Handoff-Suche, Merker.
Attrappe nur ``spawn.neustart`` (würde echte Sessions starten) und das Fenster-Bild.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import (  # noqa: E402
    aufpasser,
    aufseher_stand,
    bau_log,
    eigener_handoff,
    leiter,
    leitstand,
    spawn,
)

SPEC = 91426
TICKET = 91427
JETZT = time.time()
MINUTE = 60.0
HANDOFF = f"docs/handoffs/HANDOFF_2026-11-20_{TICKET}.md"


def _git(ordner: Path, *args: str, zeit: float | None = None) -> None:
    env = dict(os.environ)
    if zeit is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{int(zeit)} +0000"
    subprocess.run(["git", "-C", str(ordner), *args], check=True, capture_output=True, env=env)


def _repo(ordner: Path) -> Path:
    ordner.mkdir(parents=True)
    _git(ordner, "init", "-q")
    _git(ordner, "config", "user.email", "t@t")
    _git(ordner, "config", "user.name", "t")
    (ordner / "README.md").write_text("x\n", encoding="utf-8")
    _git(ordner, "add", "README.md")
    _git(ordner, "commit", "-q", "-m", "start", zeit=JETZT - 7200)
    return ordner


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Hauptbaum + Ticket-Worktree, Leitstand in tmp, Session-Start vor 60 min."""
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path))
    haupt = _repo(tmp_path / "haupt")
    wt = _repo(tmp_path / f"wt-{TICKET}")
    gestartet: list[dict[str, Any]] = []

    def neustart(repo: Path, spec: int, ticket: int, konfig: dict, **kw: Any) -> int:
        gestartet.append({"ticket": ticket, **kw})
        return 0

    monkeypatch.setattr(spawn, "neustart", neustart)
    ts = datetime.fromtimestamp(JETZT - 60 * MINUTE, tz=timezone.utc).isoformat()
    log = bau_log.lauf_pfad(wt, TICKET)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"ts": ts, "typ": "session_start", "ticket": str(TICKET)}) + "\n",
        encoding="utf-8",
    )
    return {"haupt": haupt, "wt": wt, "gestartet": gestartet}


def _handoff(wt: Path, zeit: float) -> None:
    datei = wt / HANDOFF
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("# Handoff\nDie nächste Session macht weiter.\n", encoding="utf-8")
    _git(wt, "add", HANDOFF)
    _git(wt, "commit", "-q", "-m", "docs: Handoff", zeit=zeit)


class Umwelt:
    """Leiter-Umwelt: Fenster-Zustand fest vorgegeben, Handoff-Suche + Neustart echt."""

    def __init__(
        self,
        welt: dict[str, Any],
        fenster: str,
        still_min: int | None = None,
        offen: bool = True,
        prompt_datei: bool = False,
    ) -> None:
        self.haupt, self.wt = welt["haupt"], welt["wt"]
        self.fenster, self.still, self.offen = fenster, still_min, offen
        self.prompt_datei = prompt_datei

    def jetzt(self) -> float:
        return JETZT

    def lage(self, spec: int, ticket: int, seit: float | None) -> leiter.Lage:
        start = bau_log.letzter_session_start(self.wt, ticket)
        return leiter.Lage(
            offen=self.offen,
            fenster=self.fenster,
            still_min=self.still,
            kontext_k=None,
            prompt_datei=self.prompt_datei,
            ziel=None if self.fenster == aufseher_stand.KEIN_FENSTER else "=s:3",
            letzter_start=start,
            eigener_handoff=eigener_handoff.finde(self.wt, ticket, eigener_handoff.grenze(start, ticket)),
        )

    def handoff_k(self) -> float:
        return 250.0

    def worktree(self, ticket: int) -> Path:
        return self.wt

    def werkzeug(self) -> Any:
        class NieTippen:
            def tippen(self, ziel: str, text: str) -> None:
                raise AssertionError(f"nie tippen: {text}")

        return NieTippen()

    def folge_ab_handoff(self, spec: int, ticket: int, treffer: Any) -> int:
        return eigener_handoff.folge_starten(self.haupt, spec, ticket, treffer, sperren=False)


def _takt(welt: dict[str, Any], **lage: Any) -> leiter.Ergebnis:
    return leiter.eingreifen(welt["haupt"], SPEC, TICKET, umwelt=Umwelt(welt, **lage))


# (a) Fenster weg → genau ein Start; (d) zweiter Takt → kein zweiter Start.
def test_a_d_handoff_fenster_weg_genau_ein_start(welt: dict[str, Any]) -> None:
    _handoff(welt["wt"], JETZT - 10 * MINUTE)
    erg = _takt(welt, fenster=aufseher_stand.KEIN_FENSTER)
    assert erg.exit == 0, erg.zeile
    assert [g["handoff"] for g in welt["gestartet"]] == [HANDOFF]
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    erg = _takt(welt, fenster=aufseher_stand.KEIN_FENSTER)
    assert len(welt["gestartet"]) == 1, erg.zeile


# (a) aus Leiter-Stufe 2: Handoff angefordert, nur Handoff committet, START fehlt.
def test_a_stufe2_handoff_ohne_start_fenster_weg_startet(welt: dict[str, Any]) -> None:
    leitstand.setze_leiter_stufe(TICKET, 2, JETZT - 15 * MINUTE)
    _handoff(welt["wt"], JETZT - 10 * MINUTE)
    erg = _takt(welt, fenster=aufseher_stand.KEIN_FENSTER)
    assert erg.exit == 0, erg.zeile
    assert [g["handoff"] for g in welt["gestartet"]] == [HANDOFF]
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    _takt(welt, fenster=aufseher_stand.KEIN_FENSTER)
    assert len(welt["gestartet"]) == 1


# (b) Fenster arbeitet → kein Start, auch in Stufe 2.
@pytest.mark.parametrize("stufe", [0, 2])
def test_b_handoff_fenster_arbeitet_kein_start(welt: dict[str, Any], stufe: int) -> None:
    if stufe:
        leitstand.setze_leiter_stufe(TICKET, stufe, JETZT - 15 * MINUTE)
    _handoff(welt["wt"], JETZT - 10 * MINUTE)
    _takt(welt, fenster=aufseher_stand.ARBEITET, still_min=0)
    assert welt["gestartet"] == []


# (c) Fenster still ≥ ARBEITET_STILL_MIN (aus der einen Regel aufpasser.arbeitet).
def test_c_stufe2_handoff_fenster_still_startet(welt: dict[str, Any]) -> None:
    leitstand.setze_leiter_stufe(TICKET, 2, JETZT - 30 * MINUTE)
    _handoff(welt["wt"], JETZT - 25 * MINUTE)
    still = aufpasser.ARBEITET_STILL_MIN
    erg = _takt(welt, fenster=aufseher_stand.STILL, still_min=still)
    assert erg.exit == 0, erg.zeile
    assert [g["handoff"] for g in welt["gestartet"]] == [HANDOFF]


# (e) Ticket geschlossen → kein Start, Fenster weg oder still.
@pytest.mark.parametrize("fenster", [aufseher_stand.KEIN_FENSTER, aufseher_stand.STILL])
def test_e_ticket_zu_kein_start(welt: dict[str, Any], fenster: str) -> None:
    _handoff(welt["wt"], JETZT - 10 * MINUTE)
    _takt(welt, fenster=fenster, still_min=30, offen=False)
    assert welt["gestartet"] == []


def test_fenster_weg_ohne_handoff_nichts(welt: dict[str, Any]) -> None:
    erg = _takt(welt, fenster=aufseher_stand.KEIN_FENSTER)
    assert erg.exit == 0 and welt["gestartet"] == []
    assert leitstand.leiter_eintrag(TICKET) == (0, None)


# (f) START-Datei-Weg unverändert: Stufe 2 + START da → respawn, auch mit Handoff.
@pytest.mark.parametrize("mit_handoff", [False, True])
def test_f_start_datei_weg_unveraendert(mit_handoff: bool) -> None:
    treffer = eigener_handoff.Treffer(HANDOFF, JETZT - 10 * MINUTE) if mit_handoff else None
    lage = leiter.Lage(
        offen=True,
        fenster=aufseher_stand.STILL,
        still_min=25,
        kontext_k=None,
        prompt_datei=True,
        eigener_handoff=treffer,
    )
    schritt = leiter.entscheide(lage, leiter.Gemerkt(2, JETZT - 15 * MINUTE), JETZT, 250.0)
    assert schritt.aktion == "respawn"
