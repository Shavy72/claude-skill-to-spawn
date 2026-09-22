"""Fixrunde 1 zu #254 — Befunde des Prüfpanels.

F1 Pause-Karussell: ein Reset, der längst vorbei ist, darf keine Pause auslösen;
   nach einer Handvoll Pausen in Folge steht der Wächter wie vor #254.
F2 Weg-Test über mehrere Limits hintereinander (das gestellte ``claude`` schreibt
   auch beim ``--resume`` eine Limit-Zeile).
F4 Bau-Log-Zeilen weichen auf die unversionierte Laufdatei aus, statt zu verschwinden.
F5 Stillstand hinterlässt eine Spur im Bau-Log, nicht nur eine Mail.
F6 Jede Pause bekommt einen eigenen Mail-Schlüssel (sonst entprellt der Melder).
F7 Mehrdeutige Uhrzeiten: Zeitumstellung und fehlendes am/pm.
F8 Kaputte Umgebungswerte werfen den Wächter nicht aus der Bahn.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    _ausfuehrbar,
    _mails,
    _text,
    _wache,
    _wache_welt,
    welt,
)

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import bau_log, waechter_lauf  # noqa: E402

BERLIN = ZoneInfo("Europe/Berlin")
AUSWEICH = "claude-opus-5"
ECHT = Path(__file__).resolve().parent / "hilfen" / "limit_zeile_echt.jsonl"


# --- F1: Reset längst vorbei -------------------------------------------------


def test_reset_knapp_vorbei_ergibt_noch_eine_pause() -> None:
    jetzt = datetime(2026, 9, 18, 19, 40, 30, tzinfo=BERLIN)
    ziel = datetime(2026, 9, 18, 19, 40, tzinfo=BERLIN)
    assert waechter_lauf.warte_sekunden(ziel, jetzt=jetzt, puffer=30.0) == 30.0


def test_reset_lange_vorbei_ist_unbrauchbar() -> None:
    """Sonst startet der Wächter im Takt des Puffers immer neu (Karussell)."""
    jetzt = datetime(2026, 9, 18, 20, 0, tzinfo=BERLIN)
    ziel = datetime(2026, 9, 18, 19, 40, tzinfo=BERLIN)
    assert waechter_lauf.warte_sekunden(ziel, jetzt=jetzt) is None


def test_obergrenze_der_pausen_ist_klein() -> None:
    assert 1 <= waechter_lauf.MAX_PAUSEN <= 5


# --- F7: mehrdeutige Uhrzeiten ----------------------------------------------


def _ohne_quota(text: str) -> dict[str, Any]:
    zeile: dict[str, Any] = json.loads(ECHT.read_bytes())
    zeile.pop("quotaLimits", None)
    zeile["message"]["content"] = [{"type": "text", "text": text}]
    return zeile


def test_uhrzeit_ohne_am_pm_unter_13_uhr_ist_mehrdeutig() -> None:
    """„resets 7“ kann 7 Uhr oder 19 Uhr heißen — dann lieber melden als raten."""
    zeile = _ohne_quota("You've hit your session limit · resets 7 (Europe/Berlin)")
    jetzt = datetime(2026, 9, 18, 1, 0, tzinfo=BERLIN)
    assert waechter_lauf.reset_zeitpunkt(zeile, jetzt=jetzt) is None


def test_uhrzeit_ohne_am_pm_ab_13_uhr_ist_eindeutig() -> None:
    zeile = _ohne_quota("You've hit your session limit · resets 19:40 (Europe/Berlin)")
    jetzt = datetime(2026, 9, 18, 18, 0, tzinfo=BERLIN)
    ziel = waechter_lauf.reset_zeitpunkt(zeile, jetzt=jetzt)
    assert ziel is not None
    assert ziel.astimezone(BERLIN) == datetime(2026, 9, 18, 19, 40, tzinfo=BERLIN)


def test_zeitumstellung_nimmt_die_spaetere_stunde() -> None:
    """25.10.2026 gibt es 02:30 zweimal — die zweite ist die, nach der das Limit wirklich offen ist."""
    zeile = _ohne_quota("You've hit your session limit · resets 2:30am (Europe/Berlin)")
    jetzt = datetime(2026, 10, 25, 1, 30, tzinfo=BERLIN)
    ziel = waechter_lauf.reset_zeitpunkt(zeile, jetzt=jetzt)
    assert ziel is not None
    assert ziel.utcoffset().total_seconds() == 3600, "Winterzeit (+01:00) erwartet"


# --- F8: Umgebungswerte ------------------------------------------------------


def test_zahl_aus_umgebung_nimmt_bei_muell_die_vorgabe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TO_SPAWN_RESET_PUFFER_S", "2m")
    assert waechter_lauf.zahl_aus_umgebung("TO_SPAWN_RESET_PUFFER_S", 120.0) == 120.0


def test_zahl_aus_umgebung_klemmt_negatives_auf_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TO_SPAWN_RESET_PUFFER_S", "-30")
    assert waechter_lauf.zahl_aus_umgebung("TO_SPAWN_RESET_PUFFER_S", 120.0) == 0.0


def test_wache_startet_trotz_kaputtem_umgebungswert(welt: dict[str, Path]) -> None:
    env = {**_wache_welt(welt), "TO_SPAWN_RESET_PUFFER_S": "2m", "FAKE_SCHLAF": "1"}
    ergebnis = _wache(welt["repo"], "--dry-run", env=env)
    assert ergebnis.returncode == 0, _text(ergebnis)


# --- F4/F5: Bau-Log-Spuren ---------------------------------------------------


def test_log_zeile_weicht_auf_die_laufdatei_aus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zeigt ``TO_SPAWN_LOG_REPO`` ins Leere, darf die Zeile nicht verschwinden."""
    repo = tmp_path / "repo"
    (repo / ".to-spawn").mkdir(parents=True)
    monkeypatch.setenv("TO_SPAWN_LOG_REPO", str(tmp_path / "weg"))
    monkeypatch.setenv("TO_SPAWN_LOG_RUECKFALL", str(repo))
    waechter_lauf._log_zeile(repo, 900, "waechter_pause", modell="m", bis="19:40")
    zeilen = bau_log.lese(repo, 900)
    assert [z["typ"] for z in zeilen] == ["waechter_pause"]


