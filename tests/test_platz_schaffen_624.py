"""#624: Wartender Start schafft Platz über den Aufpasser statt auf den Cron zu warten.

Gesamtgrenze voll durch ein fertiges, stilles Fenster ⇒ :func:`speicher.auf_platz_warten`
ruft die Tür :meth:`Aufpasser.platz_schaffen` und prüft sofort neu. Die Regeln, wann ein
Fenster zugeht, bleiben die des Aufpassers (offene Frage, arbeitend, Ticket offen ⇒ bleibt).

Nur Linux mit tmux (wie ``test_aufpasser_platz_592``).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem Erst-Test und wird als Parameter genannt.
from __future__ import annotations

import os
import time
import uuid

import pytest
from test_aufpasser_236 import (  # noqa: F401 — ``welt`` ist ein Fixture
    SOCKET,
    SPEC,
    Welt,
    fenster_namen,
    welt,
)
from test_aufpasser_platz_592 import _grenze, zu_9003  # noqa: F401

from to_spawn import aufpasser, briefkasten, speicher

pytestmark = pytest.mark.braucht("tmux")  # (#68)

FENSTER = "bau 9003"


def _aufpasser(welt: Welt) -> aufpasser.Aufpasser:
    e = aufpasser.Einstellungen(
        zustand=welt.zustand,
        tmux_socket=SOCKET,
        hang_min=90,
        bau_vorlage=welt.vorlage,
        deploy_muster=f"aufpasser-probe-niemals-{os.getpid()}",
    )
    e.karenz_platznot_s = 0.5
    e.exit_warten_s = 1
    return aufpasser.Aufpasser(e)


def _geschlossen(welt: Welt) -> bool:
    return any(f"„{FENSTER}“ geschlossen" in k for k in welt.kommentare())


def test_wartender_start_schafft_platz_ohne_cron(zu_9003: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    welt = zu_9003
    _grenze(welt, monkeypatch, max_sessions=2)
    welt.fenster_fake_claude(9003, str(uuid.uuid4()))
    pruefungen: list[int] = []

    def pruefen(_konfig: object) -> tuple[bool, str]:
        pruefungen.append(1)
        if FENSTER in fenster_namen():
            return False, f"{speicher.GESAMT_VOLL_TEXT}: schon 18 Claude-Sessions insgesamt"
        return True, "frei"

    def schaffen() -> object:
        return _aufpasser(welt).platz_schaffen()

    schlafen_aufrufe: list[float] = []
    zyklen = speicher.auf_platz_warten(
        None, pruefen=pruefen, schlafen=schlafen_aufrufe.append, platz_schaffen=schaffen
    )
    time.sleep(0.5)
    assert zyklen == 0 and schlafen_aufrufe == [], welt.log()
    assert FENSTER not in fenster_namen(), welt.log()
    assert _geschlossen(welt), welt.log()


def test_offene_frage_haelt_fenster(zu_9003: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    welt = zu_9003
    _grenze(welt, monkeypatch, max_sessions=2)
    # Wie #416: Ticket staging:fertig (Issue offen), Frage im Briefkasten ohne Antwort.
    daten = welt.tmp / "gh_daten.json"
    daten.write_text('{"9003": {"labels": ["staging:fertig"]}}', encoding="utf-8")
    monkeypatch.setenv("GH_STUB_DATEN", str(daten))
    monkeypatch.delenv("GH_STUB_ZU", raising=False)
    briefkasten.einwerfen(9003, "Zweig nach master?", "ja", 3, ["Test"], hauptbaum=welt.repo)
    welt.fenster_fake_claude(9003, str(uuid.uuid4()))
    _aufpasser(welt).platz_schaffen()
    time.sleep(0.5)
    assert FENSTER in fenster_namen(), welt.log()
    assert not _geschlossen(welt)


def test_arbeitendes_fenster_bleibt(zu_9003: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    welt = zu_9003
    _grenze(welt, monkeypatch, max_sessions=2)
    welt.fenster_fake_claude(9003, str(uuid.uuid4()))
    monkeypatch.setattr(aufpasser.Aufpasser, "pane_text", lambda _s, _z: aufpasser.ARBEITS_MARKER[0])
    _aufpasser(welt).platz_schaffen()
    time.sleep(0.5)
    assert FENSTER in fenster_namen(), welt.log()
    assert not _geschlossen(welt)


def test_offenes_ticket_bleibt(zu_9003: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    welt = zu_9003
    _grenze(welt, monkeypatch, max_sessions=2)
    monkeypatch.delenv("GH_STUB_ZU", raising=False)  # 9003 bleibt offen
    welt.fenster_fake_claude(9003, str(uuid.uuid4()))
    _aufpasser(welt).platz_schaffen()
    time.sleep(0.5)
    assert FENSTER in fenster_namen(), welt.log()
    assert not _geschlossen(welt)


def test_fehler_beim_platz_schaffen_start_wartet_weiter() -> None:
    zustand = {"n": 0}

    def pruefen(_konfig: object) -> tuple[bool, str]:
        zustand["n"] += 1
        if zustand["n"] < 3:
            return False, f"{speicher.GESAMT_VOLL_TEXT}: voll"
        return True, "frei"

    def kaputt() -> object:
        raise RuntimeError("Aufpasser kaputt")

    schlafen: list[float] = []
    zyklen = speicher.auf_platz_warten(None, pruefen=pruefen, schlafen=schlafen.append, platz_schaffen=kaputt)
    assert zyklen == 1 and len(schlafen) == 1
