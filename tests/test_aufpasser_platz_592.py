"""#592: Sessions geschlossener Tickets geben ihren Platz frei.

(1) Ein ``pytest`` sperrt nur das eigene Fenster (Fensterbaum oder Ticket-Worktree),
    nie den ganzen Server.
(2) Grenze voll + idle Session + Ticket zu ⇒ kurze Gegenprobe statt 15 min Karenz.

Nur Linux mit tmux (wie ``test_aufpasser_236``).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem Erst-Test und wird als Parameter genannt.
from __future__ import annotations

import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_aufpasser_236 import (  # noqa: F401 — ``welt`` ist ein Fixture
    SOCKET,
    SPEC,
    Welt,
    fenster_namen,
    welt,
)

from to_spawn import aufpasser


#: Ordnername der pytest-Attrappe — verankert das Deploy-Muster an diesem Test.
PROBE = "aufp592probe"


def _lauf_echtes_muster(welt: Welt) -> int:
    """Lauf mit dem echten ``DEPLOY_MUSTER`` des Aufpassers, verankert an der Attrappe.

    Auf dem Bau-Server laufen ständig fremde Deploy-/Gate-Prozesse (Deploy-Schlange,
    ``safe_deploy_vps.sh --nur-gate``); die sperren zu Recht serverweit. Der Anker
    ``<PROBE>/…`` lässt nur Prozesse dieses Tests auf das Muster passen — so prüft
    der Test genau, ob ``DEPLOY_MUSTER`` ein ``pytest`` serverweit zählt. Die
    Gate-Prüfung (``GATE_MUSTER``) sieht dagegen alle echten pytest-Läufe der Maschine.
    """
    return aufpasser.main(
        [
            "--tmux-socket",
            SOCKET,
            "--zustand",
            str(welt.zustand),
            "--deploy-muster",
            rf"{PROBE}[^ ]*/({aufpasser.DEPLOY_MUSTER})",
            "--bau-vorlage",
            welt.vorlage,
            "--hang-min",
            "90",
        ]
    )


def _pytest_attrappe(welt: Welt) -> Path:
    skript = welt.tmp / PROBE / "bin" / "pytest"
    skript.parent.mkdir(parents=True)
    skript.write_text("#!/bin/bash\nsleep 3600\n", encoding="utf-8")
    skript.chmod(0o755)
    return skript


@pytest.fixture()
def zu_9003(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Iterator[Welt]:
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9003")
    monkeypatch.setenv("GH_STUB_ZU", "9003")
    yield welt


def _still_seit_20_min(welt: Welt) -> None:
    welt.stand_setzen("bau 9003", seit=time.time() - 20 * 60, hash=welt.hash_von("bau 9003"))


def _beenden(prozess: subprocess.Popen[bytes]) -> None:
    os.killpg(prozess.pid, 15)
    prozess.wait()


# --- (1) pytest nur örtlich ------------------------------------------------


def test_fremdes_pytest_sperrt_nicht(zu_9003: Welt) -> None:
    welt = zu_9003
    welt.worktree(9003, schmutzig=False)
    fremd = subprocess.Popen(
        [str(_pytest_attrappe(welt))],
        cwd=str(welt.tmp / PROBE),
        start_new_session=True,
    )
    try:
        welt.fenster_still(9003, "sleep 3600")
        _still_seit_20_min(welt)
        assert _lauf_echtes_muster(welt) == 0
        time.sleep(0.5)
        assert "bau 9003" not in fenster_namen(), welt.log()
        assert any("„bau 9003“ geschlossen (Ticket zu)." in k for k in welt.kommentare())
    finally:
        _beenden(fremd)


def test_pytest_im_fensterbaum_haelt_fenster(zu_9003: Welt) -> None:
    welt = zu_9003
    welt.worktree(9003, schmutzig=False)
    welt.fenster_still(9003, str(_pytest_attrappe(welt)))
    _still_seit_20_min(welt)
    assert _lauf_echtes_muster(welt) == 0
    time.sleep(0.5)
    assert "bau 9003" in fenster_namen()
    assert "Gate läuft in diesem Fenster" in welt.log()
    assert welt.kommentare() == []


def test_pytest_im_ticket_worktree_haelt_fenster(zu_9003: Welt) -> None:
    welt = zu_9003
    wt = welt.worktree(9003, schmutzig=False)
    unter = wt / "tests"
    unter.mkdir()
    gate = subprocess.Popen([str(_pytest_attrappe(welt))], cwd=str(unter), start_new_session=True)
    try:
        welt.fenster_still(9003, "sleep 3600")
        _still_seit_20_min(welt)
        assert _lauf_echtes_muster(welt) == 0
        time.sleep(0.5)
        assert "bau 9003" in fenster_namen()
        assert "Gate läuft in diesem Fenster" in welt.log()
    finally:
        _beenden(gate)


# --- (2) kurze Karenz bei voller Grenze ------------------------------------


def _grenze(welt: Welt, monkeypatch: pytest.MonkeyPatch, max_sessions: int) -> None:
    proc = welt.tmp / "proc"
    for pid in ("101", "102"):
        (proc / pid).mkdir(parents=True)
        (proc / pid / "cmdline").write_bytes(b"/usr/bin/claude\0")
    meminfo = welt.tmp / "meminfo"
    meminfo.write_text("MemAvailable:   33554432 kB\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PROC", str(proc))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    (welt.repo / ".to-spawn" / "config.json").write_text(
        f'{{"speicher": {{"max_sessions": {max_sessions}}}}}', encoding="utf-8"
    )


def _lauf_kurze_karenz(welt: Welt) -> int:
    e = aufpasser.Einstellungen(
        zustand=welt.zustand,
        tmux_socket=SOCKET,
        hang_min=90,
        bau_vorlage=welt.vorlage,
        deploy_muster=f"aufpasser-probe-niemals-{os.getpid()}",
    )
    # Attribut statt Konstruktor-Argument: so bleibt der Test auch auf dem Stand
    # vor #592 lauffähig (Rot-Beweis zeigt das Verhalten, keinen TypeError).
    e.karenz_platznot_s = 0.5
    return aufpasser.lauf_mit_einstellungen(e)


def test_volle_grenze_schliesst_frisches_idle_fenster(zu_9003: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    welt = zu_9003
    _grenze(welt, monkeypatch, max_sessions=2)
    welt.fenster_fake_claude(9003, str(uuid.uuid4()))
    assert _lauf_kurze_karenz(welt) == 0
    time.sleep(0.5)
    assert "bau 9003" not in fenster_namen(), welt.log()
    assert any("„bau 9003“ geschlossen (Ticket zu)." in k for k in welt.kommentare())


def test_volle_grenze_ohne_session_json_bleibt_frisches_fenster(zu_9003: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Volle Grenze, aber kein Session-JSON (``aus_json`` False): keine kurze Karenz."""
    welt = zu_9003
    _grenze(welt, monkeypatch, max_sessions=2)
    welt.fenster_still(9003, f"FAKE_CLAUDE_OHNE_JSON=1 {welt.bin}/claude --session-id {uuid.uuid4()} x")
    assert _lauf_kurze_karenz(welt) == 0
    time.sleep(0.5)
    assert "bau 9003" in fenster_namen(), welt.log()
    assert welt.kommentare() == []


def test_ohne_volle_grenze_bleibt_frisches_idle_fenster(zu_9003: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    welt = zu_9003
    _grenze(welt, monkeypatch, max_sessions=5)
    welt.fenster_fake_claude(9003, str(uuid.uuid4()))
    assert _lauf_kurze_karenz(welt) == 0
    time.sleep(0.5)
    assert "bau 9003" in fenster_namen()
    assert welt.kommentare() == []
