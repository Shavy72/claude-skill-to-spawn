"""#1505: Bau-Ordner schlank über ``scripts/arbeitsordner.py`` des Haupt-Repos.

Echte Wegwerf-Repos (bare ``origin`` + Klon). Das Modul liegt als Stub auf
``origin/master``; ``worktree_anlegen`` lädt es von dort (nicht aus dem Arbeitsbaum).
Dazu: die Aufpasser-Sicherung (``git add -A``) im Sparse-Ordner.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from to_spawn import aufpasser, nest

STUB = """
import json
import subprocess
from pathlib import Path


def arbeitsordner_anlegen(repo, ziel, start, *, zweig=None, ticket=None, git=None):
    add = ["git", "worktree", "add"] + (["-b", zweig] if zweig else []) + [str(ziel), start]
    subprocess.run(add, cwd=repo, check=True, capture_output=True, text=True)
    marke = {"start": start, "zweig": zweig, "ticket": ticket}
    (Path(ziel) / ".marke.json").write_text(json.dumps(marke), encoding="utf-8")
"""

STUB_FEHLER = """
import subprocess


def arbeitsordner_anlegen(repo, ziel, start, *, zweig=None, ticket=None, git=None):
    subprocess.run(["git", "worktree", "add", "--no-checkout", "-q", str(ziel), start], cwd=repo, check=True)
    raise subprocess.CalledProcessError(128, ["git", "sparse-checkout"], stderr="stub-kaputt: sparse")
