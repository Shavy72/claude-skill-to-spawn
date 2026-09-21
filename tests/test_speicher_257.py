"""Modul to_spawn.speicher (#257): Speicher-Schutz vor jedem Session-Start.

Deckt ``frei_mib``, ``grenzen`` und ``platz_frei`` ab, dazu die Struktur-Tests
für die Aufrufstellen in Skripten/Nest. Siehe ``docs/PLAN_257_fremdrepo.md``,
Abschnitt „Paket B — Speicher-Schutz“.

Attrappen nur für Rechenlogik: ``/proc/meminfo`` und ``/proc/<pid>/cmdline``
werden als Tmp-Dateien gestellt, nie der echte ``/proc`` der Testmaschine
(sonst schwankt das Ergebnis mit den Sessions, die gerade laufen).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"

sys.path.insert(0, str(SKILL))

from to_spawn import config, speicher

# --- 1. frei_mib ---------------------------------------------------------------


def test_frei_mib_liest_mem_available(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       16000000 kB\nMemAvailable:    1234567 kB\n",
        encoding="utf-8",
    )
    assert speicher.frei_mib(meminfo=meminfo) == 1205


def test_frei_mib_ohne_datei_kein_urteil(tmp_path: Path) -> None:
    assert speicher.frei_mib(meminfo=tmp_path / "gibt-es-nicht") is None


# --- 2. claude_sessions ----------------------------------------------------------


def _proc_mit_prozessen(basis: Path) -> Path:
    proc = basis / "proc"
    proc.mkdir()
    (proc / "100").mkdir()
    (proc / "100" / "cmdline").write_bytes(b"/home/x/.local/bin/claude\0--session-id\0abc\0")
    (proc / "101").mkdir()
    (proc / "101" / "cmdline").write_bytes(b"python3\0x.py\0")
    (proc / "102").mkdir()
    (proc / "102" / "cmdline").write_bytes(b"claude\0--resume\0")
    (proc / "abc").mkdir()  # kein Zahlenname — keine PID
    return proc


def test_claude_sessions_zaehlt_nur_claude_prozesse(tmp_path: Path) -> None:
    proc = _proc_mit_prozessen(tmp_path)
    assert speicher.claude_sessions(proc=proc) == 2


# --- 3. platz_frei ---------------------------------------------------------------

_KONFIG = {"speicher": {"min_frei_mib": 2048, "max_sessions": 6, "staffel_s": 20}}


def test_platz_frei_genug_speicher() -> None:
    frei, text = speicher.platz_frei(_KONFIG, frei=4096, sessions=2)
    assert frei is True
    assert "frei" in text


def test_platz_frei_speicher_knapp() -> None:
    frei, text = speicher.platz_frei(_KONFIG, frei=1200, sessions=2)
    assert frei is False
    assert "Speicher knapp" in text
    assert "1,2 GB" in text


def test_platz_frei_obergrenze_sessions() -> None:
    frei, text = speicher.platz_frei(_KONFIG, frei=4096, sessions=6)
    assert frei is False
    assert "6 Claude-Sessions" in text
    assert "Obergrenze 6" in text


def test_platz_frei_nicht_linux_kein_urteil(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speicher.sys, "platform", "win32")
    frei, text = speicher.platz_frei(_KONFIG, frei=None, sessions=None)
    assert frei is True
    assert "kein Urteil" in text


def test_defaults_hat_speicher_block() -> None:
    assert config.DEFAULTS["speicher"] == {
        "min_frei_mib": 2048,
        "max_sessions": 6,
        "staffel_s": 20,
    }


# --- 4. CLI ------------------------------------------------------------------


def _cli(*args: str, zusatz_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    umgebung = {**os.environ, **zusatz_env}
    return subprocess.run(
        [sys.executable, str(CLI), "speicher", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=umgebung,
        check=False,
    )


def _leerer_proc(tmp_path: Path) -> Path:
    proc = tmp_path / "proc-leer"
    proc.mkdir()
    return proc


def test_cli_speicher_knapp_exit_5(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo-knapp"
    meminfo.write_text("MemAvailable:    500000 kB\n", encoding="utf-8")
    ergebnis = _cli(
        zusatz_env={
            "TO_SPAWN_SPEICHER_MEMINFO": str(meminfo),
            "TO_SPAWN_SPEICHER_PROC": str(_leerer_proc(tmp_path)),
        }
    )
    assert ergebnis.returncode == 5, ergebnis.stdout + ergebnis.stderr
    assert "Speicher knapp" in ergebnis.stdout


def test_cli_speicher_frei_exit_0(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo-frei"
    meminfo.write_text(f"MemAvailable:    {8 * 1024 * 1024} kB\n", encoding="utf-8")
    ergebnis = _cli(
        zusatz_env={
            "TO_SPAWN_SPEICHER_MEMINFO": str(meminfo),
            "TO_SPAWN_SPEICHER_PROC": str(_leerer_proc(tmp_path)),
        }
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr


def test_cli_speicher_staffel_gibt_sekunden(tmp_path: Path) -> None:
    ergebnis = _cli("--staffel", zusatz_env={})
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert ergebnis.stdout.strip() == "20"


# --- 5. Struktur: Aufrufstellen in Skripten -------------------------------------


def test_spawn_srv_prueft_speicher_und_staffelt() -> None:
    text = (SKILL / "skripte" / "spawn_srv.sh").read_text(encoding="utf-8")
    assert "speicher" in text
    assert "--staffel" in text


def test_spawn_srv_bash_syntax_ok() -> None:
    ergebnis = subprocess.run(
        ["bash", "-n", str(SKILL / "skripte" / "spawn_srv.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_aufpasser_ruft_platz_frei() -> None:
    text = (SKILL / "to_spawn" / "aufpasser.py").read_text(encoding="utf-8")
    assert "speicher.platz_frei" in text


def test_wache_ruft_platz_frei() -> None:
    text = (SKILL / "skripte" / "wache.py").read_text(encoding="utf-8")
    assert "speicher.platz_frei" in text


def test_bau_ruft_platz_frei() -> None:
    text = (SKILL / "skripte" / "bau.py").read_text(encoding="utf-8")
    assert "speicher.platz_frei" in text


# --- 6. Nest: Swap + tmux-Dienst ------------------------------------------------


def test_nest_server_bash_syntax_ok() -> None:
    ergebnis = subprocess.run(
        ["bash", "-n", str(SKILL / "nest" / "nest_server.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_nest_server_nennt_swap_und_dienst() -> None:
    text = (SKILL / "nest" / "nest_server.sh").read_text(encoding="utf-8")
    for stueck in ("tmux-bau.service", "OOMScoreAdjust=-900", "exit-empty off", "/swapfile", "mkswap"):
        assert stueck in text, stueck


def test_nest_server_trockener_plan_nennt_swap_und_dienst() -> None:
    """Trockener Plan läuft ohne root/Netz (#211) — nur Text, keine Wirkung."""
    ergebnis = subprocess.run(
        ["bash", str(SKILL / "nest" / "nest_server.sh"), "--repo", "x/y", "--trocken"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert "Swap" in ergebnis.stdout
    assert "tmux-bau.service" in ergebnis.stdout
