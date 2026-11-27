"""Ticket #1461 (Folge #1444): Fehler-Zweig von ``halt.pruefen`` läuft in die 24-h-Frist;
``anlegen`` löscht einen liegengebliebenen Merker.

Echte Halt-Datei, echter Merker; die Attrappe ist nur der erzwungene Programmfehler.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import halt


@pytest.fixture
def klon(tmp_path: Path) -> Path:
    repo = tmp_path / "klon"
    (repo / ".to-spawn").mkdir(parents=True)
    return repo


def _bis_spaeter() -> halt.Bedingung:
    return halt.Bedingung("bis", (datetime.now(UTC) + timedelta(hours=48)).isoformat())


def _vor_frist() -> str:
    return (datetime.now(UTC) - halt.ALT_FRIST - timedelta(minutes=1)).isoformat()


def _merker_altern(klon: Path) -> None:
    merker = klon / halt.MERKER_DATEI
    stand = json.loads(merker.read_text(encoding="utf-8"))
    stand["seit"] = _vor_frist()
    merker.write_text(json.dumps(stand), encoding="utf-8")


def _kaputt(*_a: object, **_k: object) -> None:
    raise RuntimeError("unerwartet")


@pytest.mark.parametrize("stelle", ["_erfuellt", "_lies"])
def test_dauerhafter_fehler_hebt_nach_frist_ab_erster_beobachtung_auf(
    klon: Path, monkeypatch: pytest.MonkeyPatch, stelle: str
) -> None:
    halt.anlegen(klon, "Nachtruhe", _bis_spaeter())
    monkeypatch.setattr(halt, stelle, _kaputt)
    assert halt.pruefen(klon) is not None
    assert (klon / halt.DATEI).exists()
    assert (klon / halt.MERKER_DATEI).exists(), "Fehler-Zweig muss die erste Beobachtung merken"
    assert halt.pruefen(klon) is not None  # zweiter Takt vor Ablauf: hält weiter
    _merker_altern(klon)
    assert halt.pruefen(klon, aufheben=False) is None  # Trockenlauf: würde aufheben …
    assert (klon / halt.DATEI).exists()  # … ändert aber nichts
    assert halt.pruefen(klon) is None
    assert not (klon / halt.DATEI).exists()


def test_alter_merker_greift_nicht_bei_neuem_halt(klon: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (klon / halt.MERKER_DATEI).write_text(
        json.dumps({"halt_seit": "unbekannt", "seit": _vor_frist()}), encoding="utf-8"
    )
    halt.anlegen(klon, "Nachtruhe", _bis_spaeter())
    assert not (klon / halt.MERKER_DATEI).exists()
    monkeypatch.setattr(halt, "_lies", _kaputt)  # Schlüssel „unbekannt“ wie der Rest-Merker
    assert halt.pruefen(klon) is not None
    assert (klon / halt.DATEI).exists()
    _merker_altern(klon)  # eigene Frist abgelaufen ⇒ fällt
    assert halt.pruefen(klon) is None
    assert not (klon / halt.DATEI).exists()


def test_lese_fehler_einmal_dann_sauber_haelt_ueber_frist_hinaus(klon: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    halt.anlegen(klon, "Nachtruhe", _bis_spaeter())
    echt = halt._lies
    monkeypatch.setattr(halt, "_lies", _kaputt)
    assert halt.pruefen(klon) is not None  # Schluckauf, Merker gesetzt
    monkeypatch.setattr(halt, "_lies", echt)
    assert halt.pruefen(klon) is not None  # sauber ⇒ Merker weg
    assert not (klon / halt.MERKER_DATEI).exists()
    jetzt = datetime.now(UTC)
    monkeypatch.setattr(halt, "_jetzt", lambda: jetzt + halt.ALT_FRIST + timedelta(hours=1))
    assert halt.pruefen(klon) is not None  # Fehler-Merker hebt den gesunden Halt nicht auf
    assert (klon / halt.DATEI).exists()


def test_fristablauf_benennt_datei_um_statt_zu_loeschen(klon: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    halt.anlegen(klon, "Nachtruhe", _bis_spaeter())
    original = (klon / halt.DATEI).read_text(encoding="utf-8")
    monkeypatch.setattr(halt, "_lies", _kaputt)
    assert halt.pruefen(klon) is not None
    _merker_altern(klon)
    assert halt.pruefen(klon) is None
    assert not (klon / halt.DATEI).exists()
    gesichert = list((klon / halt.DATEI).parent.glob(f"{Path(halt.DATEI).name}.aufgehoben-*"))
    assert len(gesichert) == 1
    assert gesichert[0].read_text(encoding="utf-8") == original


def test_warnung_nicht_pruefbar_nur_einmal_je_halt(
    klon: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    halt.anlegen(klon, "Nachtruhe", _bis_spaeter())
    monkeypatch.setattr(halt, "_erfuellt", lambda *_a, **_k: None)
    with caplog.at_level(logging.WARNING, logger="to_spawn.halt"):
        for _ in range(3):
            assert halt.pruefen(klon) is not None
    warnungen = [r for r in caplog.records if "nicht prüfbar" in r.getMessage()]
    assert len(warnungen) == 1


@pytest.mark.parametrize("rohbytes", [b"\xff\xfe kaputt", b"{kein json"])
def test_unlesbare_datei_warnt_einmal_und_sichert_mit_rohinhalt_im_log(
    klon: Path, caplog: pytest.LogCaptureFixture, rohbytes: bytes
) -> None:
    """Unlesbare/kaputte Halt-Datei: Warnung einmal je Halt, nach Frist umbenannt + Rohinhalt im Log."""
    datei = klon / halt.DATEI
    datei.write_bytes(rohbytes)
    with caplog.at_level(logging.WARNING, logger="to_spawn.halt"):
        for _ in range(3):
            assert halt.pruefen(klon) is not None
    lese_warnungen = [r for r in caplog.records if "nicht lesbar" in r.getMessage()]
    pruef_warnungen = [r for r in caplog.records if "nicht prüfbar" in r.getMessage()]
    assert len(lese_warnungen) == 1
    assert len(pruef_warnungen) == 1

    _merker_altern(klon)
    assert halt.pruefen(klon) is None
    assert not datei.exists()
    gesichert = list(datei.parent.glob(f"{datei.name}.aufgehoben-*"))
    assert len(gesichert) == 1
    assert gesichert[0].read_bytes() == rohbytes

    eintraege = [
        json.loads(zeile) for zeile in (klon / halt.LOG_DATEI).read_text(encoding="utf-8").splitlines() if zeile
    ]
    aufgehoben = [e for e in eintraege if e["ereignis"] == "aufgehoben"]
    assert len(aufgehoben) == 1
    assert aufgehoben[0]["gesichert_als"] == gesichert[0].name
    assert aufgehoben[0]["roh"] == rohbytes.decode("utf-8", errors="replace")[:500]
