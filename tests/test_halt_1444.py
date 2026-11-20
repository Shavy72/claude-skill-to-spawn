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
        json.dumps({"hauptzweig": "master", "wt_basis": str(tmp_path / "wts")}),
        encoding="utf-8",
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


# -- Fix-Runde 1 (Review #1444) ---------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _mit_origin(klon: Path, tmp_path: Path) -> str:
    """Bare-Repo als origin mit einem Commit auf master; gibt dessen SHA zurück."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(bare)], check=True)
    _git(
        klon,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "eins",
    )
    _git(klon, "remote", "add", "origin", str(bare))
    _git(klon, "push", "-q", "origin", "master")
    return _git(klon, "rev-parse", "HEAD")


def test_anlegen_lehnt_unbekanntes_ticket_ab(klon: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_STUB_FEHLT", "99999")
    with pytest.raises(ValueError, match="99999"):
        halt.anlegen(klon, "Tippfehler", halt.Bedingung("ticket_zu", "99999"))
    assert halt.main(["--repo-dir", str(klon), "anlegen", "--grund", "x", "--ticket-zu", "99999"]) == 2
    assert not (klon / halt.DATEI).exists()


def test_anlegen_lehnt_unbekannten_commit_ab(klon: Path, tmp_path: Path) -> None:
    sha = _mit_origin(klon, tmp_path)
    with pytest.raises(ValueError, match="deadbeef"):
        halt.anlegen(klon, "Tippfehler", halt.Bedingung("commit", "deadbeefdeadbeef"))
    assert not (klon / halt.DATEI).exists()
    # Bekannter Commit: anlegbar, und da er schon auf origin/master liegt, hebt pruefen auf.
    halt.anlegen(klon, "warte auf Fix", halt.Bedingung("commit", sha))
    assert halt.pruefen(klon) is None
    assert not (klon / halt.DATEI).exists()


def test_anlegen_bei_netzfehler_erlaubt(klon: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub/origin nicht erreichbar ⇒ nicht fälschlich ablehnen; die Frist fängt es."""
    monkeypatch.setenv("GH_STUB_KAPUTT", "1346")
    halt.anlegen(klon, "Vorrang", halt.Bedingung("ticket_zu", "1346"))
    assert halt.aufheben(klon, "test")
    # Klon ohne origin: fetch scheitert ⇒ Commit nicht entscheidbar ⇒ anlegbar.
    halt.anlegen(klon, "Fix", halt.Bedingung("commit", "abcdef1234567"))
    assert (klon / halt.DATEI).exists()


def test_nicht_pruefbar_hebt_nach_frist_auf(
    klon: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("GH_STUB_KAPUTT", "1346")
    halt.anlegen(klon, "Vorrang", halt.Bedingung("ticket_zu", "1346"))
    datei = klon / halt.DATEI
    # Alter Halt, aber erst jetzt nicht prüfbar ⇒ bleibt (Frist zählt ab erstem Fehlschlag).
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["seit"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    datei.write_text(json.dumps(daten), encoding="utf-8")
    merker = klon / halt.MERKER_DATEI
    assert halt.pruefen(klon) is not None
    assert merker.exists(), "erster Fehlschlag muss gemerkt werden"
    # Wieder prüfbar (Ticket offen) ⇒ Fehlschlag-Merker weg.
    monkeypatch.delenv("GH_STUB_KAPUTT")
    assert halt.pruefen(klon) is not None
    assert not merker.exists()
    # Länger als die Frist nicht prüfbar ⇒ Halt hebt sich mit Warnung auf.
    monkeypatch.setenv("GH_STUB_KAPUTT", "1346")
    assert halt.pruefen(klon) is not None
    stand = json.loads(merker.read_text(encoding="utf-8"))
    stand["seit"] = (datetime.now(timezone.utc) - halt.ALT_FRIST - timedelta(minutes=1)).isoformat()
    merker.write_text(json.dumps(stand), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="to_spawn.halt"):
        assert halt.pruefen(klon) is None
    assert not datei.exists() and not merker.exists()
    assert "nicht prüfbar" in _log(klon)[-1]["anlass"]
    assert any("nicht prüfbar" in r.getMessage() and "aufgehoben" in r.getMessage() for r in caplog.records)


def test_probelauf_laesst_erfuellten_halt_liegen(klon: Path) -> None:
    vorbei = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    halt.anlegen(klon, "kurz", halt.Bedingung("bis", vorbei))
    assert halt.pruefen(klon, aufheben=False) is None
    assert (klon / halt.DATEI).exists()
    lauf = subprocess.run(
        [
            sys.executable,
            str(SKILL / "to_spawn.py"),
            "takt",
            "1307",
            "--repo-dir",
            str(klon),
            "--gh-repo",
            "Shavy72/duoplus-management",
            "--dry-run",
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
    assert (klon / halt.DATEI).exists(), lauf.stdout + lauf.stderr
    assert [z["ereignis"] for z in _log(klon)] == ["angelegt"]


def test_aufpasser_startet_nicht_bei_halt(klon: Path, tmp_path: Path) -> None:
    from to_spawn import aufpasser

    pa = aufpasser.Aufpasser(
        aufpasser.Einstellungen(zustand=tmp_path / "zustand", tmux_socket="nie-benutzt", trocken=True)
    )
    assert pa.start_erlaubt("1307", klon, None) is True
    spaeter = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    halt.anlegen(klon, "Nachtruhe", halt.Bedingung("bis", spaeter))
    assert pa.start_erlaubt("1307", klon, None) is False
    assert pa.start_erlaubt("1307", klon, 5) is False  # vor jedem Ticket-Lesen gesperrt
    assert halt.aufheben(klon, "test")
    assert pa.start_erlaubt("1307", klon, None) is True


def test_unlesbare_halt_datei_wirft_nie(klon: Path, caplog: pytest.LogCaptureFixture) -> None:
    datei = klon / halt.DATEI
    datei.write_bytes(b"\xff\xfe kaputt")  # kein UTF-8
    with caplog.at_level(logging.WARNING, logger="to_spawn.halt"):
        assert halt.pruefen(klon) is not None  # blockiert diese Spec
    assert any("nicht lesbar" in r.getMessage() for r in caplog.records)
    alt = (datetime.now(timezone.utc) - timedelta(hours=25)).timestamp()
    os.utime(datei, (alt, alt))
    assert halt.pruefen(klon) is None  # Frist ab Änderungszeit
    assert not datei.exists()
    datei.mkdir()  # Ordner statt Datei ⇒ IsADirectoryError
    assert halt.pruefen(klon) is not None
    os.utime(datei, (alt, alt))
    assert halt.pruefen(klon) is None  # Löschen scheitert ⇒ Warnung, keine Ausnahme
