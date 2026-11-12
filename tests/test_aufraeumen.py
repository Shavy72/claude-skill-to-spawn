"""#1184: Platte hält sich selbst sauber — Entscheidung gegen echte Git-Repos.

Jeder Test baut in ``tmp_path`` ein bare ``origin``, einen Klon und einen Worktree mit
echtem ``git`` (keine Attrappe). Nur ``disk_usage`` der Platten-Sperre ist gestubbt —
das ist Rechenlogik, kein Nutzer-Ablauf.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from to_spawn import aufraeumen, speicher


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def welt(tmp_path: Path) -> dict[str, Path]:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "master", str(origin))
    klon = tmp_path / "duoplus-901"
    _git(tmp_path, "clone", str(origin), str(klon))
    _git(klon, "config", "user.email", "t@t")
    _git(klon, "config", "user.name", "t")
    (klon / "a.txt").write_text("a", encoding="utf-8")
    _git(klon, "add", "a.txt")
    _git(klon, "commit", "-m", "start")
    _git(klon, "push", "origin", "master")
    wt = tmp_path / "wt" / "wt-902"
    _git(klon, "worktree", "add", "-b", "ticket/902-probe", str(wt))
    _git(wt, "push", "-u", "origin", "ticket/902-probe")
    return {"home": tmp_path, "klon": klon, "wt": wt}


def _blick(home: Path) -> str:
    datei = home / "braucht-blick.txt"
    return datei.read_text(encoding="utf-8") if datei.exists() else ""


def test_sauber_und_geschlossen_worktree_weg(welt: dict[str, Path]) -> None:
    wt, home = welt["wt"], welt["home"]
    assert aufraeumen.nummer(wt) == 902
    assert aufraeumen.nach_ende(wt, geschlossen=True, home=home) is True
    assert not wt.exists()
    assert str(wt) not in _git(welt["klon"], "worktree", "list")
    protokoll = list(home.glob("geloescht-*.txt"))
    assert len(protokoll) == 1 and str(wt) in protokoll[0].read_text(encoding="utf-8")


def test_sauberer_klon_geschlossen_weg(welt: dict[str, Path]) -> None:
    home, klon = welt["home"], welt["klon"]
    assert aufraeumen.nach_ende(welt["wt"], geschlossen=True, home=home) is True
    assert aufraeumen.nummer(klon) == 901
    assert aufraeumen.nach_ende(klon, geschlossen=True, home=home) is True
    assert not klon.exists()


def test_ungespeicherte_datei_bleibt(welt: dict[str, Path]) -> None:
    wt, home = welt["wt"], welt["home"]
    (wt / "neu.txt").write_text("x", encoding="utf-8")
    ok, grund = aufraeumen.darf_weg(wt, geschlossen=True)
    assert ok is False and "ungespeicherte Änderungen" in grund
    assert aufraeumen.nach_ende(wt, geschlossen=True, home=home) is False
    assert (wt / "neu.txt").exists()
    assert str(wt) in _blick(home) and "ungespeichert" in _blick(home)


def test_ungepushter_commit_bleibt(welt: dict[str, Path]) -> None:
    wt, home = welt["wt"], welt["home"]
    (wt / "b.txt").write_text("b", encoding="utf-8")
    _git(wt, "add", "b.txt")
    _git(wt, "commit", "-m", "lokal")
    assert aufraeumen.nach_ende(wt, geschlossen=True, home=home) is False
    assert wt.exists()
    assert "nicht auf origin" in _blick(home)


def test_klon_mit_ungepushtem_nebenbranch_bleibt(welt: dict[str, Path]) -> None:
    home, klon = welt["home"], welt["klon"]
    aufraeumen.nach_ende(welt["wt"], geschlossen=True, home=home)
    _git(klon, "branch", "nebenbei")
    _git(klon, "checkout", "-q", "nebenbei")
    (klon / "c.txt").write_text("c", encoding="utf-8")
    _git(klon, "add", "c.txt")
    _git(klon, "commit", "-m", "neben")
    _git(klon, "checkout", "-q", "master")
    ok, grund = aufraeumen.darf_weg(klon, geschlossen=True)
    assert ok is False and "nicht auf origin" in grund


def test_offenes_ticket_bleibt(welt: dict[str, Path]) -> None:
    wt, home = welt["wt"], welt["home"]
    assert aufraeumen.nach_ende(wt, geschlossen=False, home=home) is False
    assert wt.exists()
    assert "Ticket noch offen" in _blick(home)


def test_loesch_sperre_schuetzt_home_und_claude(tmp_path: Path) -> None:
    (tmp_path / ".claude" / "x").mkdir(parents=True)
    assert aufraeumen._loesch_sperre(tmp_path, tmp_path)
    assert aufraeumen._loesch_sperre(tmp_path / ".claude" / "x", tmp_path)
    (tmp_path / "duoplus-1").mkdir()
    assert aufraeumen._loesch_sperre(tmp_path / "duoplus-1", tmp_path) is None


def test_platten_sperre_raeumt_auf_dann_ablehnung(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TO_SPAWN_ZULASSUNG_STAND", str(tmp_path / "zulassung.json"))
    messungen = iter([10.0, 12.0, 12.0])
    monkeypatch.setattr(speicher, "platte_frei_gb", lambda pfad=None: next(messungen))
    laeufe: list[bool] = []
    monkeypatch.setattr(aufraeumen, "taeglich", lambda **kw: laeufe.append(kw.get("trocken", False)) or 0)
    ok, grund = speicher.platte_urteil(None)
    assert ok is False and "Platte voll" in grund and "30 GB" in grund
    assert laeufe == [False]
    # zweiter Anlauf innerhalb des Abstands räumt nicht erneut auf
    assert speicher.platte_urteil(None)[0] is False
    assert laeufe == [False]


def test_platten_sperre_in_zulassen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(speicher.sys, "platform", "linux")
    monkeypatch.setenv("TO_SPAWN_ZULASSUNG_STAND", str(tmp_path / "zulassung.json"))
    messungen = iter([5.0, 5.0, 40.0])
    monkeypatch.setattr(speicher, "platte_frei_gb", lambda pfad=None: next(messungen))
    laeufe: list[int] = []
    monkeypatch.setattr(aufraeumen, "taeglich", lambda **kw: laeufe.append(1) or 1)
    ok, grund = speicher.zulassen(None, reservieren=False, frei=10 * 1024, sessions=0)
    assert ok is False and "Platte voll" in grund and laeufe == []  # nur fragen: kein Aufräumen
    ok, _ = speicher.zulassen(None, frei=10 * 1024, sessions=0, jetzt=1.0)
    assert ok is True and laeufe == [1]  # 5 GB → aufgeräumt → 40 GB
