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
    # Große Frist, kurze Laufzeit: Claude endete durch /exit, nicht durch Zwangsbeenden.
    start = time.monotonic()
    assert _lauf(welt, exit_warten_s=60) == 0
    assert time.monotonic() - start < 20, welt.log()
    assert "nach /exit noch da" not in welt.log(), welt.log()
    assert _warte_auf(marker), welt.log()
    assert marker.read_text(encoding="utf-8").strip() == "weiter"
    assert "bau 9003" in _fenster_nach_pause()
    assert any("„bau 9003“ geschlossen (Ticket zu)." in k and "Per /exit beendet" in k for k in welt.kommentare()), (
        welt.kommentare()
    )


def test_claude_ignoriert_exit_wird_beendet_kette_laeuft_weiter(zu_9003: Welt) -> None:
    welt = zu_9003
    marker = welt.tmp / "weiter.txt"
    welt.fenster_still(9003, _kette(welt, marker))
    assert _lauf(welt, exit_warten_s=1) == 0
    assert _warte_auf(marker), welt.log()
    assert "bau 9003" in _fenster_nach_pause()
    treffer = [k for k in welt.kommentare() if "„bau 9003“ geschlossen (Ticket zu)." in k]
    assert len(treffer) == 1 and "Per /exit beendet" not in treffer[0], welt.kommentare()
    assert "/exit ohne Wirkung" in treffer[0], welt.kommentare()


def test_kinder_von_claude_enden_nach_exit(zu_9003: Welt) -> None:
    """Ein SIGHUP-ignorierendes Kind (wie ein MCP-Server) überlebt Claudes /exit nicht."""
    welt = zu_9003
    marker = welt.tmp / "weiter.txt"
    kind_datei = welt.tmp / "kind.pid"
    env = f"FAKE_CLAUDE_EXIT_BEI_EINGABE=1 FAKE_CLAUDE_KIND_PID={kind_datei} "
    welt.fenster_still(9003, _kette(welt, marker, env))
    assert _warte_auf(kind_datei), welt.log()
    kind = int(kind_datei.read_text(encoding="utf-8").strip())
    assert _lauf(welt, exit_warten_s=30) == 0
    assert _warte_auf(marker), welt.log()
    assert not _pid_lebt(kind), welt.log()
    assert "bau 9003" in _fenster_nach_pause()


def test_kette_im_fenster_kein_eingriff_fuer_fremdes_ticket(zu_9003: Welt) -> None:
    """Fenster „bau 9003“, aber bau.py arbeitet schon an 9004 (Kette): nichts schließen."""
    welt = zu_9003
    welt.fenster_still(9003, 'python3 -c "import time; time.sleep(3600)" scripts/bau.py 9004')
    assert _lauf(welt, exit_warten_s=1) == 0
    assert "bau 9003" in _fenster_nach_pause(), welt.log()
    assert welt.kommentare() == []
    assert "bau.py arbeitet an Ticket 9004" in welt.log(), welt.log()


def _pid_lebt(pid: int) -> bool:
    """Lebt ``pid`` noch (Zombies zählen als tot)?"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return stat[stat.rfind(")") + 2] != "Z"


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


# --- Fehlerwege (Review 5194800) ---------------------------------------------


def test_claude_lebt_nach_beenden_offen_keine_meldung(zu_9003: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """``_fenster_schliessen`` → „offen“: nichts melden, Stand-Eintrag bleibt."""
    welt = zu_9003
    monkeypatch.setattr(aufpasser, "_pids_beenden", lambda pids, wer: False)
    welt.fenster_still(9003, f"{welt.bin}/claude --session-id {uuid.uuid4()} x")
    assert _lauf(welt, exit_warten_s=1) == 0
    assert "bau 9003" in _fenster_nach_pause()
    assert "Claude lebt noch — nicht als geschlossen gemeldet" in welt.log(), welt.log()
    assert welt.kommentare() == []
    assert any(k.endswith("/bau 9003") for k in welt.stand().get("fenster", {}))


def _einstellungen(welt: Welt) -> aufpasser.Einstellungen:
    return aufpasser.Einstellungen(
        zustand=welt.zustand,
        tmux_socket=SOCKET,
        hang_min=90,
        bau_vorlage=welt.vorlage,
        deploy_muster=f"aufpasser-probe-niemals-{os.getpid()}",
    )


def test_worktree_nicht_lesbar_kein_eingriff(welt: Welt, caplog: pytest.LogCaptureFixture) -> None:
    f = aufpasser.Fenster(sitzung="spec-x", spec="x", index="1", name="bau 9003", pfad="", pane_pid=0)
    kein_repo = welt.tmp / "kein-repo"
    kein_repo.mkdir()
    with caplog.at_level("ERROR"):
        assert aufpasser.Aufpasser(_einstellungen(welt)).vor_eingriff(f, "", kein_repo) is False
    assert "Worktree nicht lesbar" in caplog.text


def test_ohne_worktree_zaehlt_nur_fensterbaum(welt: Welt) -> None:
    """``worktree`` None (z. B. ``wache``): ein pytest außerhalb des Fensterbaums sperrt nicht."""
    pytest_datei = welt.tmp / "gate" / "pytest"
    pytest_datei.parent.mkdir()
    pytest_datei.write_text("#!/bin/bash\nsleep 3600\n", encoding="utf-8")
    pytest_datei.chmod(0o755)
    eltern = subprocess.Popen(["bash", "-c", f"{pytest_datei}; true"], cwd=str(welt.tmp), start_new_session=True)
    try:
        time.sleep(0.5)
        a = aufpasser.Aufpasser(_einstellungen(welt))
        draussen = aufpasser.Fenster(sitzung="s", spec="x", index="1", name="wache x", pfad="", pane_pid=0)
        assert a.gate_im_fenster(draussen, None) is None
        drin = aufpasser.Fenster(sitzung="s", spec="x", index="1", name="wache x", pfad="", pane_pid=eltern.pid)
        kopf = a.gate_im_fenster(drin, None)
        assert kopf is not None and str(pytest_datei) in kopf, kopf
    finally:
        os.killpg(eltern.pid, 15)
        eltern.wait()
