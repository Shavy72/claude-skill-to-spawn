"""Weg-Test #1449: Limit-Pause endet, sobald das Limit wieder offen ist (Probe statt Reset-Zeit).

Vorfall 20.11.: Aufseher meldete „Reset erst 22.11.“, schickte ``session_tot`` und stand —
das Limit war 40 min später wieder offen. Echt laufen ``skripte/wache.py``, die Aufsicht,
das Bau-Log und der Melder. Gestellt ist nur das Programm ``claude``: es schreibt eine echte
Limit-Zeile ins Transkript und beantwortet die Probe (``claude -p``) erst „zu“, dann „ok“.
"""

# ruff: noqa: F811  (welt = Fixture aus test_waechter_213)
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    LIMIT_ZEILE,
    _ausfuehrbar,
    _mails,
    _text,
    _wache,
    _wache_welt,
    welt,
)

from to_spawn import bau_log, waechter_lauf

AUSWEICH = "claude-sonnet-5"
ECHT = Path(__file__).resolve().parent / "hilfen" / "limit_zeile_echt.jsonl"

FAKE_CLAUDE_PROBE = r"""#!/usr/bin/env python3
import json, os, re, sys, time
from pathlib import Path
args = sys.argv[1:]
protokoll = Path(os.environ["FAKE_PROTOKOLL"])
with protokoll.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\n")
if "-p" in args:
    zaehler = protokoll.parent / "probe_zaehler.txt"
    n = int(zaehler.read_text()) + 1 if zaehler.exists() else 1
    zaehler.write_text(str(n))
    if n > int(os.environ.get("FAKE_PROBE_ZU", "0")):
        print("ok")
        sys.exit(0)
    print("You've hit your weekly limit · resets Nov 22, 9am (Europe/Berlin)")
    sys.exit(1)
if "--resume" in args:
    if os.environ.get("FAKE_RESUME_LIMIT") != "1":
        sys.exit(0)
    sid = args[args.index("--resume") + 1]
else:
    sid = args[args.index("--session-id") + 1]
ordner = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
ordner.mkdir(parents=True, exist_ok=True)
with (ordner / f"{sid}.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"type": "user", "message": {"content": "los"}}) + "\n")
    fh.flush()
    time.sleep(0.3)
    fh.write(os.environ["FAKE_LIMIT"] + "\n")
time.sleep(float(os.environ.get("FAKE_SCHLAF", "30")))
"""


def _limit_in_stunden(stunden: float) -> str:
    zeile: dict[str, Any] = json.loads(ECHT.read_bytes())
    zeile["quotaLimits"]["resetsAt"] = int(time.time() + stunden * 3600)
    return json.dumps(zeile, ensure_ascii=False)


def _limit_ohne_uhrzeit() -> str:
    zeile: dict[str, Any] = json.loads(ECHT.read_bytes())
    zeile.pop("quotaLimits", None)
    zeile["message"]["content"] = [{"type": "text", "text": "You've hit your weekly limit"}]
    return json.dumps(zeile, ensure_ascii=False)


def _env(welt: dict[str, Path], limit: str, **mehr: str) -> dict[str, str]:
    env = _wache_welt(welt)
    _ausfuehrbar(welt["tmp"] / "claude_bin" / "claude", FAKE_CLAUDE_PROBE)
    return {
        **env,
        "FAKE_LIMIT": limit,
        "FAKE_SCHLAF": "30",
        "TO_SPAWN_LIMIT_PROBE_S": "0.3",
        **mehr,
    }


def _aufrufe(welt: dict[str, Path]) -> tuple[list[list[str]], list[list[str]]]:
    """(Aufseher-Starts, Proben) aus dem Protokoll der Attrappe."""
    datei = welt["tmp"] / "claude_aufrufe.jsonl"
    alle = [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z]
    return [a for a in alle if "-p" not in a], [a for a in alle if "-p" in a]


def _typen(welt: dict[str, Path]) -> list[str]:
    return [z["typ"] for z in bau_log.lese(welt["repo"], "900")]


