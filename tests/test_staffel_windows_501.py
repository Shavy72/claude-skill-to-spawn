"""#501 Fall 1: Der Staffel-Hook findet und beendet die Session auch unter Windows.

Am PC (Praxistest 05.10.2026) blieb die Staffel stehen: ``staffel_stop.py`` suchte
die Claude-Session nur über ``/proc``. Diese Tests laufen gegen echte Prozesse — eine
Wegwerf-„Session“ (``claude.py``) mit einem Schläfer-Kind und einem Hook-Kind, das die
echten Hook-Funktionen ``claude_vorfahr`` und ``beenden`` aufruft. Keine Attrappe für
Prozesssuche oder Beenden.

Auf Linux prüfen sie dasselbe und belegen, dass sich dort nichts ändert (SIGTERM an
die Session selbst; Schläfer-Kinder sind Sache der Session).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]
HOOK_ORDNER = WURZEL / "skripte" / "hooks"
sys.path.insert(0, str(WURZEL))

from to_spawn import prozessbaum  # noqa: E402

WINDOWS = sys.platform == "win32"


def _ohne_fenster() -> dict[str, int]:
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {}


def _lebt(pid: int) -> bool:
    """Lebt ``pid``? Unabhängig vom geprüften Modul gemessen (tasklist bzw. /proc)."""
    if WINDOWS:
        aus = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **_ohne_fenster(),
        ).stdout
        return f'"{pid}"' in (aus or "")
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat[stat.rfind(")") + 2 :].split()[0] != "Z"


def _warte(bedingung, frist_s: float) -> bool:
    ende = time.monotonic() + frist_s
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.2)
    return bedingung()


@pytest.fixture
def sitzung(tmp_path: Path) -> Iterator[dict]:
    """Startet ``claude.py`` (Session-Stand-in) → Schläfer + Hook-Kind; liefert die Ergebnisse."""

    def starten(beenden: bool) -> dict:
        ergebnis = tmp_path / "ergebnis.json"
        hook = tmp_path / "hook_kind.py"
        hook.write_text(
            textwrap.dedent(
                f"""
                import json, os, sys
                sys.path.insert(0, {str(HOOK_ORDNER)!r})
                import staffel_stop
                ziel = staffel_stop.claude_vorfahr(os.getpid())
                ok = staffel_stop.beenden(ziel) if (ziel and {beenden!r}) else None
                with open({str(ergebnis)!r}, "w", encoding="utf-8") as f:
                    json.dump({{"ziel": ziel, "ok": ok}}, f)
                """
            ),
            encoding="utf-8",
        )
        schlaefer_datei = tmp_path / "schlaefer.pid"
        session = tmp_path / "claude.py"
        session.write_text(
            textwrap.dedent(
                f"""
                import subprocess, sys, time
                schlaefer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
                open({str(schlaefer_datei)!r}, "w").write(str(schlaefer.pid))
                subprocess.run([sys.executable, {str(hook)!r}])
                time.sleep(60)
                """
            ),
            encoding="utf-8",
        )
        env = dict(os.environ)
        # Obergrenze wie bei bau.py: nie über den Test-Prozess hinaus suchen/töten.
        env["BAU_LAUNCHER_PID"] = str(os.getpid())
        proc = subprocess.Popen([sys.executable, str(session)], env=env, **_ohne_fenster())
        prozesse.append(proc)
        assert _warte(ergebnis.exists, 30), "Hook-Kind hat kein Ergebnis geschrieben"
        time.sleep(0.3)
        daten = json.loads(ergebnis.read_text(encoding="utf-8"))
        daten["session_pid"] = proc.pid
        daten["schlaefer_pid"] = int(schlaefer_datei.read_text())
        daten["proc"] = proc
        return daten

    prozesse: list[subprocess.Popen] = []
    yield starten
    for proc in prozesse:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def test_hook_findet_session_ueber_vorfahren(sitzung) -> None:
    daten = sitzung(beenden=False)
    assert daten["ziel"] == daten["session_pid"]


def test_hook_beendet_session(sitzung) -> None:
    daten = sitzung(beenden=True)
    assert daten["ziel"] == daten["session_pid"]
    assert daten["ok"] is True
    assert _warte(lambda: daten["proc"].poll() is not None, 30), "Session lebt weiter"
    if WINDOWS:
        # Ganzer Baum: kein verwaister Schläfer (MCP-Server, Shells) bleibt zurück.
        assert _warte(lambda: not _lebt(daten["schlaefer_pid"]), 15), "Schläfer-Kind lebt weiter"
    else:
        prozessbaum.senden([daten["schlaefer_pid"]], __import__("signal").SIGKILL)


def test_suche_endet_an_launcher_grenze(tmp_path: Path) -> None:
    """Oberhalb von ``BAU_LAUNCHER_PID`` wird nie gesucht — auch nicht unter Windows."""
    ergebnis = tmp_path / "e.json"
    kind = tmp_path / "kind.py"
    kind.write_text(
        textwrap.dedent(
            f"""
            import json, os, sys
            sys.path.insert(0, {str(HOOK_ORDNER)!r})
            import staffel_stop
            json.dump(staffel_stop.claude_vorfahr(os.getpid()), open({str(ergebnis)!r}, "w"))
            """
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["BAU_LAUNCHER_PID"] = str(os.getpid())
    subprocess.run([sys.executable, str(kind)], env=env, check=True, timeout=60, **_ohne_fenster())
    assert json.loads(ergebnis.read_text()) is None
