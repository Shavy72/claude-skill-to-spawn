"""Limit-Erkennung gegen eine ECHTE Transkript-Zeile (duoplus-management#213).

``hilfen/limit_zeile_echt.jsonl`` ist eine unveränderte Zeile aus einem echten
Claude-Code-Transkript (Session-Limit am 18.09.2026, Claude Code 2.1.276). Die
Gegenbeispiele werden aus genau dieser Zeile abgeleitet (gleiche Datei-Form),
nur die entscheidenden Felder ändern sich.
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    _aufrufe,
    _text,
    _wache,
    _wache_welt,
    welt,
)

from to_spawn import waechter_lauf

ECHT = Path(__file__).resolve().parent / "hilfen" / "limit_zeile_echt.jsonl"


def _echte_roh() -> bytes:
    roh = ECHT.read_bytes()
    assert roh.count(b"\n") == 1 and roh.endswith(b"\n"), "genau eine Zeile erwartet"
    return roh


def _echte() -> dict[str, Any]:
    return json.loads(_echte_roh())


def _normale_assistant_zeile() -> dict[str, Any]:
    """Gleiche Datei-Form, aber eine gewöhnliche Modell-Antwort."""
    zeile = copy.deepcopy(_echte())
    for feld in ("error", "isApiErrorMessage", "apiErrorStatus", "quotaLimits"):
        zeile.pop(feld, None)
    zeile["message"]["model"] = "claude-opus-5"
    zeile["message"]["content"] = [
        {"type": "text", "text": "Tests grün, ich committe jetzt."}
    ]
    return zeile


def _drossel_429_ohne_limit_text() -> dict[str, Any]:
    """Gleiche Datei-Form, 429 + ``rate_limit``, aber kurze API-Drosselung."""
    zeile = copy.deepcopy(_echte())
    zeile["message"]["content"] = [
        {"type": "text", "text": "API Error: 429 Rate limit reached for requests"}
    ]
    return zeile


def test_echte_zeile_ist_wirklich_die_limit_meldung() -> None:
    zeile = _echte()
    assert zeile["type"] == "assistant"
    assert zeile["isApiErrorMessage"] is True
    assert zeile["apiErrorStatus"] == 429
    assert zeile["message"]["model"] == "<synthetic>"


def test_echte_limit_zeile_wird_erkannt() -> None:
    assert waechter_lauf.ist_limit_zeile(_echte()) is True


def test_normale_assistant_zeile_ist_kein_limit() -> None:
    assert waechter_lauf.ist_limit_zeile(_normale_assistant_zeile()) is False


def test_429_ohne_limit_text_ist_kein_limit() -> None:
    zeile = _drossel_429_ohne_limit_text()
    assert zeile["apiErrorStatus"] == 429 and zeile["error"] == "rate_limit"
    assert waechter_lauf.ist_limit_zeile(zeile) is False


def test_aufsicht_erkennt_nachtraeglich_angehaengte_echte_zeile(
    tmp_path: Path,
) -> None:
    """Transkript läuft schon (normale Zeilen), dann hängt Claude Code die echte
    Limit-Zeile an — die Aufsicht muss den Wechsel auslösen, und zwar genau einmal."""
    datei = tmp_path / "transkript.jsonl"
    vorher = (
        json.dumps(_normale_assistant_zeile())
        + "\n"
        + json.dumps(_drossel_429_ohne_limit_text())
        + "\n"
    )
    datei.write_text(vorher, encoding="utf-8")
    funde: list[str] = []
    gemeldet = threading.Event()

    def bei_limit(text: str) -> None:
        funde.append(text)
        gemeldet.set()

    aufsicht = waechter_lauf.Aufsicht(datei, 0, 0.02, bei_limit)
    aufsicht.start()
    try:
        assert not gemeldet.wait(0.3), f"Fehlalarm vor der Limit-Zeile: {funde}"
        with datei.open("ab") as strom:
            strom.write(_echte_roh())
        assert gemeldet.wait(5), "echte Limit-Zeile nicht erkannt"
    finally:
        aufsicht.halt.set()
        aufsicht.join(timeout=5)
    assert len(funde) == 1
    assert "session limit" in funde[0]


def test_wache_wechselt_bei_echter_limit_zeile(welt: dict[str, Path]) -> None:
    """Ganzer Weg: gestelltes ``claude`` schreibt die echte Zeile unverändert ins
    Transkript — ``wache.py`` beendet die Session und setzt mit ``--resume`` auf dem
    Ausweich-Modell fort."""
    umgebung = _wache_welt(welt)
    umgebung["FAKE_LIMIT"] = _echte_roh().decode("utf-8").rstrip("\n")
    ergebnis = _wache(welt["repo"], env=umgebung)
    assert ergebnis.returncode == 0, _text(ergebnis)
    aufrufe = _aufrufe(welt)
    assert len(aufrufe) == 2, aufrufe
    erster, zweiter = aufrufe
    sid = erster[erster.index("--session-id") + 1]
    assert zweiter[zweiter.index("--resume") + 1] == sid
    assert "Nutzungs-Limit erkannt" in ergebnis.stderr
