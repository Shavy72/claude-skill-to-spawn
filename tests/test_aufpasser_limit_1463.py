"""Aufpasser #894 (Folge #1463): beschädigte ``stand.json``-Einträge beim Laden
verwerfen und genau einmal loggen.

Hinweis: Limit-Probe je Fenster-Modell aus dem Transkript sowie das Ignorieren alter
Limit-Zeilen über neuer fertiger Ausgabe sind bereits über #892 auf ``main`` und in
``tests/test_aufpasser_limit_1453.py`` abgedeckt.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser

JETZT = 1_800_000_000.0


def _aufpasser_mit_stand(tmp: Path, inhalt: object) -> aufpasser.Aufpasser:
    zustand = tmp / "zustand"
    zustand.mkdir(parents=True, exist_ok=True)
    (zustand / "stand.json").write_text(json.dumps(inhalt), encoding="utf-8")
    return aufpasser.Aufpasser(
        aufpasser.Einstellungen(
            zustand=zustand,
            tmux_socket="nie-benutzt",
            jetzt=lambda: JETZT,
            heim=tmp / "heim",
        )
    )


def test_stand_laden_verwirft_beschaedigte_eintraege_und_loggt_einmal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Kaputte Einträge in stand.json werden verworfen, gültige bleiben, genau 1 Warning."""
    kaputt = {
        "fenster": {
            "spec-1/bau 1": {"hash": "abc", "seit": JETZT - 60, "stufe": 0},
            "spec-2/bau 2": "kein-dict",
            "spec-3/bau 3": None,
        },
        "meldungen": {
            "ok": JETZT - 10,
            "kaputt_str": "keine-zahl",
            "kaputt_bool": True,
        },
        "starts": {
            "ok": [JETZT - 100, "kaputt"],
            "kein_liste": "abc",
        },
        "limit_proben": {
            "claude|ok": {"zeit": JETZT - 60, "offen": False},
            "claude|kaputt_typ": "kein-dict",
            "claude|kaputt_zeit": {"zeit": "keine-zahl", "offen": True},
        },
        "platz_zuletzt": "keine-zahl",
    }
    with caplog.at_level(logging.WARNING, logger="aufpasser"):
        a = _aufpasser_mit_stand(tmp_path, kaputt)

    assert list(a.stand.fenster.keys()) == ["spec-1/bau 1"]
    assert a.stand.meldungen == {"ok": JETZT - 10}
    assert a.stand.starts == {"ok": [JETZT - 100]}
    assert list(a.stand.limit_proben.keys()) == ["claude|ok"]
    assert a.stand.platz_zuletzt == 0.0
    warnungen = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnungen) == 1


def test_stand_laden_kein_dict_liefert_leeren_stand_und_loggt_einmal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Ist das Wurzelobjekt in stand.json kein Dict, startet der Aufpasser leer mit 1 Warning."""
    with caplog.at_level(logging.WARNING, logger="aufpasser"):
        a = _aufpasser_mit_stand(tmp_path, ["kein", "dict"])

    assert a.stand == aufpasser.Stand()
    warnungen = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnungen) == 1


def test_stand_laden_kaputte_limit_probe_sperrt_nicht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Ein beschädigter limit_proben-Eintrag verhindert weder Laden noch die nächste Probe."""
    kaputt = {
        "fenster": {},
        "meldungen": {},
        "starts": {},
        "limit_proben": {"claude|claude-opus-5-5": {"zeit": "kaputt", "offen": False}},
    }
    with caplog.at_level(logging.WARNING, logger="aufpasser"):
        a = _aufpasser_mit_stand(tmp_path, kaputt)

    assert a.stand.limit_proben == {}
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert aufpasser._probe_gueltig({"zeit": "kaputt", "offen": False}, JETZT) is False