"""


def _git(ordner: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ordner), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _haupt(tmp_path: Path, modul: str | None) -> Path:
    """Bare ``origin`` (Hauptzweig master) + Klon ``haupt``; ``modul`` liegt committet unter scripts/."""
    saat = tmp_path / "saat"
    saat.mkdir()
    _git(saat, "init", "-q", "-b", "master")
    (saat / "README.md").write_text("x\n", encoding="utf-8")
    if modul is not None:
        (saat / "scripts").mkdir()
        (saat / "scripts" / "arbeitsordner.py").write_text(modul, encoding="utf-8")
    _git(saat, "add", "-A")
    _git(saat, "commit", "-qm", "start")
    subprocess.run(["git", "clone", "-q", "--bare", str(saat), str(tmp_path / "origin.git")], check=True)
    subprocess.run(["git", "clone", "-q", str(tmp_path / "origin.git"), str(tmp_path / "haupt")], check=True)
    return tmp_path / "haupt"


def _marke(worktree: Path) -> dict:
    return json.loads((worktree / ".marke.json").read_text(encoding="utf-8"))


def test_modul_wird_genutzt_neuer_zweig(tmp_path: Path) -> None:
    haupt = _haupt(tmp_path, STUB)
    wt = tmp_path / "wt-1505"
    nest.worktree_anlegen(wt, haupt, "1505")
    assert _marke(wt) == {"start": "origin/master", "zweig": "ticket-1505", "ticket": "1505"}
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "ticket-1505"


def test_modul_vorhandener_zweig_ohne_zweig_parameter(tmp_path: Path) -> None:
    haupt = _haupt(tmp_path, STUB)
    _git(haupt, "branch", "ticket-1505", "origin/master")
    wt = tmp_path / "wt-1505"
    nest.worktree_anlegen(wt, haupt, "1505")
    assert _marke(wt) == {"start": "ticket-1505", "zweig": None, "ticket": "1505"}


def test_modul_kommt_von_origin_nicht_aus_veraltetem_arbeitsbaum(tmp_path: Path) -> None:
    """Arbeitsbaum des Haupt-Repos kaputt/veraltet → trotzdem gilt der Stand auf origin."""
    haupt = _haupt(tmp_path, STUB)
    (haupt / "scripts" / "arbeitsordner.py").write_text("raise SystemExit('alt')\n", encoding="utf-8")
    wt = tmp_path / "wt-1505"
    nest.worktree_anlegen(wt, haupt, "1505")
    assert _marke(wt)["zweig"] == "ticket-1505"


def test_ohne_modul_altes_worktree_add(tmp_path: Path) -> None:
    haupt = _haupt(tmp_path, None)
    wt = tmp_path / "wt-1505"
    nest.worktree_anlegen(wt, haupt, "1505")
    assert not (wt / ".marke.json").exists()
    assert (wt / "README.md").is_file()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "ticket-1505"


def test_modul_nur_im_arbeitsbaum_altes_verhalten(tmp_path: Path) -> None:
    """Liegt die Datei nur ungeprüft im Arbeitsbaum (nicht auf origin) → altes ``worktree add``."""
    haupt = _haupt(tmp_path, None)
    (haupt / "scripts").mkdir()
    (haupt / "scripts" / "arbeitsordner.py").write_text(STUB, encoding="utf-8")
    wt = tmp_path / "wt-1505"
    nest.worktree_anlegen(wt, haupt, "1505")
    assert not (wt / ".marke.json").exists()
    assert (wt / "README.md").is_file()


def test_modul_nicht_ladbar_altes_verhalten(tmp_path: Path) -> None:
    haupt = _haupt(tmp_path, "def kaputt(:\n")
    wt = tmp_path / "wt-1505"
    nest.worktree_anlegen(wt, haupt, "1505")
    assert (wt / "README.md").is_file()
    assert not (wt / ".marke.json").exists()


def _voll_angelegt(haupt: Path, wt: Path) -> None:
    """Rückfall griff: voller Checkout, Zweig ``ticket-1505``, genau ein Worktree-Eintrag."""
    assert (wt / "README.md").is_file()
    assert not (wt / ".marke.json").exists()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "ticket-1505"
    sparse = subprocess.run(
        ["git", "-C", str(wt), "config", "--bool", "core.sparseCheckout"], capture_output=True, text=True, check=False
    )
    assert sparse.stdout.strip() != "true"
    assert _git(haupt, "worktree", "list", "--porcelain").count(f"worktree {wt}") == 1


def test_fehler_im_modul_rueckfall_auf_worktree_add(tmp_path: Path) -> None:
    """Fix-Runde 1: Modul wirft beim Aufruf → halber Ordner weg, dann volles ``worktree add``."""
    haupt = _haupt(tmp_path, STUB_FEHLER)
    wt = tmp_path / "wt-1505"
    nest.worktree_anlegen(wt, haupt, "1505")
    _voll_angelegt(haupt, wt)


def test_fehler_nach_zweig_anlage_rueckfall_nutzt_vorhandenen_zweig(tmp_path: Path) -> None:
    """Modul legte ``-b ticket-1505`` schon an und warf dann → Rückfall checkt den Zweig aus."""
    haupt = _haupt(tmp_path, STUB_ZWEIG_FEHLER)
    wt = tmp_path / "wt-1505"
    nest.worktree_anlegen(wt, haupt, "1505")
    _voll_angelegt(haupt, wt)


def test_fehler_im_modul_und_im_rueckfall_nestfehler(tmp_path: Path) -> None:
    haupt = _haupt(tmp_path, STUB_FEHLER)
    _git(haupt, "remote", "set-url", "origin", str(tmp_path / "fehlt.git"))
    _git(haupt, "update-ref", "-d", "refs/remotes/origin/master")
    wt = tmp_path / "wt-1505"
    with pytest.raises(nest.NestFehler):
        nest.worktree_anlegen(wt, haupt, "1505")
    assert not wt.exists()


def test_sicherung_im_sparse_ordner_mit_neuer_datei_ausserhalb_der_regel(tmp_path: Path) -> None:
    """``git add -A`` scheitert sonst an ``b/neu.txt`` außerhalb der Sparse-Regel (Punkt 7)."""
    saat = tmp_path / "saat"
    saat.mkdir()
    _git(saat, "init", "-q", "-b", "master")
    for name in ("a/x.txt", "b/alt.txt", "b/Größe ä.txt"):
        (saat / name).parent.mkdir(exist_ok=True)
        (saat / name).write_text(name, encoding="utf-8")
    _git(saat, "add", "-A")
    _git(saat, "commit", "-qm", "start")
    subprocess.run(["git", "clone", "-q", "--bare", str(saat), str(tmp_path / "origin.git")], check=True)
    subprocess.run(["git", "clone", "-q", str(tmp_path / "origin.git"), str(tmp_path / "haupt")], check=True)
    wt = tmp_path / "wt-9505"
    _git(tmp_path / "haupt", "worktree", "add", "-q", "--no-checkout", "-b", "ticket-9505", str(wt), "origin/master")
    _git(wt, "sparse-checkout", "set", "--no-cone", "/*", "!/b/")
    _git(wt, "checkout", "-q")
    assert not (wt / "b").exists()
    (wt / "b").mkdir()
    (wt / "b" / "neu.txt").write_text("neu", encoding="utf-8")
    (wt / "a" / "neu.txt").write_text("neu", encoding="utf-8")
    assert aufpasser.sichern(wt, 9505)
    # Neue Dateien drin, nicht ausgecheckte Alt-Datei bleibt (kein Lösch-Eintrag in der Sicherung).
    dateien = _git(
        tmp_path / "origin.git", "-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", "-z", "refs/heads/sicherung/9505"
    ).split("\0")
    assert sorted(d for d in dateien if d) == ["a/neu.txt", "a/x.txt", "b/Größe ä.txt", "b/alt.txt", "b/neu.txt"]


STUB_ZWEIG_FEHLER = """
import subprocess


def arbeitsordner_anlegen(repo, ziel, start, *, zweig=None, ticket=None, git=None):
    add = ["git", "worktree", "add", "--no-checkout", "-q", "-b", zweig, str(ziel), start]
    subprocess.run(add, cwd=repo, check=True)
    raise subprocess.CalledProcessError(128, ["git", "checkout"], stderr="stub-zweig-kaputt")
"""

STUB_WIRFT = '''
import subprocess


def arbeitsordner_anlegen(repo, ziel, start, *, zweig=None, ticket=None, git=None):
    subprocess.run(["git", "worktree", "add", "--no-checkout", "-q", str(ziel), start], cwd=repo, check=True)
    raise {ausnahme}("stub-wirft")
'''


@pytest.mark.parametrize("ausnahme", ["OSError", "FileNotFoundError", "RuntimeError", "KeyError"])
def test_jede_ausnahme_im_modul_rueckfall_ohne_halben_ordner(tmp_path: Path, ausnahme: str) -> None:
    """Review #1505: jede Ausnahme → halber Ordner weg, Rückfall auf volles ``worktree add``."""
    haupt = _haupt(tmp_path, STUB_WIRFT.format(ausnahme=ausnahme))
    wt = tmp_path / "wt-1505"
    nest.worktree_anlegen(wt, haupt, "1505")
    _voll_angelegt(haupt, wt)
