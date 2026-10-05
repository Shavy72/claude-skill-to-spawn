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


def test_handoff_mit_runden_suffix_zaehlt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``HANDOFF_<datum>_<N>_runde2.md`` ist ein Handoff von Ticket N — fremde Tickets nie.

    Die Projekt-Kopie des Hooks kannte das Suffix schon; seit #501 lebt es nur noch hier.
    """
    sys.path.insert(0, str(HOOK_ORDNER))
    import staffel_stop

    monkeypatch.delenv("BAU_STAFFEL_FINGERABDRUCK", raising=False)
    muster = staffel_stop.handoff_muster("192")
    assert muster.match("HANDOFF_2026-10-05_192.md")
    assert muster.match("HANDOFF_2026-10-05_192_runde2.md")
    assert muster.match("HANDOFF_2026-10-05_192_r2.md")
    assert not muster.match("HANDOFF_2026-10-05_1920.md")
    assert not muster.match("HANDOFF_2026-10-05_waechter_192.md")

    seit = time.time() - 5
    (tmp_path / "HANDOFF_2026-10-05_1920.md").write_text("Staffel: weiter\n", encoding="utf-8")
    datei = tmp_path / "HANDOFF_2026-10-05_192_runde2.md"
    datei.write_text("Staffel: weiter\n", encoding="utf-8")
    assert staffel_stop.frischer_handoff([tmp_path], "192", seit) == datei


def _kette(tmp_path: Path, namen: list[str], *, grenze_index: int | None) -> int | None:
    """Startet ``namen[0]`` → ``namen[1]`` → … → Hook-Kind; liefert ``claude_vorfahr`` des Kinds.

    Jedes Glied ist ein eigenes Python-Skript mit seinem Namen in der Kommandozeile
    (``claude.py`` zählt als Session, ``bau.py`` als Launcher). ``grenze_index`` macht
    dieses Glied zur ``BAU_LAUNCHER_PID`` — wie ``bau.py`` es für seine Sessions setzt.
    """
    ergebnis = tmp_path / "vorfahr.json"
    kind = tmp_path / "hook_kind.py"
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
    naechstes = kind
    for index in reversed(range(len(namen))):
        ordner = tmp_path / f"glied{index}"
        ordner.mkdir()
        glied = ordner / namen[index]
        setzt_grenze = index == grenze_index
        glied.write_text(
            textwrap.dedent(
                f"""
                import os, subprocess, sys
                env = dict(os.environ)
                if {setzt_grenze!r}:
                    env["BAU_LAUNCHER_PID"] = str(os.getpid())
                subprocess.run([sys.executable, {str(naechstes)!r}], env=env, timeout=60)
                """
            ),
            encoding="utf-8",
        )
        naechstes = glied
    env = dict(os.environ)
    env.pop("BAU_LAUNCHER_PID", None)
    subprocess.run([sys.executable, str(naechstes)], env=env, check=True, timeout=90, **_ohne_fenster())
    return json.loads(ergebnis.read_text())


def test_grenze_schuetzt_session_oberhalb_des_launchers(tmp_path: Path) -> None:
    """claude.py → Launcher (Grenze) → Hook-Kind: die Session oberhalb bleibt unangetastet.

    Gegenprobe ohne Grenze: dieselbe Kette findet ``claude.py`` — der Test wird also
    unabhängig davon rot, ob über pytest eine echte Claude-Session läuft.
    """
    (tmp_path / "mit").mkdir()
    assert _kette(tmp_path / "mit", ["claude.py", "launcher.py"], grenze_index=1) is None
    (tmp_path / "ohne").mkdir()
    assert _kette(tmp_path / "ohne", ["claude.py", "launcher.py"], grenze_index=None) is not None


def test_suche_bricht_an_bau_py_ab(tmp_path: Path) -> None:
    """claude.py → bau.py → Hook-Kind ohne Grenze: ``bau.py`` beendet die Suche (nie darüber)."""
    assert _kette(tmp_path, ["claude.py", "bau.py"], grenze_index=None) is None


def test_nachlauf_schreibt_ergebnis_ins_protokoll(tmp_path: Path) -> None:
    """Der Nachläufer protokolliert sein Ergebnis; ein gescheitertes taskkill ist eine WARNUNG."""
    tot = subprocess.Popen([sys.executable, "-c", "pass"], **_ohne_fenster())
    tot.wait(timeout=30)
    protokoll = tmp_path / "staffel.json.nachlauf.log"
    assert prozessbaum._nachlauf(tot.pid, tot.pid, frist=1.0, protokoll=protokoll) == 0
    zeile = protokoll.read_text(encoding="utf-8")
    if WINDOWS:
        assert "WARNUNG taskkill /T /F" in zeile and "Exit 0" not in zeile, zeile
    else:
        assert zeile.split(" ", 1)[1].startswith("OK "), zeile


def test_beenden_einer_toten_pid_stoesst_nichts_an(tmp_path: Path) -> None:
    """Ziel schon weg → False, kein Nachläufer (unter Windows sonst taskkill auf eine fremde, recycelte PID)."""
    tot = subprocess.Popen([sys.executable, "-c", "pass"], **_ohne_fenster())
    tot.wait(timeout=30)
    protokoll = tmp_path / "nachlauf.log"
    assert prozessbaum.session_beenden(tot.pid, protokoll) is False
    time.sleep(1.0)
    assert not protokoll.exists()


@pytest.mark.skipif(not WINDOWS, reason="Win32_Process-Momentaufnahme gibt es nur unter Windows")
def test_prozessliste_traegt_kommandozeile_mit_zeilenumbruch() -> None:
    """Zeilenumbruch und Nicht-ASCII in Kommandozeilen dürfen die Momentaufnahme nicht leeren.

    Probesitz-Punkt 6 (2026-10-05) blieb rot: eine fremde Kommandozeile mit rohem
    Steuerzeichen brach ``json.loads`` — leere Liste, kein Vorfahr, keine Staffel. Das
    Zeichen (0x1A) entstand, weil PowerShell im OEM-Zeichensatz statt UTF-8 ausgab.
    """
    mit_umbruch = subprocess.Popen(
        [sys.executable, "-c", "import time\ntime.sleep(60)  # „Staffel“ ✓"], **_ohne_fenster()
    )
    try:
        time.sleep(1.0)
        prozesse = prozessbaum._prozesse_windows()
        assert mit_umbruch.pid in prozesse
        assert os.getpid() in prozesse
        assert "„Staffel“ ✓" in prozesse[mit_umbruch.pid][1]
    finally:
        mit_umbruch.kill()
        mit_umbruch.wait(timeout=30)
