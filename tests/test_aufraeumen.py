"""#1184: Platte hält sich selbst sauber — Entscheidung gegen echte Git-Repos.

Jeder Test baut in ``tmp_path`` ein bare ``origin``, einen Klon und einen Worktree mit
echtem ``git`` (keine Attrappe). Nur ``disk_usage`` der Platten-Sperre ist gestubbt —
das ist Rechenlogik, kein Nutzer-Ablauf.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import time
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


# --- Datenverlust-Schutz (#1184 Review) -------------------------------------------------


def _ohne_worktree(welt: dict[str, Path]) -> Path:
    assert aufraeumen.nach_ende(welt["wt"], geschlossen=True, home=welt["home"]) is True
    return welt["klon"]


def test_klon_mit_stash_bleibt(welt: dict[str, Path]) -> None:
    klon, home = _ohne_worktree(welt), welt["home"]
    (klon / "a.txt").write_text("geaendert", encoding="utf-8")
    _git(klon, "stash")
    assert aufraeumen.nach_ende(klon, geschlossen=True, home=home) is False
    assert klon.exists() and "Stash" in _blick(home)


def test_klon_mit_ungepushtem_nebenbranch_nach_ende_bleibt(welt: dict[str, Path]) -> None:
    klon, home = _ohne_worktree(welt), welt["home"]
    _git(klon, "checkout", "-q", "-b", "nebenbei")
    (klon / "c.txt").write_text("c", encoding="utf-8")
    _git(klon, "add", "c.txt")
    _git(klon, "commit", "-m", "neben")
    _git(klon, "checkout", "-q", "master")
    assert aufraeumen.nach_ende(klon, geschlossen=True, home=home) is False
    assert klon.exists() and "nicht auf origin" in _blick(home)


def test_klon_mit_verknuepftem_worktree_bleibt(welt: dict[str, Path]) -> None:
    klon, home = welt["klon"], welt["home"]
    assert aufraeumen.nach_ende(klon, geschlossen=True, home=home) is False
    assert klon.exists() and "verknüpfte Worktrees" in _blick(home)


def _gitignore(klon: Path) -> None:
    (klon / ".gitignore").write_text(".env\nnode_modules/\ngross.bin\n", encoding="utf-8")
    _git(klon, "add", ".gitignore")
    _git(klon, "commit", "-m", "ignore")
    _git(klon, "push", "origin", "master")


def test_klon_ignorierte_env_wird_gesichert(welt: dict[str, Path]) -> None:
    klon, home = _ohne_worktree(welt), welt["home"]
    _gitignore(klon)
    (klon / ".env").write_text("GEHEIM=1\n", encoding="utf-8")
    (klon / "node_modules" / "x").mkdir(parents=True)
    (klon / "node_modules" / "x" / "i.js").write_text("x", encoding="utf-8")
    assert aufraeumen.nach_ende(klon, geschlossen=True, home=home) is True
    assert not klon.exists()
    sicherung = home / "aufraeum-sicherung" / "duoplus-901"
    assert (sicherung / ".env").read_text(encoding="utf-8") == "GEHEIM=1\n"
    assert not (sicherung / "node_modules").exists()


def test_klon_ignorierte_datei_zu_gross_bleibt(welt: dict[str, Path]) -> None:
    klon, home = _ohne_worktree(welt), welt["home"]
    _gitignore(klon)
    (klon / "gross.bin").write_bytes(b"0" * (aufraeumen.SICHERUNG_MAX_BYTES + 1))
    assert aufraeumen.nach_ende(klon, geschlossen=True, home=home) is False
    assert klon.exists() and "zu groß" in _blick(home)


def test_trocken_sichert_nicht(welt: dict[str, Path]) -> None:
    klon, home = _ohne_worktree(welt), welt["home"]
    _gitignore(klon)
    (klon / ".env").write_text("GEHEIM=1\n", encoding="utf-8")
    assert aufraeumen.nach_ende(klon, geschlossen=True, home=home, trocken=True) is True
    assert klon.exists() and not (home / "aufraeum-sicherung").exists()


def test_trocken_worktree_bleibt_ohne_protokoll(welt: dict[str, Path]) -> None:
    wt, home = welt["wt"], welt["home"]
    assert aufraeumen.nach_ende(wt, geschlossen=True, home=home, trocken=True) is True
    assert wt.exists()
    assert list(home.glob("geloescht-*.txt")) == []


@pytest.mark.parametrize("besetzt", [True, None])
def test_besetzt_oder_unmessbar_bleibt(monkeypatch: pytest.MonkeyPatch, welt: dict[str, Path], besetzt: bool | None) -> None:
    wt, home = welt["wt"], welt["home"]
    monkeypatch.setattr(aufraeumen, "_arbeitet_darin", lambda pfad: (besetzt, "Messung: Probe"))
    assert aufraeumen.darf_weg(wt, geschlossen=True) == (False, "Messung: Probe")
    assert aufraeumen.nach_ende(wt, geschlossen=True, home=home) is False
    assert wt.exists()


def _taeglich_spec_klon(monkeypatch: pytest.MonkeyPatch, welt: dict[str, Path], daten: object) -> int:
    klon = _ohne_worktree(welt)
    monkeypatch.setattr(aufraeumen, "kandidaten", lambda home: [klon])
    monkeypatch.setattr(aufraeumen, "_befehl", lambda args, trocken: None)
    monkeypatch.setattr(aufraeumen, "_tmp_alt", lambda trocken, jetzt: 0)
    monkeypatch.setattr(aufraeumen.gh, "repo_aus_origin", lambda cwd=None, fallback="": "probe/repo")
    monkeypatch.setattr(aufraeumen.gh, "json_lauf", lambda args, cwd=None: daten)
    return aufraeumen.taeglich(home=welt["home"])


def _vor_tagen(tage: float) -> str:
    zeit = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=tage)
    return zeit.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_spec_klon_erst_drei_tage_nach_schliessen_weg(monkeypatch: pytest.MonkeyPatch, welt: dict[str, Path]) -> None:
    assert _taeglich_spec_klon(monkeypatch, welt, {"state": "CLOSED", "closedAt": _vor_tagen(1)}) == 0
    assert welt["klon"].exists()


def test_spec_klon_vier_tage_geschlossen_weg(monkeypatch: pytest.MonkeyPatch, welt: dict[str, Path]) -> None:
    assert _taeglich_spec_klon(monkeypatch, welt, {"state": "CLOSED", "closedAt": _vor_tagen(4)}) == 1
    assert not welt["klon"].exists()


@pytest.mark.parametrize("daten", [None, {"state": "CLOSED"}, {"state": "CLOSED", "closedAt": "kaputt"}])
def test_spec_klon_gh_unlesbar_bleibt(monkeypatch: pytest.MonkeyPatch, welt: dict[str, Path], daten: object) -> None:
    assert _taeglich_spec_klon(monkeypatch, welt, daten) == 0
    assert welt["klon"].exists() and str(welt["klon"]) in _blick(welt["home"])


def _alt_machen(pfad: Path, alter_s: float) -> None:
    zeit = time.time() - alter_s
    for wurzel, ordner, namen in os.walk(pfad, topdown=False):
        for name in [*ordner, *namen]:
            os.utime(Path(wurzel, name), (zeit, zeit))
    os.utime(pfad, (zeit, zeit))


def test_tmp_alt_nur_ganz_alter_baum_weg(tmp_path: Path) -> None:
    alt = 3 * 24 * 3600
    tief = tmp_path / "mit-frischem" / "a" / "b"
    tief.mkdir(parents=True)
    (tief / "frisch.txt").write_text("x", encoding="utf-8")
    _alt_machen(tmp_path / "mit-frischem", alt)
    jetzt = time.time()
    os.utime(tief / "frisch.txt", (jetzt, jetzt))
    (tmp_path / "ganz-alt" / "c").mkdir(parents=True)
    (tmp_path / "ganz-alt" / "c" / "d.txt").write_text("d", encoding="utf-8")
    _alt_machen(tmp_path / "ganz-alt", alt)
    assert aufraeumen._tmp_alt(True, jetzt, wurzel=tmp_path) == 1
    assert (tmp_path / "ganz-alt").exists() and (tmp_path / "mit-frischem").exists()
    assert aufraeumen._tmp_alt(False, jetzt, wurzel=tmp_path) == 1
    assert not (tmp_path / "ganz-alt").exists()
    assert (tief / "frisch.txt").exists()


def test_tmp_alt_unmessbar_bleibt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "alt").mkdir()
    _alt_machen(tmp_path / "alt", 3 * 24 * 3600)
    monkeypatch.setattr(aufraeumen, "_arbeitet_darin", lambda pfad: (None, "keine Messung"))
    assert aufraeumen._tmp_alt(False, time.time(), wurzel=tmp_path) == 0
    assert (tmp_path / "alt").exists()
