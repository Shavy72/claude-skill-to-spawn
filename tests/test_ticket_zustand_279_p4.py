"""#279 P4: Hausmeister, Befund, Spec-Rand und Manifest fragen ``ticket_zustand``.

(1) Befund: ein als Duplikat geschlossenes Ticket ist „verworfen“, nicht „gebaut“ —
    in REST-Form (``state_reason``) und CLI-Form (``stateReason``).
(2) Manifest: ein fertig gebautes Ticket (offen + Staging-Label) wird wie ein zu
    behandeltes geprüft (Frage ``fertig_gebaut``) — auch ein Blocker-Verweis darauf.
(3) Hausmeister und Spec-Rand: Staging-Label allein ist kein Schluss (``endgueltig_zu``).

Attrappen nur für die GitHub-Abfrage (externer Dienst), die Regeln laufen echt.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import befund, hausmeister, manifest, spec_rand, ticket_zustand  # noqa: E402

STAGING = [{"name": ticket_zustand.STAGING_FERTIG_LABEL}]
ALT_ZU = "2020-01-01T00:00:00Z"


# (1) Befund ---------------------------------------------------------------


@pytest.mark.parametrize(
    "daten",
    [
        {"state": "closed", "state_reason": "duplicate"},
        {"state": "CLOSED", "stateReason": "DUPLICATE"},
    ],
)
def test_befund_duplikat_ist_verworfen(daten: dict[str, Any]) -> None:
    assert befund._status(daten) == "verworfen"


def test_befund_erledigt_ist_gebaut_und_staging_bleibt_offen() -> None:
    assert befund._status({"state": "CLOSED", "stateReason": "COMPLETED"}) == "gebaut"
    assert befund._status({"state": "open", "labels": STAGING}) == "offen"


# (2) Manifest -------------------------------------------------------------


def test_manifest_checkpoint_ohne_offene_andere_wenn_rest_fertig_gebaut() -> None:
    bericht = manifest.Bericht(spec="900", tickets=["901", "902"])
    github: dict[str, dict[str, Any] | None] = {
        "901": {"state": "OPEN", "labels": STAGING, "_kanten": []},
        "902": {"state": "OPEN", "labels": [{"name": "checkpoint"}], "_kanten": []},
    }
    manifest._pruefe_checkpoint(bericht, github, ["902"])
    assert bericht.fehler == []


def test_manifest_blocker_verweis_auf_fertig_gebautes_ist_nicht_offen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ticket_zustand, "lesen", lambda nr, cwd=None: {"state": "OPEN", "labels": STAGING})
    bericht = manifest.Bericht(spec="900", tickets=["901"])
    daten = {"state": "OPEN", "labels": [], "body": "## Blocked by\n#777\n", "_slug": "o/r"}
    offen = manifest._offen_frage(Path("."), {})
    manifest._pruefe_verweise(bericht, "901", daten, [], {"901"}, offen)
    assert bericht.fehler == []
    assert any("#777" in w for w in bericht.warnungen)


def test_manifest_blocker_verweis_unlesbar_bleibt_fehler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ticket_zustand, "lesen", lambda nr, cwd=None: None)
    assert manifest._offen_frage(Path("."), {})("777") is None


# (3) Hausmeister + Spec-Rand ----------------------------------------------


def test_hausmeister_staging_fertig_ist_kein_schluss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hausmeister, "_ticket_daten", lambda nr, slug=None: {"state": "OPEN", "labels": STAGING})
    with pytest.raises(hausmeister._Bleibt, match="offen"):
        hausmeister._ticket_lange_zu(5, None)


def test_hausmeister_zu_mit_staging_label_darf_weg(monkeypatch: pytest.MonkeyPatch) -> None:
    daten = {"state": "CLOSED", "closedAt": ALT_ZU, "labels": STAGING}
    monkeypatch.setattr(hausmeister, "_ticket_daten", lambda nr, slug=None: daten)
    hausmeister._ticket_lange_zu(5, None)


def test_spec_rand_staging_fertig_bleibt_offen() -> None:
    assert spec_rand._offen({"state": "open", "labels": STAGING}) is True
    assert spec_rand._offen({"state": "closed", "state_reason": "completed"}) is False
