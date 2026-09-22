"""Weg-Test: Wächter pausiert bis zum Limit-Reset und fährt selbst weiter (#254).

Echt laufen die CLI ``skripte/wache.py``, die Aufsicht, das Bau-Log und der
Melder. Gestellt ist nur das Programm ``claude`` (schreibt eine echte
Limit-Zeile ins Transkript) — wie in ``test_waechter_213.py``, dessen Welt
diese Datei mitbenutzt.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    LIMIT_ZEILE,
    _aufrufe,
    _mails,
    _text,
    _wache,
    _wache_welt,
    welt,
)

AUSWEICH = "claude-opus-5"
ECHT = Path(__file__).resolve().parent / "hilfen" / "limit_zeile_echt.jsonl"


def _limit_mit_reset(in_sekunden: float) -> str:
    """Echte Limit-Zeile, deren Reset gleich fällig ist (``quotaLimits.resetsAt``)."""
    zeile: dict[str, Any] = json.loads(ECHT.read_bytes())
    zeile["quotaLimits"]["resetsAt"] = int(time.time() + in_sekunden)
    return json.dumps(zeile, ensure_ascii=False)


def _limit_ohne_uhrzeit() -> str:
    zeile = copy.deepcopy(LIMIT_ZEILE)
    zeile["message"]["content"] = [
        {"type": "text", "text": "You've hit your session limit"}
    ]
    return json.dumps(zeile, ensure_ascii=False)


def _log_typen(welt: dict[str, Path]) -> list[str]:
    from to_spawn import bau_log

    return [z["typ"] for z in bau_log.lese(welt["repo"], "900")]


def test_wache_pausiert_bis_zum_reset_und_faehrt_selbst_weiter(
    welt: dict[str, Path],
) -> None:
    env = {
        **_wache_welt(welt),
        "FAKE_LIMIT": _limit_mit_reset(2),
        "FAKE_SCHLAF": "30",
        "TO_SPAWN_RESET_PUFFER_S": "1",
    }
    beginn = time.monotonic()
    ergebnis = _wache(welt["repo"], "--model", AUSWEICH, env=env)
    gebraucht = time.monotonic() - beginn
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert gebraucht < 40, "Wächter ist in der Pause hängen geblieben"
    assert gebraucht > 1, "Wächter hat die Pause gar nicht abgewartet"

    aufrufe = _aufrufe(welt)
    assert len(aufrufe) == 2, aufrufe
    erster, zweiter = aufrufe
    sid = erster[erster.index("--session-id") + 1]
    assert zweiter[zweiter.index("--resume") + 1] == sid
    assert zweiter[zweiter.index("--model") + 1] == AUSWEICH

    from to_spawn import bau_log

    zeilen = bau_log.lese(welt["repo"], "900")
    pause = [z for z in zeilen if z["typ"] == "waechter_pause"]
    weiter = [z for z in zeilen if z["typ"] == "waechter_weiter"]
    assert len(pause) == 1 and len(weiter) == 1, zeilen
    assert pause[0]["bis"] and float(pause[0]["sekunden"]) > 0
    assert weiter[0]["modell"] == AUSWEICH
    assert [m["art"] for m in _mails(welt)] == ["waechter_pause"]


def test_wache_ohne_uhrzeit_meldet_session_tot_und_wartet_nicht(
    welt: dict[str, Path],
) -> None:
    """Ohne erkennbare Reset-Zeit bleibt alles wie vor #254: Mail, kein Neustart."""
    env = {
        **_wache_welt(welt),
        "FAKE_LIMIT": _limit_ohne_uhrzeit(),
        "FAKE_SCHLAF": "3",
    }
    beginn = time.monotonic()
    ergebnis = _wache(welt["repo"], "--model", AUSWEICH, env=env)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert time.monotonic() - beginn < 40, "Wächter hat trotzdem gewartet"
    assert len(_aufrufe(welt)) == 1
    assert [m["art"] for m in _mails(welt)] == ["session_tot"]
    assert "waechter_pause" not in _log_typen(welt)


def test_wache_wartet_nicht_ueber_die_obergrenze(welt: dict[str, Path]) -> None:
    """Reset Tage weg (Wochen-Limit) → Mail wie heute statt Endlos-Pause."""
    env = {
        **_wache_welt(welt),
        "FAKE_LIMIT": _limit_mit_reset(3 * 24 * 3600),
        "FAKE_SCHLAF": "3",
    }
    beginn = time.monotonic()
    ergebnis = _wache(welt["repo"], "--model", AUSWEICH, env=env)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert time.monotonic() - beginn < 40, "Wächter hat trotzdem gewartet"
    assert len(_aufrufe(welt)) == 1
    assert [m["art"] for m in _mails(welt)] == ["session_tot"]
