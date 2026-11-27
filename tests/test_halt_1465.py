"""Ticket #1465 (Folge #1461): im Zweifel hält der Halt.

- Werkzeug-Ausfall (gh/git nicht erreichbar) bei gültiger Bedingung hebt den Halt nie auf;
  nur Ticket/Commit, das es nachweislich nicht gibt (Tippfehler), fällt nach der Frist.
- Umbenennen/Löschen gescheitert ⇒ Halt gilt weiter, Warnung einmal je Halt.
- Programmfehler-Traceback einmal je Halt, nicht jede Runde.
- Sicherungs-Name eindeutig, auch in derselben Sekunde.

Echte Halt-Datei, echtes Git-Repo; GitHub läuft über die Test-Naht ``TO_SPAWN_GH_STUB``.
Attrappen nur für erzwungene Programm-/Dateifehler und die Uhr.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import halt

GH_STUB = SKILL / "tests" / "hilfen" / "gh_stub.py"


@pytest.fixture
def klon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "klon"
    (repo / ".to-spawn").mkdir(parents=True)
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps({"hauptzweig": "master", "wt_basis": str(tmp_path / "wts")}),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(GH_STUB))
    for name in ("GH_STUB_ZU", "GH_STUB_KAPUTT", "GH_STUB_FEHLT", "GH_STUB_LEER"):
        monkeypatch.delenv(name, raising=False)
    return repo


def _bis_spaeter() -> halt.Bedingung:
    return halt.Bedingung("bis", (datetime.now(UTC) + timedelta(hours=48)).isoformat())


def _merker_altern(klon: Path) -> None:
    merker = klon / halt.MERKER_DATEI
    stand = json.loads(merker.read_text(encoding="utf-8"))
    stand["seit"] = (datetime.now(UTC) - halt.ALT_FRIST - timedelta(minutes=1)).isoformat()
    merker.write_text(json.dumps(stand), encoding="utf-8")


def _sicherungen(klon: Path) -> list[Path]:
    return sorted((klon / halt.DATEI).parent.glob(f"{Path(halt.DATEI).name}.aufgehoben-*"))


def _log(klon: Path) -> list[dict]:
    datei = klon / halt.LOG_DATEI
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z]


def _kaputt(*_a: object, **_k: object) -> None:
    raise RuntimeError("unerwartet")


def test_gh_ausfall_haelt_gueltigen_halt_ueber_frist(klon: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Befund 1: gh > 24 h nicht erreichbar ⇒ Halt bleibt (nicht prüfbar ≠ Grund weg)."""
    halt.anlegen(klon, "Vorrang", halt.Bedingung("ticket_zu", "1346"))
    monkeypatch.setenv("GH_STUB_KAPUTT", "1346")
    assert halt.pruefen(klon) is not None
    _merker_altern(klon)
    assert halt.pruefen(klon) is not None
    assert (klon / halt.DATEI).exists()
    assert _sicherungen(klon) == []
    # Gegenprobe: gh wieder da, Ticket zu ⇒ Datei gelöscht, keine Sicherung, kein ``gesichert_als``.
    monkeypatch.delenv("GH_STUB_KAPUTT")
    monkeypatch.setenv("GH_STUB_ZU", "1346")
    assert halt.pruefen(klon) is None
    assert not (klon / halt.DATEI).exists()
    assert not (klon / halt.MERKER_DATEI).exists()
    assert _sicherungen(klon) == []
    letzte = _log(klon)[-1]
    assert letzte["ereignis"] == "aufgehoben" and "gesichert_als" not in letzte and "roh" not in letzte


def test_git_ausfall_haelt_commit_halt_ueber_frist(klon: Path) -> None:
    """Befund 1 für ``commit``: Klon ohne origin ⇒ git fetch scheitert ⇒ Halt bleibt."""
    halt.anlegen(klon, "Fix", halt.Bedingung("commit", "abcdef1234567"))
    assert halt.pruefen(klon) is not None
    _merker_altern(klon)
    assert halt.pruefen(klon) is not None
    assert (klon / halt.DATEI).exists()


