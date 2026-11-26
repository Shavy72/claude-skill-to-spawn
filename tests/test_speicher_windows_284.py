"""Windows-Speicher-Schranke (#284): echtes Urteil und automatisches Nachholen.

Belegt die Abnahme von #284:
1. Unter Windows liefert ``speicher.platz_frei`` ein echtes Urteil (frei / knapp / voll)
   mit Zahl hinter derselben Schnittstelle wie unter Linux.
2. Dieselben Schwellen-Namen (``min_frei_mib``, ``max_sessions``, ``staffel_s``) aus einer Quelle.
3. Künstlich knapper Speicher hält den Start zurück und holt ihn automatisch nach, sobald
   wieder Luft ist — kein Abbruch, keine Frage an den Nutzer.
4. Die Meldung „kein Urteil (kein Linux)“ gibt es nicht mehr.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"

sys.path.insert(0, str(SKILL))

from to_spawn import config, speicher

_KONFIG = {"speicher": {"min_frei_mib": 2048, "max_sessions": 6, "staffel_s": 20}}


def _windows_ohne_testtuer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Schaltet ``speicher`` auf Windows ohne ``/proc``-Test-Türen."""
    monkeypatch.setattr(speicher.sys, "platform", "win32")
    monkeypatch.delenv("TO_SPAWN_SPEICHER_MEMINFO", raising=False)
    monkeypatch.delenv("TO_SPAWN_SPEICHER_PROC", raising=False)
    monkeypatch.setenv("TO_SPAWN_SPEICHER_SESSIONS", str(tmp_path / "sessions"))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PLATZ", str(tmp_path / "platz.json"))


# --- 1. Urteil unter Windows über gemeinsame Schnittstelle ------------------


