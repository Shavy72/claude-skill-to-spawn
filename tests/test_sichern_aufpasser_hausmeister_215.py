"""#215: Sichern im Aufpasser gegen ``MUSTER_JE_REPO`` und Geheimnis-Prüfung per ``-z``.

Echte Git-Repos unter ``tmp_path`` mit bare-„origin“ (kein Git-Mock):
- (a) ``aufpasser.sichern``, ``sichern_worktree`` und ``sichern_und_fortsetzen`` prüfen
  vor dem Push über :func:`to_spawn.uebergabe_sichern.gesperrte_pfade` (eine Sperre).
- (b) Geheimnis-Prüfung im Hausmeister (Ordner und Zweig) liest Dateinamen per ``-z``
  und trennt an NUL (Leerzeichen und Umlaute bleiben ganz).
- (c) Vereinheitlichte NUL-Zerlegung ohne ``strip("\\r\\n")`` erkennt Dateinamen mit
  Zeilenumbruch am Ende korrekt.
"""

# ruff: noqa: F811 — Fixtures kommen aus bestehenden Test-Modulen und heißen wie ihre Parameter.
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest
from test_hausmeister_1368 import Welt, git, welt  # noqa: F401 — Fixture
from test_hausmeister_1439 import zw  # noqa: F401 — Fixture

from to_spawn import aufpasser, hausmeister
from to_spawn import uebergabe_sichern as us

