"""#592 Fix 3: Ticket zu ⇒ Claude bekommt ``/exit``, das Fenster und die bau.py-Kette leben weiter.

Lebt im Fensterbaum eine Claude-Session, tippt der Aufpasser ``/exit`` und wartet
``exit_warten_s`` auf ihr Ende; was danach noch lebt, wird beendet. Die Shell dahinter
(bei bau.py: das nächste Ticket der Kette) läuft weiter. Ohne Claude im Baum wird das
Fenster wie bisher geschlossen.

Nur Linux mit tmux (wie ``test_aufpasser_236``).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem Erst-Test und wird als Parameter genannt.
from __future__ import annotations

import os
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


@pytest.fixture()
def zu_9003(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Iterator[Welt]:
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9003")
    monkeypatch.setenv("GH_STUB_ZU", "9003")
    welt.worktree(9003, schmutzig=False)
    yield welt


def _lauf(welt: Welt, exit_warten_s: float) -> int:
    welt.stand_setzen("bau 9003", seit=time.time() - 20 * 60, hash=welt.hash_von("bau 9003"))
    e = aufpasser.Einstellungen(
        zustand=welt.zustand,
        tmux_socket=SOCKET,
        hang_min=90,
        bau_vorlage=welt.vorlage,
        deploy_muster=f"aufpasser-probe-niemals-{os.getpid()}",
    )
    # Attribut statt Konstruktor-Argument: Rot-Beweis auf dem Stand vor dem Fix
    # zeigt das Verhalten, keinen TypeError.
    e.exit_warten_s = exit_warten_s
    return aufpasser.lauf_mit_einstellungen(e)


def _kette(welt: Welt, marker: Path, env: str = "") -> str:
    """Fensterbefehl wie bau.py: Claude, danach geht die Kette weiter."""
    sid = uuid.uuid4()
    return f'bash -c "{env}{welt.bin}/claude --session-id {sid} x; echo weiter > {marker}; sleep 3600"'


def _warte_auf(datei: Path, frist_s: float = 10.0) -> bool:
    ende = time.monotonic() + frist_s
    while time.monotonic() < ende:
        if datei.is_file():
            return True
        time.sleep(0.1)
    return False


def test_claude_endet_per_exit_kette_laeuft_weiter(zu_9003: Welt) -> None:
    welt = zu_9003
    marker = welt.tmp / "weiter.txt"
    welt.fenster_still(9003, _kette(welt, marker, "FAKE_CLAUDE_EXIT_BEI_EINGABE=1 "))
    assert _lauf(welt, exit_warten_s=10) == 0
    assert _warte_auf(marker), welt.log()
    assert marker.read_text(encoding="utf-8").strip() == "weiter"
    assert "bau 9003" in _fenster_nach_pause()
    assert any("„bau 9003“ geschlossen (Ticket zu)." in k and "/exit" in k for k in welt.kommentare()), welt.kommentare()


def test_claude_ignoriert_exit_wird_beendet_kette_laeuft_weiter(zu_9003: Welt) -> None:
    welt = zu_9003
    marker = welt.tmp / "weiter.txt"
    welt.fenster_still(9003, _kette(welt, marker))
    assert _lauf(welt, exit_warten_s=1) == 0
    assert _warte_auf(marker), welt.log()
    assert "bau 9003" in _fenster_nach_pause()


def test_ohne_claude_wird_fenster_geschlossen(zu_9003: Welt) -> None:
    welt = zu_9003
    welt.fenster_still(9003, "sleep 3600")
    assert _lauf(welt, exit_warten_s=1) == 0
    time.sleep(0.5)
    assert "bau 9003" not in fenster_namen(), welt.log()
    treffer = [k for k in welt.kommentare() if "„bau 9003“ geschlossen (Ticket zu)." in k]
    assert len(treffer) == 1 and "/exit" not in treffer[0], welt.kommentare()


def _fenster_nach_pause() -> list[str]:
    time.sleep(0.5)
    return fenster_namen()