def test_windows_platz_frei_echtes_urteil_frei(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _windows_ohne_testtuer(monkeypatch, tmp_path)
    monkeypatch.setattr(speicher, "windows_speicher_mib", lambda: (12288, 32768))
    monkeypatch.setattr(speicher, "windows_sessions", lambda *, mit_stillen=False: 1)

    frei, text = speicher.platz_frei(_KONFIG)

    assert frei is True
    assert "Speicher frei" in text
    assert "12 GB" in text
    assert "1 Claude-Sessions" in text
    assert "Obergrenze 6" in text
    assert "Mindestmaß 2 GB" in text
    assert "kein Linux" not in text
    assert "kein Urteil" not in text


def test_windows_platz_frei_speicher_knapp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _windows_ohne_testtuer(monkeypatch, tmp_path)
    monkeypatch.setattr(speicher, "windows_speicher_mib", lambda: (1200, 32768))
    monkeypatch.setattr(speicher, "windows_sessions", lambda *, mit_stillen=False: 1)

    frei, text = speicher.platz_frei(_KONFIG)

    assert frei is False
    assert "Speicher knapp" in text
    assert "1,2 GB" in text
    assert "Mindestmaß 2 GB" in text


def test_windows_platz_frei_obergrenze_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _windows_ohne_testtuer(monkeypatch, tmp_path)
    monkeypatch.setattr(speicher, "windows_speicher_mib", lambda: (16384, 32768))
    monkeypatch.setattr(speicher, "windows_sessions", lambda *, mit_stillen=False: 6)

    frei, text = speicher.platz_frei(_KONFIG)

    assert frei is False
    assert "6 Claude-Sessions" in text
    assert "Obergrenze 6" in text


def test_windows_unmessbar_ohne_kein_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _windows_ohne_testtuer(monkeypatch, tmp_path)
    monkeypatch.setattr(speicher, "windows_speicher_mib", lambda: (None, None))
    monkeypatch.setattr(speicher, "windows_sessions", lambda *, mit_stillen=False: None)

    frei, text = speicher.platz_frei(_KONFIG)

    assert frei is True
    assert "kein Urteil" in text
    assert "kein Linux" not in text


def test_speicher_quellcode_ohne_kein_linux() -> None:
    quelltext = (SKILL / "to_spawn" / "speicher.py").read_text(encoding="utf-8")
    assert "kein Linux" not in quelltext


# --- 2. Windows-Session-Zählung ---------------------------------------------


def test_windows_sessions_aus_zaehlt_claude_und_agy_ohne_einmal_und_doppel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "300").write_text("", encoding="utf-8")  # kein .json — egal
    (sessions_dir / "301.json").write_text(json.dumps({"pid": 301, "status": "idle"}), encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_SPEICHER_SESSIONS", str(sessions_dir))

    prozesse = {
        10: (4, r"C:\Windows\System32\cmd.exe", 1),
        100: (10, r'"C:\Users\u\.local\bin\claude.exe" --resume abc', 2),
        # npm-Hülle cmd.exe -> node.exe: zählt als genau eine Session
        200: (10, r'C:\Windows\System32\cmd.exe /c "C:\Users\u\AppData\Roaming\npm\claude.cmd"', 3),
        201: (200, r'C:\Program Files\nodejs\node.exe "C:\Users\u\node_modules\@anthropic-ai\claude-code\cli.js"', 4),
        # Stille Session (status: idle)
        301: (10, r"C:\Users\u\.local\bin\claude.exe", 5),
        # agy-Session interaktiv
        400: (10, r"C:\Users\u\.local\bin\agy.exe -i auftrag", 6),
        # Einmal-Helfer (-p / --print) zählen nie als eigene Session
        500: (10, r"C:\Users\u\.local\bin\claude.exe -p kurzlauf", 7),
        501: (10, r"C:\Users\u\.local\bin\agy.exe --print kurzlauf", 8),
        # Normale Prozesse und Launcher zählen nicht
        600: (10, r"C:\Python312\python.exe skripte\bau.py 284", 9),
    }

    assert speicher._windows_sessions_aus(prozesse) == 3  # 100, 200/201, 400
    assert speicher._windows_sessions_aus(prozesse, mit_stillen=True) == 4  # plus 301
    assert speicher._windows_sessions_aus({}) is None


# --- 3. Künstlich knapper Speicher: warten und automatisch nachholen --------


def test_windows_knapper_speicher_wartet_und_holt_nach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _windows_ohne_testtuer(monkeypatch, tmp_path)
    messwerte = iter([(512, 32768), (1024, 32768), (8192, 32768)])
    monkeypatch.setattr(speicher, "windows_speicher_mib", lambda: next(messwerte))
    monkeypatch.setattr(speicher, "windows_sessions", lambda *, mit_stillen=False: 1)

    pausen: list[float] = []
    zyklen = speicher.auf_platz_warten(_KONFIG, wer="bau 284", schlafen=pausen.append, takt_s=30)

    assert zyklen == 2
    assert pausen == [30, 30]
    err = capsys.readouterr().err
    assert speicher.WARTE_TEXT in err
    assert "Speicher knapp" in err
    assert "Speicher wieder frei nach 2 Wartezyklen" in err
    assert "kein Linux" not in err
    plaetze = json.loads((tmp_path / "platz.json").read_text(encoding="utf-8"))
    assert len(plaetze) == 1


# --- 4. Echter Windows-Lauf auf dem GitHub-Windows-Läufer -------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Echter Windows-Beleg auf dem Windows-Läufer (#284)")
def test_windows_echter_speicher_und_automatisches_nachholen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Echter Windows-Nachweis ohne RAM-Attrappe: GlobalMemoryStatusEx + auf_platz_warten."""
    monkeypatch.delenv("TO_SPAWN_SPEICHER_MEMINFO", raising=False)
    monkeypatch.delenv("TO_SPAWN_SPEICHER_PROC", raising=False)
    monkeypatch.setenv("TO_SPAWN_SPEICHER_SESSIONS", str(tmp_path / "sessions"))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PLATZ", str(tmp_path / "platz.json"))

    frei_mib, gesamt_mib = speicher.system_speicher_mib()
    assert frei_mib is not None and frei_mib > 0
    assert gesamt_mib is not None and gesamt_mib >= frei_mib

    ok, urteil = speicher.platz_frei({"speicher": {"min_frei_mib": 1, "max_sessions": 64, "staffel_s": 20}})
    assert ok is True
    assert "Speicher frei" in urteil
    assert "GB" in urteil
    assert "kein Linux" not in urteil
    assert "kein Urteil" not in urteil

    # Künstlich knapper Speicher über frische Repo-Konfig: zuerst Schwelle über Gesamt-RAM,
    # nach einem Wartezyklus wieder Luft → Start wird automatisch nachgeholt.
    repo = tmp_path / "repo"
    konfig_datei = repo / config.KONFIG_PFAD
    konfig_datei.parent.mkdir(parents=True)
    konfig_datei.write_text(
        json.dumps({"speicher": {"min_frei_mib": gesamt_mib + 1024, "max_sessions": 64, "staffel_s": 20}}),
        encoding="utf-8",
    )
    konfig = config.lade(repo)

    pausen: list[float] = []

    def nach_pause_luft(sekunden: float) -> None:
        pausen.append(sekunden)
        konfig_datei.write_text(
            json.dumps({"speicher": {"min_frei_mib": 1, "max_sessions": 64, "staffel_s": 20}}),
            encoding="utf-8",
        )

    zyklen = speicher.auf_platz_warten(konfig, wer="bau 284", schlafen=nach_pause_luft, takt_s=15)

    assert zyklen == 1
    assert pausen == [15]
    err = capsys.readouterr().err
    assert speicher.WARTE_TEXT in err
    assert "Speicher knapp" in err
    assert "Speicher wieder frei nach 1 Wartezyklen" in err

    # CLI-Probe unter echtem Windows ohne Test-Türen
    env = {k: v for k, v in os.environ.items() if k not in ("TO_SPAWN_SPEICHER_MEMINFO", "TO_SPAWN_SPEICHER_PROC")}
    env["TO_SPAWN_SPEICHER_SESSIONS"] = str(tmp_path / "sessions")
    env["TO_SPAWN_SPEICHER_PLATZ"] = str(tmp_path / "platz-cli.json")
    ergebnis = subprocess.run(
        [sys.executable, str(CLI), "speicher", "--trocken"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert ergebnis.returncode in (0, speicher.EXIT_VOLL), ergebnis.stdout + ergebnis.stderr
    assert "kein Linux" not in ergebnis.stdout
    assert "kein Urteil" not in ergebnis.stdout