def _pruefe_weiter_nach_probe(welt: dict[str, Path], env: dict[str, str]) -> None:
    beginn = time.monotonic()
    ergebnis = _wache(welt["repo"], "--model", AUSWEICH, env=env)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert time.monotonic() - beginn < 40, "Wächter hängt in der Probe-Pause"

    starts, proben = _aufrufe(welt)
    assert len(starts) == 2, starts  # Start + Fortsetzung ohne Handgriff
    sid = starts[0][starts[0].index("--session-id") + 1]
    assert starts[1][starts[1].index("--resume") + 1] == sid
    assert len(proben) == 2, proben  # erste Probe „zu“, zweite „ok“
    assert all(p[p.index("--model") + 1] == AUSWEICH for p in proben), proben
    arten = [m["art"] for m in _mails(welt)]
    assert "session_tot" not in arten, arten
    assert _typen(welt).count("waechter_weiter") == 1, _typen(welt)


def test_reset_in_40_h_aber_limit_frueher_offen_faehrt_weiter(welt: dict[str, Path]) -> None:
    """Abnahme #1449: Reset „in 40 h“, Limit geht früher auf → nächste Probe startet den Aufseher."""
    _pruefe_weiter_nach_probe(welt, _env(welt, _limit_in_stunden(40), FAKE_PROBE_ZU="1"))


def test_reset_unbekannt_probe_offen_faehrt_weiter(welt: dict[str, Path]) -> None:
    _pruefe_weiter_nach_probe(welt, _env(welt, _limit_ohne_uhrzeit(), FAKE_PROBE_ZU="1"))


def test_wochen_limit_greift_wirklich_keine_dauerschleife(welt: dict[str, Path]) -> None:
    """Probe bleibt „zu“, Limit greift nach jedem Neustart: nach MAX_PAUSEN steht er (F1)."""
    env = _env(
        welt,
        _limit_in_stunden(40),
        FAKE_PROBE_ZU="1000",
        FAKE_RESUME_LIMIT="1",
        TO_SPAWN_LIMIT_PROBE_MAX_S="1",
        FAKE_SCHLAF="20",
    )
    beginn = time.monotonic()
    ergebnis = _wache(welt["repo"], "--model", AUSWEICH, env=env)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert time.monotonic() - beginn < 50, "Wächter hängt in der Pausen-Schleife"

    starts, proben = _aufrufe(welt)
    assert len(starts) == waechter_lauf.MAX_PAUSEN + 1, starts
    # Probe im Takt (0,3 s) je Pause (1 s) — keine Proben-Flut
    assert 1 <= len(proben) <= waechter_lauf.MAX_PAUSEN * 4, proben
    arten = [m["art"] for m in _mails(welt)]
    assert arten.count("waechter_pause") == waechter_lauf.MAX_PAUSEN, arten
    assert arten[-1] == "session_tot", arten


def test_kurzer_reset_wartet_blind_ohne_probe(welt: dict[str, Path]) -> None:
    """Reset in wenigen Sekunden (unter MAX_WARTE_S): Pause wie bisher, keine Probe."""
    env = _env(welt, _limit_in_stunden(2 / 3600), TO_SPAWN_RESET_PUFFER_S="1")
    ergebnis = _wache(welt["repo"], "--model", AUSWEICH, env=env)
    assert ergebnis.returncode == 0, _text(ergebnis)
    starts, proben = _aufrufe(welt)
    assert len(starts) == 2 and not proben, (starts, proben)


def test_probe_ohne_api_key_und_limit_text_heisst_zu(tmp_path: Path, monkeypatch: Any) -> None:
    """Rechenlogik der Probe: Limit-Text → zu, „ok“ → offen; der API-Key fliegt raus."""
    binaer = tmp_path / "bin"
    binaer.mkdir()
    attrappe = _ausfuehrbar(
        binaer / "claude",
        "#!/usr/bin/env python3\nimport os, sys\n"
        "print(\"You've hit your limit (API-Key)\" if os.environ.get('ANTHROPIC_API_KEY') else os.environ['ANTWORT'])\n",
    )
    monkeypatch.setenv("TO_SPAWN_TEST_BIN", str(binaer))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "geheim")
    monkeypatch.setenv("ANTWORT", "ok")
    assert waechter_lauf.limit_offen(str(attrappe), AUSWEICH, tmp_path) is True
    monkeypatch.setenv("ANTWORT", "You've hit your weekly limit")
    assert waechter_lauf.limit_offen(str(attrappe), AUSWEICH, tmp_path) is False
