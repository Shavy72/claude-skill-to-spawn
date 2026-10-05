"""#598: In einer Fenster-Kette (``bau N; bau M``) zählt das Ticket des laufenden bau.py.

1. Läuft im Fenster „bau N“ schon bau.py M und ist M zu, endet Ms Session (``/exit``).
2. Hängt Ms Session dort, geht sie als Ticket M weiter (eigenes Fenster „bau M“,
   ``--resume`` mit Ms ID) — die Kette im alten Fenster läuft weiter.
3. Läuft das Folge-Ticket der Kette schon woanders, endet das ganze Fenster statt
   ``/exit`` — sonst startete die Kette ein zweites bau.py für dasselbe Ticket.

Nur Linux mit tmux (wie ``test_aufpasser_236``). bau.py ist eine Shell-Attrappe
(``bash …/bau.py <N> <sid>``), die die Claude-Attrappe startet.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem Erst-Test und wird als Parameter genannt.
from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from test_aufpasser_236 import (  # noqa: F401 — ``welt`` ist ein Fixture
    SITZUNG,
    SOCKET,
    SPEC,
    Welt,
    fenster_namen,
    tmux,
    welt,
)
from test_aufpasser_exit_592 import _lauf, _warte_auf

from to_spawn import aufpasser, prozessbaum

BAU_ATTRAPPE = """#!/bin/bash
# Attrappe für ``bau.py <N> <sid>``: startet die Claude-Attrappe mit Gesprächs-ID.
"{claude}" --session-id "$2" x
"""


def _kette(welt: Welt, tickets: list[int], marker: Path) -> tuple[str, dict[int, str]]:
    """Fensterbefehl ``bau N; bau M; …``, danach Marker (Kette lief weiter)."""
    bau = welt.tmp / "kette" / "bau.py"
    bau.parent.mkdir(exist_ok=True)
    bau.write_text(BAU_ATTRAPPE.format(claude=welt.bin / "claude"), encoding="utf-8")
    sids = {t: str(uuid.uuid4()) for t in tickets}
    schritte = "; ".join(f"bash {bau} {t} {sids[t]}" for t in tickets)
    befehl = f'bash -c "export FAKE_CLAUDE_EXIT_BEI_EINGABE=1; {schritte}; echo weiter > {marker}; sleep 3600"'
    return befehl, sids


def _pane_pids() -> list[int]:
    raus = tmux("list-panes", "-a", "-F", "#{pane_pid}")
    return [int(z) for z in raus.split() if z.strip().isdigit()]


def _bau_py_zahl(ticket: int) -> int:
    """Wie viele bau.py-Prozesse für ``ticket`` laufen in den Panes dieses tmux?"""
    zahl = 0
    for pane in _pane_pids():
        for pid in prozessbaum.baum(pane):
            argv = aufpasser._argv(pid)
            zahl += any(
                t.endswith("bau.py") and argv[i + 1] == str(ticket)
                for i, t in enumerate(argv[:-1])
            )
    return zahl


def _claude_pids(name: str) -> list[int]:
    ziel = [
        z
        for z in tmux(
            "list-windows", "-t", f"={SITZUNG}", "-F", "#{window_name}\t#{pane_pid}"
        ).splitlines()
    ]
    pane = next(int(z.split("\t")[1]) for z in ziel if z.split("\t")[0] == name)
    return [p for p in prozessbaum.baum(pane) if prozessbaum.ist_claude(p)]


def _warte_auf_claude(name: str, frist_s: float = 10.0) -> list[int]:
    ende = time.monotonic() + frist_s
    while time.monotonic() < ende:
        pids = _claude_pids(name)
        if pids:
            return pids
        time.sleep(0.1)
    return []


def test_echtes_ticket_ohne_prozess_nimmt_fensternamen() -> None:
    """Fallback: kein Prozess lesbar (PID 0) ⇒ Ticket aus dem Fensternamen."""
    f = aufpasser.Fenster(SITZUNG, SPEC, "1", "bau 7", "/", 0)
    assert aufpasser.echtes_ticket(f) == 7
    assert aufpasser.kette_folge(0, 7) == []


def test_kette_ticket_m_zu_session_endet(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Befund 1: Fenster „bau 9003“, bau.py 9004 läuft, 9004 ist zu ⇒ Ms Session endet."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9003|9004")
    monkeypatch.setenv("GH_STUB_ZU", "9003,9004")
    welt.worktree(9004, schmutzig=False)
    marker = welt.tmp / "weiter"
    befehl, _ = _kette(welt, [9004], marker)
    welt.fenster_still(9003, befehl)
    claude = _warte_auf_claude("bau 9003")
    assert claude, "Claude-Attrappe startete nicht"
    assert _lauf(welt, exit_warten_s=5) == 0
    assert _warte_auf(marker), welt.log()
    assert not any(prozessbaum.lebt(p) for p in claude), welt.log()
    assert any("geschlossen (Ticket zu)" in k for k in welt.kommentare()), (
        welt.kommentare()
    )


def test_kette_haenger_setzt_echtes_ticket_fort(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Befund 2: Hängt Ms Session im Fenster „bau 9003“, geht sie als 9004 weiter, Kette lebt."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9003|9004")
    monkeypatch.delenv("GH_STUB_ZU", raising=False)
    welt.worktree(9004, schmutzig=False)
    marker = welt.tmp / "weiter"
    befehl, sids = _kette(welt, [9004], marker)
    welt.fenster_still(9003, befehl)
    assert _warte_auf_claude("bau 9003")
    welt.stand_setzen(
        "bau 9003",
        stufe=1,
        seit=time.time() - 100 * 60,
        eingriff=time.time() - 100 * 60,
        hash=welt.hash_von("bau 9003"),
    )
    assert welt.lauf("--bau-vorlage", welt.vorlage_mit_claude()) == 0
    argv = (
        welt.protokoll_bau.read_text(encoding="utf-8").splitlines()
        if welt.protokoll_bau.is_file()
        else []
    )
    assert argv == [f"9004 --resume {sids[9004]}"], (argv, welt.log())
    assert _warte_auf(marker), welt.log()
    namen = fenster_namen()
    assert namen.count("bau 9003") == 1 and namen.count("bau 9004") == 1, namen


def test_kette_folge_laeuft_schon_kein_doppel_start(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Befund 3: Kette „bau 9003; bau 9004“, 9004 läuft schon im eigenen Fenster ⇒ kein zweites bau.py 9004."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9003|9004")
    monkeypatch.setenv("GH_STUB_ZU", "9003")
    welt.worktree(9003, schmutzig=False)
    welt.fenster_still(
        9004, 'python3 -c "import time; time.sleep(3600)" scripts/bau.py 9004'
    )
    marker = welt.tmp / "weiter"
    befehl, _ = _kette(welt, [9003, 9004], marker)
    welt.fenster_still(9003, befehl)
    assert _warte_auf_claude("bau 9003")
    assert _bau_py_zahl(9004) == 1
    assert _lauf(welt, exit_warten_s=5) == 0
    time.sleep(2)
    assert _bau_py_zahl(9004) == 1, welt.log()
    assert "bau 9003" not in fenster_namen(), welt.log()
    assert "bau 9004" in fenster_namen()
