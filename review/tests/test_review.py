"""Pflicht-Fälle aus dem Handoff gegen echte Git-Repos."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from review_hilfen import git, lauf, route


def namen(r: dict) -> list[str]:
    return [p["name"] for p in r["pruefer"]]


def signal_ids(r: dict) -> list[str]:
    return [s["id"] for s in r["signale"]]


def test_nur_readme_ist_keine(repo: Path) -> None:
    (repo / "README.md").write_text("# Test\nmehr Text\n", encoding="utf-8")
    r = route(repo, "--moment", "commit")
    assert r["stufe"] == "keine"
    assert r["pruefer"] == [] and r["builtin"] == []


def test_kleine_python_aenderung(repo: Path, umgebung: Callable[[str, str], Path]) -> None:
    umgebung("hinweis", "voll")
    neu = "".join(f"def f{i}():\n    return {i}\n" for i in range(5))
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write(neu)
    r = route(repo, "--moment", "commit")
    assert r["diff_zeilen"] == 10
    assert r["stufe"] == "klein"
    assert namen(r) == ["python-reviewer"]
    assert r["richter"] is False


def test_sicherheit_ist_gross(repo: Path, umgebung: Callable[[str, str], Path]) -> None:
    umgebung("hinweis", "voll")
    (repo / "auth").mkdir()
    (repo / "auth" / "login.py").write_text('token = request.headers["Bearer"]\n', encoding="utf-8")
    git(repo, "add", "-A")
    r = route(repo, "--moment", "commit")
    assert r["stufe"] == "gross"
    assert "security-reviewer" in namen(r)
    assert {"befehl": "/security-review"} in r["builtin"]
    assert r["richter"] is True
    spec = next(p for p in r["pruefer"] if p["name"] == "general-purpose")
    assert spec["auftraege"] == ["basis", "spec"]
    assert all(p["auftraege"][0] == "basis" for p in r["pruefer"])


def test_except_pass_ist_fehlerpfad(repo: Path, umgebung: Callable[[str, str], Path]) -> None:
    umgebung("hinweis", "voll")
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("try:\n    eins()\nexcept: pass\n")
    r = route(repo, "--moment", "commit")
    assert "fehlerpfad" in signal_ids(r)
    assert "silent-failure-hunter" in namen(r)


def test_wortgrenze_rolle_trifft_nicht_kontrolle(repo: Path) -> None:
    (repo / "rechnen.py").write_text("def kontrolle():\n    return kontrolle_wert()\n", encoding="utf-8")
    git(repo, "add", "-A")
    r = route(repo, "--moment", "commit")
    assert "sicherheit" not in signal_ids(r)


def test_wortgrenze_rolle_trifft_bei_auth_rolle(repo: Path) -> None:
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("auth_rolle = pruefe_rolle(nutzer)\n")
    r = route(repo, "--moment", "commit")
    assert "sicherheit" in signal_ids(r)


def test_wortgrenze_env_trifft_nicht_os_environ(repo: Path) -> None:
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("wert = os.environ.get('X')\n")
    r = route(repo, "--moment", "commit")
    assert "sicherheit" not in signal_ids(r)


def test_wortgrenze_env_trifft_bei_string_literal(repo: Path) -> None:
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("pfad = '.env'\n")
    r = route(repo, "--moment", "commit")
    assert "sicherheit" in signal_ids(r)


def test_wortgrenze_boot_trifft_nicht_bootstrap(repo: Path) -> None:
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("bootstrap_system()\n")
    r = route(repo, "--moment", "commit")
    assert "kosten" not in signal_ids(r)


def test_wortgrenze_boot_trifft_bei_boot(repo: Path) -> None:
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("boot(handy)\n")
    r = route(repo, "--moment", "commit")
    assert "kosten" in signal_ids(r)


def test_wortgrenze_token_trifft_nicht_tokenizer(repo: Path) -> None:
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("tokenizer = Tokenizer()\n")
    r = route(repo, "--moment", "commit")
    assert "sicherheit" not in signal_ids(r)


def test_wortgrenze_token_trifft_bei_auth_token(repo: Path) -> None:
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("auth_token = erzeuge_token()\n")
    r = route(repo, "--moment", "commit")
    assert "sicherheit" in signal_ids(r)


def test_css_ist_oberflaeche(repo: Path) -> None:
    ziel = repo / "web" / "static"
    ziel.mkdir(parents=True)
    (ziel / "app.css").write_text("body { color: black; }\n", encoding="utf-8")
    git(repo, "add", "-A")
    r = route(repo, "--moment", "ui")
    assert "oberflaeche" in signal_ids(r)


def _beleg_schreiben(repo: Path, r: dict, befunde: list[dict]) -> Path:
    entwurf = {k: r[k] for k in ("moment", "stufe", "sha", "fingerprint", "dateien")}
    entwurf["befunde"] = befunde
    entwurf["richter"] = None
    datei = repo.parent / "entwurf.json"
    datei.write_text(json.dumps(entwurf), encoding="utf-8")
    ergebnis = lauf("review_beleg.py", "schreiben", "--datei", str(datei), "--repo", str(repo))
    assert ergebnis.returncode == 0, ergebnis.stderr
    return Path(ergebnis.stdout.splitlines()[0])


def test_beleg_rot_blockiert_nur_scharf(repo: Path, umgebung: Callable[[str], Path]) -> None:
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("def zwei():\n    return 2\n")
    r = route(repo, "--moment", "commit")
    befund = {"datei": "rechnen.py", "zeile": 3, "schwere": "hoch", "konfidenz": 90,
              "pruefer": "python-reviewer+silent-failure-hunter", "text": "Test",
              "grund": "belegt", "status": "behalten"}  # fmt: skip
    beleg = _beleg_schreiben(repo, r, [befund])
    gespeichert = json.loads(beleg.read_text(encoding="utf-8"))
    assert gespeichert["status"] == "rot" and gespeichert["befunde"][0]["grund"] == "belegt"
    kurz = lauf("review_beleg.py", "kurz", "--datei", str(beleg))
    assert kurz.returncode == 0 and len(kurz.stdout.splitlines()) <= 15
    # .review/.gitignore hält Belege aus git status heraus
    assert (repo / ".review" / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert ".review" not in git(repo, "status", "--porcelain", "--untracked-files=all")

    pruef = ["pruefen", "--moment", "commit", "--repo", str(repo)]
    umgebung("scharf")
    scharf = lauf("review_beleg.py", *pruef)
    assert scharf.returncode == 1 and "Beleg rot" in scharf.stderr
    umgebung("hinweis")
    assert lauf("review_beleg.py", *pruef).returncode == 0
    # Fix nach rotem Beleg → nächste Fix-Runde
    assert route(repo, "--moment", "commit")["fix_runde"] == 0
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("def drei():\n    return 3\n")
    assert route(repo, "--moment", "commit")["fix_runde"] == 1


def test_alter_fingerprint_ungueltig(repo: Path) -> None:
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("def zwei():\n    return 2\n")
    _beleg_schreiben(repo, route(repo, "--moment", "commit"), [])
    assert route(repo, "--moment", "commit")["beleg_gueltig"] is True
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("def drei():\n    return 3\n")
    assert route(repo, "--moment", "commit")["beleg_gueltig"] is False


def test_bericht_soll_ist(repo: Path, tmp_path: Path) -> None:
    log = tmp_path / "ausloeser.jsonl"
    zeilen = [
        {"zeit": "2099-01-01T00:00:00+00:00", "repo": repo.as_posix(), "moment": "commit",
         "stufe": "klein", "fingerprint": f"fp{i}"}
        for i in range(3)
    ]  # fmt: skip
    log.write_text("".join(json.dumps(z) + "\n" for z in zeilen), encoding="utf-8")
    (repo / ".review").mkdir()
    for i in range(2):
        beleg = {"moment": "commit", "status": "gruen", "zeit": "2099-01-01T00:00:00+00:00",
                 "pruefer": [{"name": "x", "befunde_roh": 2}], "befunde": [], "token_schaetzung": 10}  # fmt: skip
        (repo / ".review" / f"beleg-abc{i}-commit.json").write_text(json.dumps(beleg), encoding="utf-8")
    ergebnis = lauf("review_bericht.py")
    assert ergebnis.returncode == 0, ergebnis.stderr
    zeile = next(z for z in ergebnis.stdout.splitlines() if z.startswith("| commit "))
    assert zeile.startswith("| commit | 3 | 2 |")
    assert "Hook oder Dirigent defekt?" in zeile


def test_kein_git_repo(tmp_path: Path, umgebung: Callable[[str], Path]) -> None:
    leer = tmp_path / "leer"
    leer.mkdir()
    ergebnis = lauf("review_route.py", "--moment", "manuell", "--repo", str(leer))
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert json.loads(ergebnis.stdout)["stufe"] == "keine"


def test_kurz_eine_zeile(repo: Path, umgebung: Callable[[str, str], Path]) -> None:
    umgebung("hinweis", "voll")
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("def zwei():\n    return 2\n")
    ergebnis = lauf("review_route.py", "--moment", "commit", "--repo", str(repo), "--kurz")
    assert ergebnis.returncode == 0
    zeilen = ergebnis.stdout.strip().splitlines()
    assert len(zeilen) == 1 and zeilen[0].startswith("Review klein: python-reviewer")


def test_moment_sitzung_mit_files_und_untracked(repo: Path) -> None:
    (repo / "neu.py").write_text("def neu():\n    return 0\n", encoding="utf-8")
    r = route(repo, "--moment", "sitzung", "--files", "neu.py")
    assert r["moment"] == "sitzung"
    assert r["dateien"] == ["neu.py"] and r["diff_zeilen"] == 2
    assert lauf("review_route.py", "--moment", "quatsch", "--repo", str(repo)).returncode == 2
