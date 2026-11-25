"""#460 Fix-Runde 1: Gesamtgrenze über alle Claude-Prozesse, Ausweichwege der Platz-Vergabe,
alte Session-JSON bei wiederverwendeter PID, Reservierung endet mit sichtbarem Claude-Prozess.

Attrappe ist nur ``/proc``, ``meminfo`` und der Sessions-Ordner (Messwerte); ``speicher``,
``aufpasser._session_json_lesen`` und die Sperre sind echt.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pytest

from to_spawn import aufpasser, speicher, sperre

#: 16-GB-Rechner, max_sessions 6 → arbeitende Rechner-Grenze 12, Gesamtgrenze 24 (Messung 25.11.2026, Refs 284/624; vorher 9/18).
_KONFIG6 = {"speicher": {"max_sessions": 6, "min_frei_mib": 0, "staffel_s": 0}}
_EIGEN_TMUX = "/tmp/tmux-test/default,111,0"
_FREMD_TMUX = "/tmp/tmux-test/default,111,7"


def _welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, tmux: str | None = None) -> tuple[Path, Path]:
    proc = tmp_path / "proc"
    proc.mkdir()
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16384000 kB\nMemAvailable:   10485760 kB\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PROC", str(proc))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_SESSIONS", str(sessions))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PLATZ", str(tmp_path / "platz.json"))
    if tmux:
        monkeypatch.setenv("TMUX", tmux)
    else:
        monkeypatch.delenv("TMUX", raising=False)
    return proc, sessions


def _stat(pid: int, ppid: int, start: int, name: str = "claude") -> str:
    """``/proc/<pid>/stat``: Feld 4 = ppid, Feld 22 = starttime."""
    return f"{pid} ({name}) S {ppid} " + " ".join(["0"] * 17) + f" {start} 0 0\n"


def _claude(
    proc: Path,
    sessions: Path,
    pid: int,
    status: str | None,
    *,
    tmux: str = _FREMD_TMUX,
    ppid: int = 1,
    start: int = 1000,
    json_start: int | None = None,
) -> None:
    """Claude-Prozess ``pid`` in tmux-Sitzung ``tmux``; ``status`` = Feld der Session-JSON."""
    ordner = proc / str(pid)
    ordner.mkdir()
    (ordner / "cmdline").write_bytes(b"/usr/bin/claude\0--resume\0abc\0")
    (ordner / "environ").write_bytes(f"PATH=/usr/bin\0TMUX={tmux}\0".encode())
    (ordner / "stat").write_text(_stat(pid, ppid, start), encoding="utf-8")
    if status is not None:
        daten: dict[str, object] = {"pid": pid, "status": status}
        if json_start is not None:
            daten["procStart"] = str(json_start)
        (sessions / f"{pid}.json").write_text(json.dumps(daten), encoding="utf-8")


def _starter_sichtbar(proc: Path, start: int = 4242) -> None:
    """Der Testprozess selbst (der Platz-Nehmer) erscheint lebend im Attrappen-``/proc``."""
    ordner = proc / str(os.getpid())
    ordner.mkdir()
    (ordner / "cmdline").write_bytes(b"/usr/bin/python3\0bau.py\0")
    (ordner / "stat").write_text(_stat(os.getpid(), 1, start, "python3"), encoding="utf-8")


# --- 1. Gesamtgrenze über alle Claude-Prozesse (still + arbeitend) ------------------


def test_gesamtgrenze_18_greift_bei_vielen_stillen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """23 stille + 1 arbeitende fremde Session: arbeitende 1 von 12, aber insgesamt 24 von 24 → voll (284: war 17+1/18)."""
    proc, sessions = _welt(tmp_path, monkeypatch, tmux=_EIGEN_TMUX)
    for pid in range(201, 224):
        _claude(proc, sessions, pid, "idle")
    _claude(proc, sessions, 301, "busy")
    frei, text = speicher.platz_frei_hier(_KONFIG6)
    assert not frei, text
    assert "24" in text and "insgesamt" in text and "still" in text, text
    assert not text.startswith("Rechner voll: schon"), text  # unterscheidbar von „arbeitende voll“


def test_unter_gesamtgrenze_frei(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """22 stille + 1 arbeitende = 23 < 24 → frei (284: war 16+1 < 18)."""
    proc, sessions = _welt(tmp_path, monkeypatch, tmux=_EIGEN_TMUX)
    for pid in range(201, 223):
        _claude(proc, sessions, pid, "idle")
    _claude(proc, sessions, 301, "busy")
    frei, text = speicher.platz_frei_hier(_KONFIG6)
    assert frei, text


def test_arbeitende_rechner_grenze_meldet_weiter_rechner_voll(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """12 arbeitende fremde (284: war 9): alte Meldung „Rechner voll“ bleibt, Gesamtgrenze greift noch nicht."""
    proc, sessions = _welt(tmp_path, monkeypatch, tmux=_EIGEN_TMUX)
    for pid in range(301, 313):
        _claude(proc, sessions, pid, "busy")
    frei, text = speicher.platz_frei_hier(_KONFIG6)
    assert (frei, text) == (
        False,
        "Rechner voll: schon 12 Claude-Sessions auf dem Rechner (Obergrenze 12)",
    )


def test_gesamt_grenze_ist_doppelte_rechner_grenze() -> None:
    assert speicher.gesamt_grenze(16 * 1024 * 1024) == 2 * speicher.rechner_grenze(16 * 1024 * 1024) == 24


# --- 2. Ausweichwege von _pruefen_und_belegen ------------------------------------


def _immer_frei(_konfig: object) -> tuple[bool, str]:
    return True, "frei (Test)"


def test_sperre_belegt_meldet_nicht_frei(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hält ein anderer Start die Platz-Sperre länger als die Wartezeit → diesmal „nicht frei“."""
    _welt(tmp_path, monkeypatch)
    monkeypatch.setattr(speicher, "SPERRE_WARTEN_S", 0.2)
    with sperre.halte(speicher._platz_pfad().with_suffix(".sperre")) as gehalten:
        assert gehalten
        anfang = time.monotonic()
        frei, text = speicher._pruefen_und_belegen(_KONFIG6, _immer_frei, None)
        assert time.monotonic() - anfang < 5
    assert (frei, text) == (False, "Platz-Vergabe gerade belegt (anderer Start)")
    assert not speicher._platz_pfad().exists()


