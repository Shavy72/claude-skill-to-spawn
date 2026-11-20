"""Ticket #1444: Halt einer Spec hebt sich selbst auf, wenn die Bedingung erfüllt ist.

Weg-Test ohne Attrappen für die Halt-Logik: echter Unterprozess ``python to_spawn.py
takt …``, echtes Git-Repo, echte Halt-Datei. GitHub (externer Dienst) läuft über die
übliche Test-Naht ``TO_SPAWN_GH_STUB`` → ``tests/hilfen/gh_stub.py``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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
        json.dumps({"hauptzweig": "master", "wt_basis": str(tmp_path / "wts")}), encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(GH_STUB))
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    monkeypatch.setenv("TO_SPAWN_WAECHTER_ORDNER", str(tmp_path / "waechter"))
    monkeypatch.delenv("GH_STUB_ZU", raising=False)
    monkeypatch.delenv("GH_STUB_KAPUTT", raising=False)
    return repo


def _log(klon: Path) -> list[dict]:
    datei = klon / halt.LOG_DATEI
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z]


def _takt(klon: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SKILL / "to_spawn.py"),
            "takt",
            "1307",
            "--repo-dir",
            str(klon),
            "--gh-repo",
            "Shavy72/duoplus-management",
        ],
        cwd=str(SKILL),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def test_ticket_zu_hebt_halt_im_naechsten_takt_auf(klon: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    halt.anlegen(klon, "Spec #1330 hat Vorrang", halt.Bedingung("ticket_zu", "1346"), wer="leit")
    erster = _takt(klon)
    assert erster.returncode == 0, erster.stdout + erster.stderr
    assert "Halt" in erster.stdout and "#1346" in erster.stdout
    assert (klon / halt.DATEI).exists(), "Ticket offen — Halt muss stehen bleiben"

    monkeypatch.setenv("GH_STUB_ZU", "1346")  # Ticket geschlossen
    _takt(klon)  # danach läuft der Takt normal weiter (capo), Ergebnis hier egal
    assert not (klon / halt.DATEI).exists(), "Bedingung erfüllt — Halt muss weg sein"
    letzte = _log(klon)[-1]
    assert letzte["ereignis"] == "aufgehoben" and letzte["wer_hebt"] == "takt"
    assert letzte["bedingung"] == {"art": "ticket_zu", "wert": "1346"}


@pytest.mark.parametrize("bedingung", [None, "Ticket 1346 zu"])
def test_anlage_ohne_bedingung_abgelehnt(klon: Path, bedingung: object) -> None:
    with pytest.raises(ValueError):
        halt.anlegen(klon, "Grund", bedingung)  # type: ignore[arg-type]
    assert not (klon / halt.DATEI).exists()
    with pytest.raises(ValueError):
        halt.Bedingung("ticket_zu", "abc")
    # Befehlszeile verlangt genau eine Bedingung.
    with pytest.raises(SystemExit):
        halt.main(["--repo-dir", str(klon), "anlegen", "--grund", "x"])
    assert not (klon / halt.DATEI).exists()


def test_bis_zeitpunkt_laeuft_ab(klon: Path) -> None:
    vorbei = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    spaeter = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    halt.anlegen(klon, "Nachtruhe", halt.Bedingung("bis", spaeter))
    assert halt.pruefen(klon) is not None
    assert halt.aufheben(klon, "test")
    halt.anlegen(klon, "kurz", halt.Bedingung("bis", vorbei))
    assert halt.pruefen(klon) is None
    assert not (klon / halt.DATEI).exists()
    assert _log(klon)[-1]["ereignis"] == "aufgehoben"


def test_nicht_pruefbar_halt_bleibt(
    klon: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("GH_STUB_KAPUTT", "1346")
    halt.anlegen(klon, "Vorrang", halt.Bedingung("ticket_zu", "1346"))
    with caplog.at_level(logging.WARNING, logger="to_spawn.halt"):
        assert halt.pruefen(klon) is not None
    assert (klon / halt.DATEI).exists()
    assert any("nicht prüfbar" in r.getMessage() for r in caplog.records)


def test_alter_halt_ohne_bedingung_laeuft_nach_24h_ab(klon: Path, caplog: pytest.LogCaptureFixture) -> None:
    seit = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    (klon / halt.DATEI).write_text(json.dumps({"grund": "von Hand", "seit": seit}), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="to_spawn.halt"):
        assert halt.pruefen(klon) is None
    assert any("ohne gültige Bedingung" in r.getMessage() for r in caplog.records)
    assert not (klon / halt.DATEI).exists()
