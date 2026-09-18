"""Zweite Fixrunde #213: Karenz 0, Ausgangsstand ohne Uhr-Vergleich, Mail ohne Befehl.

Alle Tests laufen OHNE ``TO_SPAWN_WAECHTER_SOFORT`` (Modul nicht in
``conftest._SOFORT_MODULE``): die Zeitgrenzen kommen nur aus der Repo-Konfig.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _beleg,
    _capo,
    _commit,
    _gh_setzen,
    _iso,
    _kommentare,
    _konfig,
    _mails,
    _text,
    welt,
)


def _zustand_datei(welt: dict[str, Path]) -> Path:
    return welt["zustand"] / f"test_wegwerf_{SPEC}.json"


def _zustand(welt: dict[str, Path], nummer: str = "901") -> str:
    daten = json.loads(welt["gh"].read_text(encoding="utf-8"))
    return daten["issues"][nummer]["state"]


def _ohne_karenz(welt: dict[str, Path], **zusatz: object) -> None:
    _konfig(welt, waechter={"karenz_minuten": 0}, **zusatz)


# --- 1: karenz_minuten 0 = keine Karenz ------------------------------------------------


def test_karenz_null_prueft_sofort(welt: dict[str, Path]) -> None:
    _ohne_karenz(welt)
    _commit(welt["repo"], "feat: ohne Nummer", _beleg("901"))
    _gh_setzen(welt, "901", state="open", closed_at=None)
    erste = _capo(welt)  # Ausgangsstand: 901 ist noch offen
    assert erste.returncode == 0, _text(erste)
    _gh_setzen(welt, "901", state="closed", closed_at=_iso())
    zweite = _capo(welt)
    assert "prüfe später" not in zweite.stdout, _text(zweite)
    assert _zustand(welt) == "open"
    kommentar = _kommentare(welt, "901")
    assert len(kommentar) == 1 and "commit_ohne_nummer" in kommentar[0]
    assert "beweis_fehlt" not in kommentar[0]


def test_karenz_fehlt_bleibt_vorgabe(welt: dict[str, Path]) -> None:
    """Ohne ``karenz_minuten`` gilt weiter die Vorgabe (15 min)."""
    _commit(welt["repo"], "feat: ohne Nummer", _beleg("901"))
    _gh_setzen(welt, "901", state="open", closed_at=None)
    _capo(welt)
    _gh_setzen(welt, "901", state="closed", closed_at=_iso())
    zweite = _capo(welt)
    assert "prüfe später" in zweite.stdout, _text(zweite)
    assert _zustand(welt) == "closed"


# --- 1b: Ausgangsstand hängt nicht an der lokalen Uhr ---------------------------------


def test_nach_erstem_tick_geschlossen_trotz_uhr_vorlauf(welt: dict[str, Path]) -> None:
    """Lokale Uhr geht vor (hier 5 min): ``closed_at`` von GitHub liegt dann VOR
    ``erster_tick``, obwohl das Ticket erst nach dem ersten Tick zuging. Es darf
    trotzdem kein Ausgangsstand sein — das Ticket war beim ersten Tick offen."""
    _ohne_karenz(welt)
    _commit(welt["repo"], "feat: ohne Nummer", _beleg("901"))
    _gh_setzen(welt, "901", state="open", closed_at=None)
    assert _capo(welt).returncode == 0
    datei = _zustand_datei(welt)
    daten = json.loads(datei.read_text(encoding="utf-8"))
    vorlauf = datetime.now(timezone.utc) + timedelta(minutes=5)
    daten["erster_tick"] = vorlauf.isoformat(timespec="seconds")
    datei.write_text(json.dumps(daten), encoding="utf-8")
    _gh_setzen(welt, "901", state="closed", closed_at=_iso())
    zweite = _capo(welt)
    assert "Ausgangsstand" not in zweite.stdout, _text(zweite)
    assert _zustand(welt) == "open"


def test_beim_ersten_tick_zu_bleibt_ausgangsstand(welt: dict[str, Path]) -> None:
    _ohne_karenz(welt)
    _commit(welt["repo"], "feat: ohne Nummer", _beleg("901"))
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(timedelta(minutes=1)))
    erste = _capo(welt)
    zweite = _capo(welt)
    assert "Ausgangsstand" in erste.stdout and "Ausgangsstand" in zweite.stdout
    assert _zustand(welt) == "closed" and _kommentare(welt, "901") == []
    # Neues Schließ-Ereignis (anderes closed_at) wird wie üblich geprüft.
    _gh_setzen(welt, "901", closed_at=_iso())
    _capo(welt)
    assert _zustand(welt) == "open"


# --- 3: kein mail.befehl = bewusste Wahl, kein Fehler ---------------------------------


def _spec_fertig_ohne_mail(welt: dict[str, Path], mail: dict[str, object]) -> None:
    _konfig(welt, mail=mail)
    _commit(welt["repo"], "feat: A (#901)", _beleg("901"))
    _commit(welt["repo"], "feat: B (#902)", _beleg("902"))
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())


def test_ohne_mail_befehl_spec_fertig_exit_0(welt: dict[str, Path]) -> None:
    _spec_fertig_ohne_mail(welt, {"ziel": "", "nur_kritisch": True})
    for _ in range(2):
        ergebnis = _capo(welt)
        assert ergebnis.returncode == 0, _text(ergebnis)
        assert "SPEC FERTIG" in ergebnis.stdout
        assert "FEHLER" not in ergebnis.stdout
        assert ergebnis.stdout.count("Mail nicht eingerichtet (mail.befehl leer)") == 1
    assert _mails(welt) == []


def test_leerer_mail_befehl_ist_auch_nicht_eingerichtet(welt: dict[str, Path]) -> None:
    _spec_fertig_ohne_mail(welt, {"befehl": [], "nur_kritisch": True})
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "Mail nicht eingerichtet (mail.befehl leer)" in ergebnis.stdout


def test_konfigurierter_mail_befehl_scheitert_bleibt_fehler(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAIL_FAKE_EXIT", "1")
    _commit(welt["repo"], "feat: A (#901)", _beleg("901"))
    _commit(welt["repo"], "feat: B (#902)", _beleg("902"))
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 1, _text(ergebnis)
    assert "FEHLER: Mail spec_fertig nicht verschickt" in ergebnis.stdout
    assert "Mail nicht eingerichtet" not in ergebnis.stdout
