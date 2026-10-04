"""Tests für duoplus-management#451 — capo wertet nur echte Session-Fragen am Checkpoint.

Befund (Spec #399, 03.10.): Davids Akzeptanz-Hinweis am noch blockierten Checkpoint-
Ticket #441 zählte als „Frage“; nach 60 min kam die Mail „Checkpoint ohne Vorschlag“,
obwohl nie eine Session für das Ticket lief.

Echt laufen wie in #285: die CLI ``skripte/capo.py`` gegen ein Git-Repo mit
Bare-Origin. Gestellt sind nur GitHub (``gh``-Ersatz aus #213, jetzt mit Blocker-
Abfrage) und der Mail-Befehl (schreibt in eine Datei).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from test_capo_checkpoint_285 import (
    CHECKPOINT,
    FRAGE,
    VORSCHLAG,
    _checkpoint_stellen,
    _kommentar,
    _labels,
    _texte,
)
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    _capo,
    _gh_zustand,
    _mails,
    _text,
    welt,
)

from to_spawn import capo

DAVID = "Akzeptanz: Grün erst, wenn alle Handys im Takt laufen."


def _blocker(welt: dict[str, Path], nummer: str, *eintraege: tuple[int, str]) -> None:
    """Native Blocker-Kanten ``(Nummer, Zustand)`` für ein Ticket in den GitHub-Zustand legen."""
    daten = _gh_zustand(welt)
    daten.setdefault("blocker", {})[nummer] = [{"number": n, "state": s} for n, s in eintraege]
    welt["gh"].write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")


def _checkpoint_zeilen(ausgabe: str) -> list[str]:
    return [z for z in ausgabe.splitlines() if "#902" in z and "Checkpoint" in z]


# --- a) Davids Kommentar ohne Session-Spur ist keine Frage ---------------------


def test_david_kommentar_ohne_session_spur_keine_zeile_keine_mail(welt: dict[str, Path]) -> None:
    _checkpoint_stellen(welt)
    _kommentar(welt, "902", "Shavy72", DAVID, 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _checkpoint_zeilen(ergebnis.stdout) == [], _text(ergebnis)
    assert "checkpoint_offen" not in [m.get("art") for m in _mails(welt)], _mails(welt)
    assert [t for t in _texte(welt, "902") if t.startswith("Wächter:")] == []
    assert _labels(welt, "902") == [CHECKPOINT]


def test_david_kommentar_vor_der_frist_meldet_kein_warten(welt: dict[str, Path]) -> None:
    """Der Befund-Beleg: „#441 Checkpoint wartet (44 von 60 min)“ ohne jede Session."""
    _checkpoint_stellen(welt)
    _kommentar(welt, "902", "Shavy72", DAVID, 44)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _checkpoint_zeilen(ergebnis.stdout) == [], _text(ergebnis)


def test_frage_der_session_zaehlt_weiter(welt: dict[str, Path]) -> None:
    """Gegenprobe: eine markierte Session-Frage ohne Vorschlag wird nach der Frist gemeldet."""
    _checkpoint_stellen(welt)
    _kommentar(welt, "902", "Shavy72", DAVID, 120)
    _kommentar(welt, "902", "Shavy72", FRAGE, 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "kein Vorschlag" in ergebnis.stdout, _text(ergebnis)
    assert "checkpoint_offen" in [m.get("art") for m in _mails(welt)], _mails(welt)


# --- b) offene Blocker: Checkpoint ruht ---------------------------------------


def test_offener_blocker_ueberspringt_checkpoint(welt: dict[str, Path]) -> None:
    _checkpoint_stellen(welt)
    _blocker(welt, "902", (428, "closed"), (429, "open"))
    _kommentar(welt, "902", "bau-bot", f"{FRAGE}\n\n{VORSCHLAG}", 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _checkpoint_zeilen(ergebnis.stdout) == [], _text(ergebnis)
    assert [t for t in _texte(welt, "902") if t.startswith("Wächter: Annahme")] == []
    assert _mails(welt) == []


def test_alle_blocker_zu_checkpoint_laeuft(welt: dict[str, Path]) -> None:
    _checkpoint_stellen(welt)
    _blocker(welt, "902", (428, "closed"), (429, "CLOSED"))
    _kommentar(welt, "902", "bau-bot", f"{FRAGE}\n\n{VORSCHLAG}", 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "#902 Checkpoint-Annahme" in ergebnis.stdout, _text(ergebnis)


def test_blocker_unlesbar_meldet_fehler_ohne_annahme(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Sichere Richtung: ohne Blocker-Stand weder Annahme noch Mail, aber sichtbarer FEHLER."""
    _checkpoint_stellen(welt)
    _kommentar(welt, "902", "bau-bot", f"{FRAGE}\n\n{VORSCHLAG}", 61)
    monkeypatch.setenv("GH_STUB_FEHLER", "blocked_by")
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 1, _text(ergebnis)  # FEHLER-Zeilen machen capo rot
    assert "#902 FEHLER: Blocker nicht lesbar" in ergebnis.stdout, _text(ergebnis)
    assert [t for t in _texte(welt, "902") if t.startswith("Wächter: Annahme")] == []
    assert _mails(welt) == []


# --- c) Einheit: checkpoint_frage_zeit -------------------------------------------


def _roh(body: str, vor_min: float) -> dict:
    zeit = datetime.now(timezone.utc) - timedelta(minutes=vor_min)
    return {"user": {"login": "Shavy72"}, "body": body, "created_at": zeit.strftime("%Y-%m-%dT%H:%M:%SZ")}


def test_frage_zeit_ignoriert_kommentare_ohne_session_marke() -> None:
    kommentare = [_roh(DAVID, 30), _roh("Wächter: Annahme nach 61 min", 20)]
    assert capo.checkpoint_frage_zeit([], kommentare) is None


def test_frage_zeit_nimmt_session_kommentar_und_bau_log() -> None:
    session = _roh(FRAGE, 50)
    zeile = {"typ": "blockiert", "ts": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}
    nur_kommentar = capo.checkpoint_frage_zeit([], [_roh(DAVID, 5), session])
    assert nur_kommentar is not None
    assert abs((datetime.now(timezone.utc) - nur_kommentar).total_seconds() / 60 - 50) < 1
    mit_log = capo.checkpoint_frage_zeit([zeile], [session])
    assert mit_log is not None
    assert abs((datetime.now(timezone.utc) - mit_log).total_seconds() / 60 - 10) < 1


@pytest.mark.parametrize("eintrag", [{"number": 429}, "429"])
def test_blocker_format_unbekannt_meldet_fehler(welt: dict[str, Path], eintrag: object) -> None:
    """Fehlt ``state`` oder ist der Eintrag kein Objekt, ruht der Checkpoint nicht still."""
    _checkpoint_stellen(welt)
    daten = _gh_zustand(welt)
    daten.setdefault("blocker", {})["902"] = [eintrag]
    welt["gh"].write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    _kommentar(welt, "902", "bau-bot", f"{FRAGE}\n\n{VORSCHLAG}", 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 1, _text(ergebnis)
    assert "#902 FEHLER: Blocker-Format unbekannt" in ergebnis.stdout, _text(ergebnis)
    assert [t for t in _texte(welt, "902") if t.startswith("Wächter: Annahme")] == []
    assert _mails(welt) == []
