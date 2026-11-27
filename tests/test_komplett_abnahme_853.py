"""#853: Komplett-Prüfung (Moment.ALLE_AUF_STAGING) startet trotz offenem Abnahme-Ticket.

Abnahme-Tickets mit ``checkpoint:human`` (bzw. ``regularien.checkpoint_label``) warten auf den
Menschen und werden erst nach der Prüfung geschlossen. Für ``Moment.ALLE_AUF_STAGING`` zählen
sie deshalb nicht zu den Bau-Tickets — sonst liefe die Komplett-Prüfung vor der Abnahme nie.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus test_waechter_213 und wird als Parameter genannt.
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from test_komplett_je_spec_425 import _befehl, _laeufe
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _capo,
    _commit,
    _gh_setzen,
    _gh_zustand,
    _iso,
    _konfig,
    _text,
    welt,
)

from to_spawn import capo, komplett_lauf, ticket_zustand
from to_spawn.pruefplan import Moment

STAGING = {"url": "http://127.0.0.1:8001/"}


def _eingerichtet(welt: dict[str, Path], zaehl: Path, **zusatz: object) -> None:
    _konfig(
        welt,
        pruefung={"schnell": "true", "komplett": _befehl(zaehl)},
        staging=STAGING,
        **zusatz,
    )


def _abnahme_hinzufuegen(welt: dict[str, Path], nummer: int = 903, label: str = "checkpoint:human") -> None:
    daten = _gh_zustand(welt)
    kinder: list[int] = list(daten["sub"][SPEC])
    if nummer not in kinder:
        kinder.append(nummer)
    daten["sub"][SPEC] = kinder
    daten["issues"][str(nummer)] = {
        "state": "open",
        "updated_at": _iso(),
        "labels": [label],
    }
    welt["gh"].write_text(json.dumps(daten), encoding="utf-8")


def _bau_tickets_zu(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: B (#902)", {})
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())


def _zaehl(welt: dict[str, Path]) -> Path:
    return welt["tmp"] / "zaehl.txt"


def test_takt_alle_auf_staging_laeuft_trotz_offenem_abnahme_ticket(welt: dict[str, Path]) -> None:
    """Weg-Test #853: fertige Bau-Tickets + offenes Abnahme-Ticket lösen ALLE_AUF_STAGING aus."""
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl)
    _abnahme_hinzufuegen(welt)
    _bau_tickets_zu(welt)

    erster = _capo(welt)
    assert "Komplett-Prüfung grün" in erster.stdout, _text(erster)
    assert "SPEC FERTIG" not in erster.stdout, _text(erster)
    assert _laeufe(zaehl) == 1, _text(erster)
    merker = komplett_lauf.lies_merker(welt["repo"], int(SPEC))
    assert merker is not None and merker.gruen and merker.moment == Moment.ALLE_AUF_STAGING.value

    # Zweiter Takt mit weiter offenem Abnahme-Ticket startet keinen zweiten Lauf.
    zweiter = _capo(welt)
    assert _laeufe(zaehl) == 1, _text(zweiter)
    assert "SPEC FERTIG" not in zweiter.stdout, _text(zweiter)

    # Nach Abschluss des Abnahme-Tickets wird SPEC FERTIG ohne erneuten Komplett-Lauf gemeldet.
    _gh_setzen(
        welt,
        "903",
        state="closed",
        closed_at=_iso(timedelta(minutes=20)),
        labels=["checkpoint:human", capo.OK_LABEL],
    )
    schluss = _capo(welt)
    assert "SPEC FERTIG" in schluss.stdout, _text(schluss)
    assert _laeufe(zaehl) == 1, _text(schluss)


def test_takt_offenes_bau_ticket_und_abnahme_kein_lauf(welt: dict[str, Path]) -> None:
    """Solange noch ein Bau-Ticket offen ist, startet ALLE_AUF_STAGING nicht."""
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl)
    _abnahme_hinzufuegen(welt)

    ergebnis = _capo(welt)
    assert _laeufe(zaehl) == 0, _text(ergebnis)
    assert "Komplett-Prüfung" not in ergebnis.stdout, _text(ergebnis)
    assert komplett_lauf.lies_merker(welt["repo"], int(SPEC)) is None


def test_takt_nur_abnahme_ticket_kein_lauf(welt: dict[str, Path]) -> None:
    """Spec mit ausschließlich einem offenen Abnahme-Ticket startet keine Komplett-Prüfung."""
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl)
    daten = _gh_zustand(welt)
    daten["sub"][SPEC] = [903]
    daten["issues"] = {
        "903": {
            "state": "open",
            "updated_at": _iso(),
            "labels": ["checkpoint:human"],
        }
    }
    welt["gh"].write_text(json.dumps(daten), encoding="utf-8")

    ergebnis = _capo(welt)
    assert _laeufe(zaehl) == 0, _text(ergebnis)
    assert "Komplett-Prüfung" not in ergebnis.stdout, _text(ergebnis)
    assert komplett_lauf.lies_merker(welt["repo"], int(SPEC)) is None


def test_takt_eigenes_checkpoint_label_wird_beachtet(welt: dict[str, Path]) -> None:
    """Konfiguriertes ``regularien.checkpoint_label`` zählt ebenfalls nicht als Bau-Ticket."""
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl, regularien={"checkpoint_label": "abnahme:mensch"})
    _abnahme_hinzufuegen(welt, label="abnahme:mensch")
    _bau_tickets_zu(welt)

    ergebnis = _capo(welt)
    assert "Komplett-Prüfung grün" in ergebnis.stdout, _text(ergebnis)
    assert "SPEC FERTIG" not in ergebnis.stdout, _text(ergebnis)
    assert _laeufe(zaehl) == 1, _text(ergebnis)
    merker = komplett_lauf.lies_merker(welt["repo"], int(SPEC))
    assert merker is not None and merker.gruen and merker.moment == Moment.ALLE_AUF_STAGING.value


def test_takt_bau_ticket_staging_fertig_und_offene_abnahme(welt: dict[str, Path]) -> None:
    """Bau-Ticket mit ``staging:fertig`` + offenes Abnahme-Ticket lösen ALLE_AUF_STAGING aus."""
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl)
    _abnahme_hinzufuegen(welt)
    _commit(welt["repo"], "feat: B (#902)", {})
    _gh_setzen(welt, "902", state="open", labels=[ticket_zustand.STAGING_FERTIG_LABEL])

    ergebnis = _capo(welt)
    assert "Komplett-Prüfung grün" in ergebnis.stdout, _text(ergebnis)
    assert "SPEC FERTIG" not in ergebnis.stdout, _text(ergebnis)
    assert _laeufe(zaehl) == 1, _text(ergebnis)
    merker = komplett_lauf.lies_merker(welt["repo"], int(SPEC))
    assert merker is not None and merker.gruen and merker.moment == Moment.ALLE_AUF_STAGING.value
