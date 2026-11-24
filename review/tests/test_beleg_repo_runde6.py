"""Runde 6: Beleg landet im Repo des Entwurfs (Rest 1) und parallele Sessions überschreiben
sich den Beleg nicht mehr (Rest 2, alter Name beleg-<sha>-<moment>.json kollidierte)."""

from __future__ import annotations

import json
from pathlib import Path

from review_hilfen import git, lauf, route

ROT = [
    {
        "datei": "rechnen.py",
        "zeile": 3,
        "schwere": "hoch",
        "konfidenz": 95,
        "pruefer": "python-reviewer",
        "text": "falsch",
        "status": "behalten",
    }
]


def _entwurf(ablage: Path, r: dict, name: str, befunde: list | None = None, mit_repo: bool = True) -> Path:
    felder = ("moment", "stufe", "sha", "fingerprint", "dateien", "diff_modus")
    entwurf = {k: r[k] for k in felder}
    if mit_repo:
        entwurf["repo"] = r["repo"]
    entwurf["befunde"] = befunde or []
    entwurf["richter"] = None
    datei = ablage / f"{name}.json"
    datei.write_text(json.dumps(entwurf), encoding="utf-8")
    return datei


def _fremdes_repo(tmp_path: Path) -> Path:
    pfad = tmp_path / "anderes"
    pfad.mkdir()
    git(pfad, "init", "-q")
    git(pfad, "config", "user.name", "Andere")
    git(pfad, "config", "user.email", "andere@example.invalid")
    (pfad / "notiz.txt").write_text("fremd\n", encoding="utf-8")
    git(pfad, "add", "-A")
    git(pfad, "commit", "-q", "-m", "fremd")
    return pfad


def _aendern(repo: Path) -> None:
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("def zwei():\n    return 2\n")


# ---------------------------------------------------------------- Rest 1: richtiges Repo


def test_schreiben_nimmt_repo_aus_entwurf_nicht_cwd(repo: Path, tmp_path: Path) -> None:
    anderes = _fremdes_repo(tmp_path)
    _aendern(repo)
    r = route(repo, "--moment", "sitzung", "--files", "rechnen.py")
    entwurf = _entwurf(tmp_path, r, "entwurf")

    ergebnis = lauf("review_beleg.py", "schreiben", "--datei", str(entwurf), cwd=anderes)

    assert ergebnis.returncode == 0, ergebnis.stderr
    ziel = Path(ergebnis.stdout.splitlines()[0])
    assert ziel.parent == repo.resolve() / ".review"
    assert not (anderes / ".review").exists()
    assert route(repo, "--moment", "sitzung", "--files", "rechnen.py")["beleg_gueltig"] is True


def test_schreiben_ohne_repo_mit_fremder_sha_bricht_ab(repo: Path, tmp_path: Path) -> None:
    """Alter Entwurf ohne repo-Feld aus falschem cwd: sha gibt es dort nicht → Exit 2, kein Beleg."""
    anderes = _fremdes_repo(tmp_path)
    _aendern(repo)
    r = route(repo, "--moment", "sitzung", "--files", "rechnen.py")
    entwurf = _entwurf(tmp_path, r, "entwurf", mit_repo=False)

    ergebnis = lauf("review_beleg.py", "schreiben", "--datei", str(entwurf), cwd=anderes)

    assert ergebnis.returncode == 2
    assert "gehört nicht" in ergebnis.stderr
    assert not (anderes / ".review").exists()


# ---------------------------------------------------------------- Rest 2: keine Kollision


def test_parallele_sessions_ueberschreiben_beleg_nicht(repo: Path, tmp_path: Path) -> None:
    _aendern(repo)
    (repo / "andere.py").write_text("def drei():\n    return 3\n", encoding="utf-8")
    r1 = route(repo, "--moment", "sitzung", "--files", "rechnen.py")
    r2 = route(repo, "--moment", "sitzung", "--files", "andere.py")
    assert r1["sha"] == r2["sha"] and r1["fingerprint"] != r2["fingerprint"]

    for i, r in enumerate((r1, r2)):
        aus = lauf("review_beleg.py", "schreiben", "--datei", str(_entwurf(tmp_path, r, f"e{i}")), cwd=tmp_path)
        assert aus.returncode == 0, aus.stderr

    assert len(list((repo / ".review").glob("beleg-*-sitzung*.json"))) == 2
    assert route(repo, "--moment", "sitzung", "--files", "rechnen.py")["beleg_gueltig"] is True
    assert route(repo, "--moment", "sitzung", "--files", "andere.py")["beleg_gueltig"] is True


def test_fremder_roter_beleg_erhoeht_fix_runde_nicht(repo: Path, tmp_path: Path) -> None:
    """Rot-Beleg einer parallelen Session für andere Dateien zählt nicht als eigene Fix-Runde."""
    _aendern(repo)
    (repo / "andere.py").write_text("def drei():\n    return 3\n", encoding="utf-8")
    fremd = route(repo, "--moment", "sitzung", "--files", "andere.py")
    rot = [{**ROT[0], "datei": "andere.py"}]
    aus = lauf("review_beleg.py", "schreiben", "--datei", str(_entwurf(tmp_path, fremd, "fremd", rot)), cwd=tmp_path)
    assert aus.returncode == 0, aus.stderr

    eigen = route(repo, "--moment", "sitzung", "--files", "rechnen.py")
    assert eigen["beleg_gueltig"] is False
    assert eigen["fix_runde"] == 0


def test_eigener_roter_beleg_erhoeht_fix_runde_weiter(repo: Path, tmp_path: Path) -> None:
    _aendern(repo)
    r = route(repo, "--moment", "sitzung", "--files", "rechnen.py")
    aus = lauf("review_beleg.py", "schreiben", "--datei", str(_entwurf(tmp_path, r, "rot", ROT)), cwd=tmp_path)
    assert aus.returncode == 0, aus.stderr

    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("# gefixt\n")
    nach_fix = route(repo, "--moment", "sitzung", "--files", "rechnen.py")
    assert nach_fix["beleg_gueltig"] is False
    assert nach_fix["fix_runde"] == 1