NUR_POSIX = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows erlaubt keinen Zeilenumbruch im Dateinamen",
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _entfernte_zweige(origin: Path) -> list[str]:
    roh = _git(origin, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return sorted(roh.split())


def _repo_mit_worktree(tmp_path: Path, ticket: int) -> tuple[Path, Path, Path]:
    """Echtes Repo mit bare-Origin und Ticket-Worktree ``wt-<ticket>``."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = tmp_path / "repo"
    _git(tmp_path, "clone", "-q", str(origin), str(repo))
    for k, v in (
        ("user.email", "test@example.invalid"),
        ("user.name", "Test"),
        ("commit.gpgsign", "false"),
        # Unter Windows setzt ``git clone`` ``core.ignorecase=true``, sodass ``.gitignore``
        # Groß-/Kleinschreibung ignoriert und ungetrackte Dateien in anderer Schreibung
        # gar nicht erst in ``git add -A`` landen (der Fall aus #215).
        ("core.ignorecase", "false"),
    ):
        _git(repo, "config", k, v)
    _git(repo, "checkout", "-q", "-b", "main")
    (repo / ".gitignore").write_text(".claude-handoff*.md\nentscheidungen.md\n", encoding="utf-8")
    (repo / "README.md").write_text("basis\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "README.md")
    _git(repo, "commit", "-q", "-m", "basis")
    _git(repo, "push", "-q", "origin", "main")
    wt = tmp_path / f"wt-{ticket}"
    _git(repo, "worktree", "add", "-q", "-b", f"ticket/{ticket}-test", str(wt), "main")
    return origin, repo, wt


def _aufpasser_instanz(tmp_path: Path) -> aufpasser.Aufpasser:
    return aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "zustand"))


# --- (a) Aufpasser prüft vor dem Push gegen gesperrte_pfade --------------------------


@pytest.mark.parametrize(
    "pfad",
    [".CLAUDE-HANDOFF.md", "docs/Entscheidungen.md", "unter/.CLAUDE-HANDOFF-LOOP.md"],
)
def test_a_sichern_worktree_verweigert_ungetrackte_gesperrte_datei(
    tmp_path: Path, pfad: str
) -> None:
    origin, repo, wt = _repo_mit_worktree(tmp_path, 42)
    (wt / "arbeit.txt").write_text("echte arbeit\n", encoding="utf-8")
    gesperrt = wt / pfad
    gesperrt.parent.mkdir(parents=True, exist_ok=True)
    gesperrt.write_text("vertrauliche Übergabe\n", encoding="utf-8")

    a = _aufpasser_instanz(tmp_path)
    with pytest.raises(RuntimeError, match="verweigert"):
        a.sichern_worktree(repo, 42, wt)

    assert "sicherung/42" not in _entfernte_zweige(origin)
    # Gegenprobe: ohne die gesperrte Datei sichert der Aufpasser normal.
    gesperrt.unlink()
    sha = a.sichern_worktree(repo, 42, wt)
    assert sha is not None
    assert "sicherung/42" in _entfernte_zweige(origin)
    assert _git(origin, "show", "sicherung/42:arbeit.txt") == "echte arbeit"


def test_a_sichern_und_fortsetzen_verweigert_ungetrackte_gesperrte_datei(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    origin, repo, wt = _repo_mit_worktree(tmp_path, 43)
    (wt / "arbeit.txt").write_text("echte arbeit\n", encoding="utf-8")
    (wt / ".CLAUDE-HANDOFF.md").write_text("rohe Übergabe\n", encoding="utf-8")

    a = _aufpasser_instanz(tmp_path)
    gemeldet: list[tuple[str, str]] = []
    fortgesetzt: list[str] = []

    def _merken(
        e: dict,
        f: aufpasser.Fenster,
        r: Path,
        schluessel: str,
        ereignis: str,
        text: str,
    ) -> None:
        gemeldet.append((ereignis, text))

    a._braucht_david = _merken  # type: ignore[method-assign]
    a.vor_eingriff = lambda *_a, **_k: (fortgesetzt.append("vor_eingriff") or True)  # type: ignore[method-assign]
    f = aufpasser.Fenster("spec-99", "99", "1", "bau 43", str(wt), 0, "@1", "%1")
    info = aufpasser.SessionInfo("11111111-1111-1111-1111-111111111111", wt, "idle", 0)
    eintrag: dict[str, object] = {"hash": "h", "seit": 0.0, "stufe": 1}

    with caplog.at_level(logging.ERROR, logger=aufpasser.log.name):
        a.sichern_und_fortsetzen(f, 43, repo, "spec-99/bau 43", eintrag, "h", info)

    assert "sicherung/43" not in _entfernte_zweige(origin)
    assert fortgesetzt == []
    assert any(art == "sicherung_fehlgeschlagen" for art, _ in gemeldet), gemeldet
    assert any(".CLAUDE-HANDOFF.md" in r.getMessage() for r in caplog.records)


def test_a_sichern_verweigert_gesperrte_datei_im_ungepushten_commit(tmp_path: Path) -> None:
    origin, _repo, wt = _repo_mit_worktree(tmp_path, 44)
    (wt / ".CLAUDE-HANDOFF.md").write_text("roh\n", encoding="utf-8")
    _git(wt, "add", "-f", ".CLAUDE-HANDOFF.md")
    _git(wt, "commit", "-q", "-m", "mit Übergabe")
    (wt / "arbeit.txt").write_text("mehr\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="verweigert"):
        aufpasser.sichern(wt, 44)

    assert "sicherung/44" not in _entfernte_zweige(origin)


# --- (b) Geheimnis-Prüfung im Hausmeister per -z -------------------------------------


@pytest.mark.parametrize("rel", ["ordner mit leer/.env", "Ümlaut/.env"])
def test_b_hausmeister_ordner_geheimnis_mit_leerzeichen_und_umlaut(
    welt: Welt, rel: str
) -> None:
    wt = welt.worktree(51)
    ziel = wt / rel
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text("GEHEIM=1\n", encoding="utf-8")
    git(wt, "add", "-f", rel)
    git(wt, "commit", "-q", "-m", "geheim")
    welt.ticket(51, "CLOSED", 48)

    erg = welt.aufraeumen(wt, loeschen=False)

    assert erg.art == "bleibt", erg
    assert f"Geheimnis-Datei {rel} in ungepushtem Commit" == erg.grund, erg.grund


@pytest.mark.parametrize("rel", ["ordner mit leer/.env", "Ümlaut/.env"])
def test_b_hausmeister_zweig_geheimnis_mit_leerzeichen_und_umlaut(
    zw: Welt, rel: str
) -> None:
    wt = zw.heim / "wt" / "wt-52"
    git(zw.haupt, "worktree", "add", "-q", "-b", "ticket/52-geheim", str(wt))
    ziel = wt / rel
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text("GEHEIM=1\n", encoding="utf-8")
    git(wt, "add", "-f", rel)
    git(wt, "commit", "-q", "-m", "geheim")
    zw.ticket(52, "CLOSED", 48)

    # Worktree entfernen, Zweig bleibt lokal mit ungesichertem Geheimnis-Commit.
    git(zw.haupt, "worktree", "remove", "--force", str(wt))
    erg = hausmeister._zweig_entscheiden(
        zw.haupt, "ticket/52-geheim", None, trocken=True, ort="lokal", holen=False
    )

    assert erg.art == "bleibt", erg
    assert f"Geheimnis-Datei {rel} in ungesichertem Commit" == erg.grund, erg.grund


# --- (c) Vereinheitlichte NUL-Zerlegung ohne strip("\r\n") ---------------------------


def test_c_pfade_aus_nul_erhaelt_zeilenumbruch_am_ende() -> None:
    roh = ".env.lokal\n\x00ordner mit leer/.env\x00Ümlaut/.env\x00"
    assert us.pfade_aus_nul(roh) == [
        ".env.lokal\n",
        "ordner mit leer/.env",
        "Ümlaut/.env",
    ]


@NUR_POSIX
def test_c_dateiname_mit_zeilenumbruch_am_ende_im_echten_repo(tmp_path: Path) -> None:
    origin, _repo, wt = _repo_mit_worktree(tmp_path, 53)
    # (1) Geheimnis-Datei mit Zeilenumbruch am Ende wird mit vollem Namen erkannt.
    (wt / ".env.lokal\n").write_text("K=V\n", encoding="utf-8")
    # (2) Übergabe in einem Ordner mit Zeilenumbruch am Ende wird mit exaktem Pfad erkannt.
    (wt / "ordner\n").mkdir()
    (wt / "ordner\n" / ".claude-handoff.md").write_text("roh\n", encoding="utf-8")
    # (3) Datei mit Zeilenumbruch nach .md wird NICHT durch strip("\n") zu .claude-handoff.md verstümmelt.
    (wt / ".claude-handoff.md\n").write_text("kein echtes .md\n", encoding="utf-8")
    _git(wt, "add", "-f", ".env.lokal\n", "ordner\n/.claude-handoff.md", ".claude-handoff.md\n")
    _git(wt, "commit", "-q", "-m", "zeilenumbruch")

    geheim = hausmeister._geheimnis_pfade(wt, "HEAD", "--not", "--remotes=origin")
    assert geheim == [".env.lokal\n"], geheim

    gesperrt = us.gesperrte_pfade(wt, "HEAD", "--not", "--remotes=origin")
    assert gesperrt == ["ordner\n/.claude-handoff.md"], gesperrt
    assert _entfernte_zweige(origin) == ["main"]