# --- F2/F5/F6: mehrere Limits hintereinander ---------------------------------

FAKE_CLAUDE_IMMER_LIMIT = r"""#!/usr/bin/env python3
import json, os, re, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FAKE_PROTOKOLL"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\n")
if "--session-id" in args:
    sid = args[args.index("--session-id") + 1]
else:
    sid = args[args.index("--resume") + 1]
ordner = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
ordner.mkdir(parents=True, exist_ok=True)
datei = ordner / f"{sid}.jsonl"
zeile = json.loads(os.environ["FAKE_LIMIT"])
zeile["quotaLimits"]["resetsAt"] = int(time.time() + float(os.environ["FAKE_RESET_IN"]))
with datei.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"type": "user", "message": {"content": "los"}}) + "\n")
    fh.flush()
    time.sleep(0.3)
    fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")
time.sleep(float(os.environ.get("FAKE_SCHLAF", "30")))
"""


def test_wache_steht_nach_mehreren_pausen_still(welt: dict[str, Path]) -> None:
    """Limit direkt nach jedem Neustart: nach MAX_PAUSEN ist Schluss, sonst läuft es ewig."""
    env = _wache_welt(welt)
    _ausfuehrbar(welt["tmp"] / "claude_bin" / "claude", FAKE_CLAUDE_IMMER_LIMIT)
    env |= {
        "FAKE_LIMIT": ECHT.read_text(encoding="utf-8").strip(),
        "FAKE_RESET_IN": "1",
        "FAKE_SCHLAF": "20",
        "TO_SPAWN_RESET_PUFFER_S": "0",
    }
    beginn = time.monotonic()
    ergebnis = _wache(welt["repo"], "--model", AUSWEICH, env=env)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert time.monotonic() - beginn < 50, "Wächter hängt in der Pausen-Schleife"

    aufrufe = [
        json.loads(z)
        for z in (welt["tmp"] / "claude_aufrufe.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if z
    ]
    assert len(aufrufe) == waechter_lauf.MAX_PAUSEN + 1, aufrufe

    arten = [m["art"] for m in _mails(welt)]
    assert arten.count("waechter_pause") == waechter_lauf.MAX_PAUSEN, arten
    assert arten[-1] == "session_tot", arten

    typen = [z["typ"] for z in bau_log.lese(welt["repo"], "900")]
    assert typen.count("waechter_pause") == waechter_lauf.MAX_PAUSEN
    assert typen.count("waechter_weiter") == waechter_lauf.MAX_PAUSEN
    assert "blockiert" in typen, "Stillstand muss eine Spur im Bau-Log hinterlassen"


def test_wache_ohne_uhrzeit_schreibt_blockiert_ins_bau_log(
    welt: dict[str, Path],
) -> None:
    zeile: dict[str, Any] = json.loads(ECHT.read_bytes())
    zeile.pop("quotaLimits", None)
    zeile["message"]["content"] = [
        {"type": "text", "text": "You've hit your session limit"}
    ]
    env = {
        **_wache_welt(welt),
        "FAKE_LIMIT": json.dumps(zeile, ensure_ascii=False),
        "FAKE_SCHLAF": "3",
    }
    ergebnis = _wache(welt["repo"], "--model", AUSWEICH, env=env)
    assert ergebnis.returncode == 0, _text(ergebnis)
    typen = [z["typ"] for z in bau_log.lese(welt["repo"], "900")]
    assert "blockiert" in typen, typen
    assert [m["art"] for m in _mails(welt)] == ["session_tot"]


def test_os_ist_da() -> None:
    assert os.name in ("posix", "nt")
