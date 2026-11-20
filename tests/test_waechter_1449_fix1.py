"""Fix-Runde 1 #1449: Pausen-Zähler je Arbeitsphase, Probe-Pausen-Mail, Abbruch nach der Probe,
Fehlerzweige der Probe.

Weg-Test wie in ``test_waechter_1449_limit_probe``: echt laufen ``skripte/wache.py``, Aufsicht,
Bau-Log und Melder; gestellt ist nur das Programm ``claude``.
"""

# ruff: noqa: F811  (welt = Fixture aus test_waechter_213)
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    _ausfuehrbar,
    _mails,
    _text,
    _wache,
    _wache_welt,
    welt,
)
from test_waechter_1449_limit_probe import AUSWEICH, _aufrufe, _limit_in_stunden, _typen

from to_spawn import waechter_lauf

#: Jeder Lauf (Start und ``--resume``) arbeitet erst (Assistent-Antwort), dann greift das
#: Limit — bis ``FAKE_LIMIT_LAEUFE`` Läufe vorbei sind; danach endet die Session sauber.
#: Die Probe meldet sofort „ok“.
FAKE_CLAUDE_ARBEIT = r"""#!/usr/bin/env python3
import json, os, re, sys, time
from pathlib import Path
args = sys.argv[1:]
protokoll = Path(os.environ["FAKE_PROTOKOLL"])
with protokoll.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\n")
if "-p" in args:
    print("ok")
    sys.exit(0)
zaehler = protokoll.parent / "lauf_zaehler.txt"
n = int(zaehler.read_text()) + 1 if zaehler.exists() else 1
zaehler.write_text(str(n))
if n > int(os.environ["FAKE_LIMIT_LAEUFE"]):
    sys.exit(0)
sid = args[args.index("--resume") + 1] if "--resume" in args else args[args.index("--session-id") + 1]
ordner = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
ordner.mkdir(parents=True, exist_ok=True)
arbeit = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Tick erledigt"}]}}
with (ordner / f"{sid}.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"type": "user", "message": {"content": "los"}}) + "\n")
    fh.write(json.dumps(arbeit) + "\n")
    fh.flush()
    time.sleep(0.3)
    fh.write(os.environ["FAKE_LIMIT"] + "\n")
time.sleep(30)
"""


def test_vier_pausen_mit_arbeit_dazwischen_session_lebt(welt: dict[str, Path]) -> None:
    """Befund 1: MAX_PAUSEN zählt Pausen in Folge, nicht je Session — Arbeit setzt zurück."""
    laeufe = waechter_lauf.MAX_PAUSEN + 1
    env = {
        **_wache_welt(welt),
        "FAKE_LIMIT": _limit_in_stunden(40),
        "FAKE_LIMIT_LAEUFE": str(laeufe),
        "TO_SPAWN_LIMIT_PROBE_S": "0.3",
    }
    _ausfuehrbar(welt["tmp"] / "claude_bin" / "claude", FAKE_CLAUDE_ARBEIT)
    ergebnis = _wache(welt["repo"], "--model", AUSWEICH, env=env, timeout=90)
    assert ergebnis.returncode == 0, _text(ergebnis)
    starts, _proben = _aufrufe(welt)
    assert len(starts) == laeufe + 1, starts  # jede Pause endete mit Fortsetzung
    arten = [m["art"] for m in _mails(welt)]
    assert "session_tot" not in arten, arten
    assert _typen(welt).count("waechter_pause") == laeufe, _typen(welt)


def test_probe_pause_mail_nennt_probe_statt_fernes_datum(welt: dict[str, Path]) -> None:
    """Befund 2: Probe-Pause endet meist nach Minuten — die Mail sagt das, nicht „bis in 8 Tagen“."""
    env = {
        **_wache_welt(welt),
        "FAKE_LIMIT": _limit_in_stunden(40),
        "FAKE_LIMIT_LAEUFE": "1",
        "TO_SPAWN_LIMIT_PROBE_S": "0.3",
    }
    _ausfuehrbar(welt["tmp"] / "claude_bin" / "claude", FAKE_CLAUDE_ARBEIT)
    ergebnis = _wache(welt["repo"], "--model", AUSWEICH, env=env)
    assert ergebnis.returncode == 0, _text(ergebnis)
    pausen = [m for m in _mails(welt) if m["art"] == "waechter_pause"]
    assert len(pausen) == 1, _mails(welt)
    text = json.dumps(pausen[0], ensure_ascii=False)
    assert "pausiert bis" not in text, text
    assert "sobald das Limit offen ist" in text, text
    assert "spätestens" in text, text


def test_abbruch_waehrend_der_probe_gewinnt(monkeypatch: Any) -> None:
    """Befund 3: greift der Abbruch (Umzug/Ablösung) während der Probe, endet die Pause mit False."""
    zustand = {"geprobt": False}

    def offen() -> bool:
        zustand["geprobt"] = True
        return True

    assert (
        waechter_lauf._warten(5.0, 0.05, lambda: zustand["geprobt"], offen, 0.05)
        is False
    )


def test_probe_programm_fehlt_gilt_als_zu(tmp_path: Path, monkeypatch: Any) -> None:
    """Befund 4: Programm ``claude`` fehlt → „zu“, kein Absturz des Aufsehers."""
    binaer = tmp_path / "bin"
    binaer.mkdir()
    monkeypatch.setenv("TO_SPAWN_TEST_BIN", str(binaer))
    assert (
        waechter_lauf.limit_offen(str(binaer / "claude"), AUSWEICH, tmp_path) is False
    )


def test_probe_zeitablauf_gilt_als_zu(tmp_path: Path, monkeypatch: Any) -> None:
    """Befund 4: Probe hängt länger als PROBE_TIMEOUT_S → „zu“, kein Absturz des Aufsehers."""
    binaer = tmp_path / "bin"
    binaer.mkdir()
    attrappe = _ausfuehrbar(
        binaer / "claude",
        "#!/usr/bin/env python3\nimport time\ntime.sleep(10)\nprint('ok')\n",
    )
    monkeypatch.setenv("TO_SPAWN_TEST_BIN", str(binaer))
    monkeypatch.setattr(waechter_lauf, "PROBE_TIMEOUT_S", 0.5)
    beginn = time.monotonic()
    assert waechter_lauf.limit_offen(str(attrappe), AUSWEICH, tmp_path) is False
    assert time.monotonic() - beginn < 5
