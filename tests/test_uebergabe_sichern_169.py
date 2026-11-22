"""#169: Probelauf lädt nie hoch, Zweige mit Übergabe-/Entscheidungs-Dateien bleiben lokal.

Echte Git-Repos unter ``tmp_path``; „origin“ ist ein lokales bare-Repo, nie GitHub. Ziel und
Maschinen-Liste setzt die autouse-Fixture in ``conftest.py`` auf ``tmp``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import hausmeister, konsole
from to_spawn import uebergabe_sichern as us

WURZEL = Path(__file__).resolve().parent.parent


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _entfernte_zweige(origin: Path) -> list[str]:
    roh = _git(origin, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return sorted(roh.split())


@pytest.fixture
def skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Klon mit bare-„origin“, ``main`` hochgeladen, Zweig-Upload zeigt auf den Klon."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = tmp_path / "skill"
    _git(tmp_path, "clone", "-q", str(origin), str(repo))
    for k, v in (
        ("user.email", "t@t"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ):
        _git(repo, "config", k, v)
    _git(repo, "checkout", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "basis")
    _git(repo, "push", "-q", "origin", "main")
    monkeypatch.setenv(us.UMGEBUNG_ZWEIG_REPOS, str(repo))
    monkeypatch.setenv(us.UMGEBUNG_ZIEL, str(tmp_path / "tresor"))
    return {"tmp": tmp_path, "origin": origin, "repo": repo}


def _zweig(
    skill: dict[str, Path], name: str, dateien: list[tuple[str, str | None]]
) -> Path:
    """Worktree auf neuem Zweig ``name``; je Datei ein Commit (``None`` = löschen)."""
    wt = skill["tmp"] / name.replace("/", "-")
    _git(skill["repo"], "worktree", "add", "-q", "-b", name, str(wt), "main")
    for pfad, inhalt in dateien:
        datei = wt / pfad
        if inhalt is None:
            _git(wt, "rm", "-q", pfad)
        else:
            datei.parent.mkdir(parents=True, exist_ok=True)
            datei.write_text(inhalt, encoding="utf-8")
            _git(wt, "add", "-f", pfad)
        _git(wt, "commit", "-q", "-m", f"{name}: {pfad}")
    return wt


# --- Punkt 1: Probelauf lädt nie hoch -------------------------------------------------


def test_hausmeister_probelauf_laedt_keinen_zweig_hoch(skill: dict[str, Path]) -> None:
    _zweig(skill, "l1/arbeit", [("code.py", "x = 1\n")])
    hausmeister.rundgang(loeschen=False)
    assert _entfernte_zweige(skill["origin"]) == ["main"]
    assert not (skill["tmp"] / "tresor").exists()  # Probelauf schreibt auch nichts
    hausmeister.rundgang(loeschen=True)  # echter Rundgang: Netz bleibt
    assert _entfernte_zweige(skill["origin"]) == ["l1/arbeit", "main"]


def test_trocken_meldet_was_geschaehe_ohne_zu_schreiben(skill: dict[str, Path]) -> None:
    _zweig(skill, "l1/arbeit", [("code.py", "x = 1\n")])
    erg = us.sichern(trocken=True)
    assert erg.trocken and erg.hochgeladen == ["skill:l1/arbeit"], erg.fehler
    assert "würde" in erg.zeile()
    assert _entfernte_zweige(skill["origin"]) == ["main"]
    assert not (skill["tmp"] / "tresor").exists()


def test_trocken_kopiert_keine_uebergabe(skill: dict[str, Path]) -> None:
    datei = skill["tmp"] / "leit" / ".claude-handoff-loop.md"
    datei.parent.mkdir()
    datei.write_text("Übergabe\n", encoding="utf-8")
    erg = us.sichern([datei], trocken=True)
    assert erg.fehler == [] and datei in erg.kopiert
    assert f"würde {len(erg.kopiert)} Datei(en)" in erg.zeile()
    assert not (skill["tmp"] / "tresor").exists()
    assert datei in us.sichern([datei]).kopiert  # echt: wird geschrieben


# --- Punkt 2: Inhaltsprüfung vor dem Upload ---------------------------------------------


def test_zweig_mit_uebergabe_oder_log_wird_laut_verweigert(
    skill: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    _zweig(skill, "l1/sauber", [("code.py", "x = 1\n")])
    _zweig(skill, "l1/uebergabe", [(".claude-handoff-loop.md", "roh\n")])
    # Später gelöscht, aber in der Historie: würde mit dem Push trotzdem öffentlich.
    _zweig(
        skill,
        "l1/log",
        [("docs/entscheidungen.md", "E1\n"), ("docs/entscheidungen.md", None)],
    )
    erg = us.sichern()
    assert erg.hochgeladen == ["skill:l1/sauber"]
    assert _entfernte_zweige(skill["origin"]) == ["l1/sauber", "main"]
    gesperrt = [f for f in erg.fehler if "verweigert" in f]
    assert len(gesperrt) == 2, erg.fehler
    assert any("l1/uebergabe" in f and ".claude-handoff-loop.md" in f for f in gesperrt)
    assert any("l1/log" in f and "docs/entscheidungen.md" in f for f in gesperrt)
    assert "verweigert" in caplog.text


def test_nie_oeffentlich_kennt_die_muster() -> None:
    for pfad in (
        ".claude-handoff.md",
        "a/.claude-handoff-bausystem-loop.md",
        "x/entscheidungen.md",
    ):
        assert us.nie_oeffentlich(pfad), pfad
    for pfad in (
        "claude-handoff.md",
        "entscheidungen.md.bak",
        "docs/handoff.md",
        "code.py",
    ):
        assert not us.nie_oeffentlich(pfad), pfad


# --- Punkt 3: .gitignore deckt dieselben Muster ----------------------------------------


def test_gitignore_deckt_alle_nie_oeffentlich_muster() -> None:
    zeilen = (WURZEL / ".gitignore").read_text(encoding="utf-8").splitlines()
    for muster in us.MUSTER_JE_REPO:
        assert muster in zeilen, muster
    proben = [".claude-handoff-loop.md", "docs/entscheidungen.md"]
    aus = subprocess.run(
        ["git", "check-ignore", "--no-index", *proben],
        cwd=WURZEL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert sorted(aus.stdout.split()) == sorted(proben)


# --- Punkt 4: CLI ``sichern --trocken`` ---------------------------------------------


def test_cli_sichern_trocken_laedt_nichts_hoch(skill: dict[str, Path]) -> None:
    _zweig(skill, "l1/arbeit", [("code.py", "x = 1\n")])
    aus = subprocess.run(
        [sys.executable, str(WURZEL / "to_spawn.py"), "sichern", "--trocken"],
        cwd=skill["repo"],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        **konsole.UNTERPROZESS_TEXT,  # die CLI schreibt UTF-8, auch in Windows-Pipes
        timeout=60,
        check=False,
    )
    assert aus.returncode == 0, aus.stderr
    assert "würde" in aus.stdout and "1 Zweig(e)" in aus.stdout
    assert _entfernte_zweige(skill["origin"]) == ["main"]
    assert not (skill["tmp"] / "tresor").exists()
