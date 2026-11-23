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
from types import SimpleNamespace
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


# (4) Checkpoint-Frage = echter Schluss (Prüfer-Befund PR 304) ------------


def _checkpoint_lauf(checkpoint: dict[str, Any]) -> manifest.Bericht:
    bericht = manifest.Bericht(spec="900", tickets=["901", "902"])
    github: dict[str, dict[str, Any] | None] = {
        "901": {"state": "OPEN", "labels": [], "_kanten": [{"number": 902}]},
        "902": {**checkpoint, "_kanten": [{"number": 901}]},
    }
    manifest._pruefe_checkpoint(bericht, github, ["902"])
    return bericht


def test_manifest_checkpoint_mit_staging_label_ist_nicht_schon_zu() -> None:
    bericht = _checkpoint_lauf({"state": "OPEN", "labels": STAGING + [{"name": "checkpoint"}]})
    assert not any("schon zu" in f for f in bericht.fehler)


def test_manifest_checkpoint_geschlossen_bleibt_fehler() -> None:
    bericht = _checkpoint_lauf({"state": "CLOSED", "stateReason": "COMPLETED", "labels": []})
    assert any("schon zu" in f for f in bericht.fehler)


def test_manifest_und_spec_rand_einig_fuer_checkpoint_mit_staging_label() -> None:
    checkpoint = {"state": "OPEN", "labels": STAGING + [{"name": "checkpoint"}]}
    manifest_sagt_offen = not any("schon zu" in f for f in _checkpoint_lauf(checkpoint).fehler)
    assert manifest_sagt_offen is spec_rand._offen(checkpoint) is True


# (5) Abdeckung Hausmeister: zweig_fertig + _schliesszeit -------------------


def _zweig_attrappe(monkeypatch: pytest.MonkeyPatch, daten: dict[str, Any]) -> None:
    ort = SimpleNamespace(remote="https://github.com/o/r", cwd=Path("."), haupt="origin/HEAD")
    monkeypatch.setattr(hausmeister, "_zweig_ort", lambda repo, o: ort)
    monkeypatch.setattr(hausmeister, "_zweig_stand", lambda o, z: "a" * 40)
    monkeypatch.setattr(hausmeister, "_zweig_fehlende", lambda o, s: [])
    monkeypatch.setattr(hausmeister, "_slug", lambda r: "o/r")
    monkeypatch.setattr(hausmeister, "_ticket_daten", lambda nr, slug=None: daten)


def test_hausmeister_zweig_mit_staging_label_ist_nicht_fertig(monkeypatch: pytest.MonkeyPatch) -> None:
    _zweig_attrappe(monkeypatch, {"state": "OPEN", "labels": STAGING})
    fertig, _, grund, _ = hausmeister.zweig_fertig(".", "ticket/5-x", holen=False)
    assert fertig is False and "offen" in grund


def test_hausmeister_zweig_mit_geschlossenem_ticket_ist_fertig(monkeypatch: pytest.MonkeyPatch) -> None:
    _zweig_attrappe(monkeypatch, {"state": "CLOSED", "labels": STAGING})
    assert hausmeister.zweig_fertig(".", "ticket/5-x", holen=False)[0] is True


@pytest.mark.parametrize(
    ("daten", "hat_zeit"),
    [
        ({"state": "OPEN", "labels": STAGING, "closedAt": None}, False),
        ({"state": "CLOSED", "labels": STAGING, "closedAt": ALT_ZU}, True),
    ],
)
def test_hausmeister_schliesszeit_nur_bei_echtem_schluss(
    monkeypatch: pytest.MonkeyPatch, daten: dict[str, Any], hat_zeit: bool
) -> None:
    monkeypatch.setattr(hausmeister, "_ticket_nummer", lambda ordner, echt: 5)
    monkeypatch.setattr(hausmeister, "_origin_slug", lambda echt: "o/r")
    monkeypatch.setattr(hausmeister, "_ticket_daten", lambda nr, slug=None: daten)
    assert (hausmeister._schliesszeit(Path(".")) is not None) is hat_zeit


# (6) Unbekannter/leerer state gilt als offen -------------------------------


@pytest.mark.parametrize("daten", [{}, {"state": ""}, {"state": "merged"}])
def test_unbekannter_state_gilt_als_offen(daten: dict[str, Any]) -> None:
    assert spec_rand._offen(daten) is True
    assert manifest._fertig_gebaut(daten) is False