def test_tippfehler_ticket_faellt_weiter_nach_frist(klon: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gegenprobe: Ticket gibt es nachweislich nicht (an ``anlegen`` vorbei) ⇒ Frist wie bisher."""
    (klon / halt.DATEI).write_text(
        json.dumps(
            {
                "grund": "Tippfehler",
                "seit": datetime.now(UTC).isoformat(),
                "wer": "t",
                "bedingung": {"art": "ticket_zu", "wert": "999999"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GH_STUB_FEHLT", "999999")
    assert halt.pruefen(klon) is not None
    _merker_altern(klon)
    assert halt.pruefen(klon) is None
    assert len(_sicherungen(klon)) == 1


def test_umbenennen_scheitert_halt_bleibt_warnung_einmal(
    klon: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Befund 2: Umbenennen scheitert ⇒ Halt gilt weiter, eine Warnung je Halt, kein Log ``aufgehoben``."""
    halt.anlegen(klon, "Nachtruhe", _bis_spaeter())
    monkeypatch.setattr(halt, "_erfuellt", _kaputt)
    assert halt.pruefen(klon) is not None
    _merker_altern(klon)
    echt = Path.rename

    def _verweigert(self: Path, ziel: Path) -> Path:
        raise PermissionError(13, "Keine Rechte", str(self))

    monkeypatch.setattr(Path, "rename", _verweigert)
    with caplog.at_level(logging.WARNING, logger="to_spawn.halt"):
        for _ in range(3):
            assert halt.pruefen(klon) is not None
    assert (klon / halt.DATEI).exists()
    assert [z["ereignis"] for z in _log(klon)] == ["angelegt"]
    warnungen = [r for r in caplog.records if "nicht entfernbar" in r.getMessage()]
    assert len(warnungen) == 1
    monkeypatch.setattr(Path, "rename", echt)
    assert halt.pruefen(klon) is None
    assert not (klon / halt.DATEI).exists()


def test_sicherung_in_derselben_sekunde_ueberschreibt_nicht(klon: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Befund 3: zwei Fristabläufe in derselben Sekunde ⇒ zwei Sicherungen mit Rohinhalt im Log."""
    fest = datetime.now(UTC).astimezone().replace(microsecond=0)
    zaehler = iter(range(1000))
    monkeypatch.setattr(halt, "_jetzt", lambda: fest + timedelta(microseconds=next(zaehler)))
    echt = halt._erfuellt
    inhalte = []
    for grund in ("erster", "zweiter"):
        halt.anlegen(klon, grund, _bis_spaeter())
        inhalte.append((klon / halt.DATEI).read_text(encoding="utf-8"))
        monkeypatch.setattr(halt, "_erfuellt", _kaputt)
        assert halt.pruefen(klon) is not None
        _merker_altern(klon)
        assert halt.pruefen(klon) is None
        monkeypatch.setattr(halt, "_erfuellt", echt)
    gesichert = _sicherungen(klon)
    assert sorted(p.read_text(encoding="utf-8") for p in gesichert) == sorted(inhalte)
    aufgehoben = [z for z in _log(klon) if z["ereignis"] == "aufgehoben"]
    assert sorted(z["gesichert_als"] for z in aufgehoben) == sorted(p.name for p in gesichert)
    assert sorted(z["roh"] for z in aufgehoben) == sorted(inhalte)


def test_traceback_nur_einmal_je_halt(
    klon: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Review (a): dauerhafter Programmfehler ⇒ Traceback einmal je Halt, nicht jede Runde."""
    halt.anlegen(klon, "Nachtruhe", _bis_spaeter())
    monkeypatch.setattr(halt, "_erfuellt", _kaputt)
    with caplog.at_level(logging.DEBUG, logger="to_spawn.halt"):
        for _ in range(3):
            assert halt.pruefen(klon) is not None
    assert len([r for r in caplog.records if r.exc_info and r.levelno >= logging.WARNING]) == 1


def test_sauberer_lauf_loescht_merker_frist_zaehlt_neu(klon: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review (b): +25 h nach sauberem Lauf darf ein alter Merker nicht mehr zählen.

    Bliebe der Merker vom ersten Fehler liegen, fiele der Halt beim zweiten Fehler sofort.
    """
    halt.anlegen(klon, "Nachtruhe", _bis_spaeter())
    echt = halt._erfuellt
    monkeypatch.setattr(halt, "_erfuellt", _kaputt)
    assert halt.pruefen(klon) is not None
    assert (klon / halt.MERKER_DATEI).exists()
    monkeypatch.setattr(halt, "_erfuellt", echt)
    assert halt.pruefen(klon) is not None
    assert not (klon / halt.MERKER_DATEI).exists()
    spaeter = datetime.now(UTC).astimezone() + halt.ALT_FRIST + timedelta(hours=1)
    monkeypatch.setattr(halt, "_jetzt", lambda: spaeter)
    monkeypatch.setattr(halt, "_erfuellt", _kaputt)
    assert halt.pruefen(klon) is not None
    assert (klon / halt.DATEI).exists()


def test_programmfehler_haelt_gueltigen_halt_ueber_frist(klon: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix-Runde 1: wirft ``_erfuellt`` dauerhaft, Ticket existiert ⇒ Halt bleibt auch nach der Frist."""
    halt.anlegen(klon, "Vorrang", halt.Bedingung("ticket_zu", "1346"))
    monkeypatch.setattr(halt, "_erfuellt", _kaputt)
    assert halt.pruefen(klon) is not None
    _merker_altern(klon)
    assert halt.pruefen(klon) is not None
    assert (klon / halt.DATEI).exists()
    assert _sicherungen(klon) == []


def test_git_ausfall_warnt_nur_einmal_je_halt(
    klon: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """#895: nicht erreichbares ``git`` (OSError/Timeout) ⇒ Halt bleibt über Frist, genau eine Warnung je Halt."""
    halt.anlegen(klon, "Fix", halt.Bedingung("commit", "abcdef1234567"))

    def _git_weg(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("git fehlt")

    monkeypatch.setattr(halt.subprocess, "run", _git_weg)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="to_spawn.halt"):
        assert halt.pruefen(klon) is not None
        assert halt.pruefen(klon) is not None
        _merker_altern(klon)
        assert halt.pruefen(klon) is not None
        assert halt.pruefen(klon) is not None
    assert (klon / halt.DATEI).exists()
    assert _sicherungen(klon) == []
    warnungen = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnungen) == 1, [r.getMessage() for r in warnungen]


def test_sicherung_bei_identischem_zeitstempel_eindeutig(klon: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#895: zwei Fristabläufe mit exakt gleichem Zeitstempel ⇒ zwei eindeutige Sicherungen ohne Überschreiben."""
    fest = datetime.now(UTC).astimezone().replace(microsecond=0)
    monkeypatch.setattr(halt, "_jetzt", lambda: fest)
    echt = halt._erfuellt
    inhalte: list[str] = []
    for grund in ("erster", "zweiter"):
        halt.anlegen(klon, grund, _bis_spaeter())
        inhalte.append((klon / halt.DATEI).read_text(encoding="utf-8"))
        monkeypatch.setattr(halt, "_erfuellt", _kaputt)
        assert halt.pruefen(klon) is not None
        _merker_altern(klon)
        assert halt.pruefen(klon) is None
        monkeypatch.setattr(halt, "_erfuellt", echt)
    gesichert = _sicherungen(klon)
    assert len(gesichert) == 2
    assert sorted(p.read_text(encoding="utf-8") for p in gesichert) == sorted(inhalte)
    aufgehoben = [z for z in _log(klon) if z["ereignis"] == "aufgehoben"]
    assert sorted(z["gesichert_als"] for z in aufgehoben) == sorted(p.name for p in gesichert)