def test_sperre_nicht_oeffenbar_prueft_ohne_sperre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Sperr-Ordner nicht anlegbar (OSError) → ohne Sperre prüfen, Warnung, Start erlaubt."""
    _welt(tmp_path, monkeypatch)
    blocker = tmp_path / "ist_datei"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PLATZ", str(blocker / "platz.json"))
    with caplog.at_level(logging.WARNING, logger="to_spawn.speicher"):
        assert speicher._pruefen_und_belegen(_KONFIG6, _immer_frei, None) == (
            True,
            "frei (Test)",
        )
    assert "Platz-Sperre nicht nutzbar" in caplog.text


def test_schreibfehler_beim_belegen_startet_trotzdem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Platz-Datei nicht schreibbar (ist ein Ordner) → Warnung, Start trotzdem erlaubt."""
    _welt(tmp_path, monkeypatch)
    (tmp_path / "platz.json").mkdir()
    with caplog.at_level(logging.WARNING, logger="to_spawn.speicher"):
        assert speicher._pruefen_und_belegen(_KONFIG6, _immer_frei, None) == (
            True,
            "frei (Test)",
        )
    assert "Platz nicht vermerkt" in caplog.text


# --- 3. alte Session-JSON / wiederverwendete PID -----------------------------------


def test_alte_session_json_bei_wiederverwendeter_pid_zaehlt_nicht_als_still(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON sagt idle, gehört aber zu einem früheren Prozess mit derselben PID → zählt als arbeitend."""
    proc, sessions = _welt(tmp_path, monkeypatch)
    _claude(proc, sessions, 201, "idle", start=500, json_start=400)
    _claude(proc, sessions, 202, "idle", start=600, json_start=600)
    assert speicher.claude_sessions() == 1


def test_gemeinsame_lese_funktion_prueft_proc_start(tmp_path: Path) -> None:
    """``aufpasser._session_json_lesen`` nutzt denselben procStart-Abgleich (lebender Testprozess)."""
    ordner = tmp_path / ".claude" / "sessions"
    ordner.mkdir(parents=True)
    pid = os.getpid()
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    echt = stat[stat.rfind(")") + 2 :].split()[19]
    datei = ordner / f"{pid}.json"
    datei.write_text(json.dumps({"pid": pid, "sessionId": "s1", "procStart": "1"}), encoding="utf-8")
    assert aufpasser._session_json_lesen(pid, "@1", "%1", tmp_path) is None
    datei.write_text(json.dumps({"pid": pid, "sessionId": "s1", "procStart": echt}), encoding="utf-8")
    info = aufpasser._session_json_lesen(pid, "@1", "%1", tmp_path)
    assert info is not None and info.session_id == "s1"


# --- 4. Reservierung: Start über 120 s, Ende sobald Prozess sichtbar ----------------

_KONFIG3 = {"speicher": {"max_sessions": 3, "min_frei_mib": 0, "staffel_s": 0}}


def test_start_ueber_120_s_ueberbucht_nicht(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Platz-Nehmer lebt noch, Claude noch nicht da: nach 121 s zählt der Platz weiter."""
    proc, sessions = _welt(tmp_path, monkeypatch)
    _starter_sichtbar(proc)
    _claude(proc, sessions, 201, "busy")
    _claude(proc, sessions, 202, "busy")
    assert speicher.auf_platz_warten(_KONFIG3, schlafen=lambda _s: pytest.fail("darf nicht warten")) == 0
    jetzt = time.time()
    monkeypatch.setattr(speicher.time, "time", lambda: jetzt + speicher.RESERVIERUNG_S + 1)
    frei, text = speicher.platz_frei(_KONFIG3)
    assert not frei and "schon 3" in text, text
    monkeypatch.setattr(speicher.time, "time", lambda: jetzt + speicher.RESERVIERUNG_MAX_S + 1)
    assert speicher.platz_frei(_KONFIG3)[0] is True


def test_reservierung_endet_sobald_claude_prozess_sichtbar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Der gestartete Claude-Prozess (Enkel des Platz-Nehmers) zählt selbst — der Platz nicht mehr doppelt."""
    proc, sessions = _welt(tmp_path, monkeypatch)
    _starter_sichtbar(proc)
    _claude(proc, sessions, 201, "busy")
    assert speicher.auf_platz_warten(_KONFIG3, schlafen=lambda _s: pytest.fail("darf nicht warten")) == 0
    assert speicher.platz_frei(_KONFIG3)[0] is True  # 1 arbeitend + 1 Platz = 2 < 3
    # Sandbox-Hülle (bwrap) zwischen Starter und Claude
    huelle = proc / "450"
    huelle.mkdir()
    (huelle / "cmdline").write_bytes(b"/usr/bin/bwrap\0")
    (huelle / "stat").write_text(_stat(450, os.getpid(), 4300, "bwrap"), encoding="utf-8")
    _claude(proc, sessions, 451, None, ppid=450, start=4301)
    frei, text = speicher.platz_frei(_KONFIG3)
    assert frei and "2 Claude-Sessions" in text, text  # ohne Freigabe: 2 + Platz = 3 → voll


def test_neuer_platz_desselben_starters_ersetzt_den_alten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kette (bau.py startet nach dem Ende das nächste Ticket): je Starter höchstens ein Platz."""
    proc, _sessions = _welt(tmp_path, monkeypatch)
    _starter_sichtbar(proc)
    for _ in range(2):
        assert speicher.auf_platz_warten(_KONFIG3, schlafen=lambda _s: pytest.fail("darf nicht warten")) == 0
    assert len(json.loads(speicher._platz_pfad().read_text(encoding="utf-8"))) == 1
