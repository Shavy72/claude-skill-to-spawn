# ruff: noqa: F811  (Fixtures aus test_hooks importiert)
"""Runde 5 (Handoff Runde 4, Punkt 0): Beleg hängt an HEAD-SHA. Bau-Session prüft, schreibt
Beleg, committet → HEAD neu → Stop-Hook fand keinen Beleg und forderte erneut ein volles
Review (doppelte Kosten je Commit). Beleg-Suche muss über den Inhalt (datei_hashes des Diffs
gegen dieselbe Basis) laufen, unabhängig von HEAD. Echte Skripte, echtes Git, kein Ersatz."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_hooks import REVIEW, Umgebung, git, py_aendern, repo, u  # noqa: F401, E402


def _beleg_echt(u: Umgebung, repo: Path, befunde: list[dict] | None = None) -> Path:
    """Wie der Dirigent: Route holen, Entwurf bauen, `review_beleg.py schreiben`."""
    r = u.route(repo, "--moment", "sitzung", "--files", "rechnen.py")
    entwurf = {k: r[k] for k in ("sha", "fingerprint", "stufe", "dateien", "moment")}
    entwurf["befunde"] = befunde or []
    pfad = u.tmp / "entwurf.json"
    pfad.write_text(json.dumps(entwurf), encoding="utf-8")
    aus = subprocess.run(
        [sys.executable, str(REVIEW / "review_beleg.py"), "schreiben", "--datei", str(pfad), "--repo", str(repo)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=u.env,
        check=True,
    )
    beleg = Path(aus.stdout.splitlines()[0])
    inhalt = json.loads(beleg.read_text(encoding="utf-8"))
    assert inhalt["datei_hashes"] and inhalt["inhalt_hashes"]
    return beleg


def _gueltig_gegen_start(u: Umgebung, repo: Path, start: str, datei: str) -> bool:
    """Gegenprobe zu ``u.stop(...) is None``: stop.py schweigt auch bei Absturz (fail-open)."""
    return u.route(repo, "--moment", "sitzung", "--base", start, "--files", datei)["beleg_gueltig"] is True


def test_beleg_ueberlebt_commit_des_geprueften_codes(u: Umgebung, repo: Path) -> None:
    start = git(repo, "rev-parse", "HEAD").strip()
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    _beleg_echt(u, repo)
    assert u.stop(repo) is None  # vor Commit: gültig

    git(repo, "commit", "-q", "-am", "bau")

    assert u.stop(repo) is None  # nach Commit: derselbe Inhalt → kein zweites Review
    assert _gueltig_gegen_start(u, repo, start, "rechnen.py")


def test_nach_commit_weitere_aenderung_fordert_review(u: Umgebung, repo: Path) -> None:
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    _beleg_echt(u, repo)
    git(repo, "commit", "-q", "-am", "bau")
    (repo / "rechnen.py").write_text("def eins():\n    return 1\n\n\ndef zwei():\n    return 3\n", encoding="utf-8")

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"


def test_route_nennt_gefundenen_beleg_nach_commit(u: Umgebung, repo: Path) -> None:
    start = git(repo, "rev-parse", "HEAD").strip()
    py_aendern(repo)
    beleg = _beleg_echt(u, repo)
    git(repo, "commit", "-q", "-am", "bau")

    r = u.route(repo, "--moment", "sitzung", "--base", start, "--files", "rechnen.py")

    assert r["beleg_gueltig"] is True
    assert Path(r["beleg"]).resolve() == beleg.resolve()


def test_pruefen_cli_findet_beleg_nach_commit(u: Umgebung, repo: Path) -> None:
    u.modus("scharf")
    start = git(repo, "rev-parse", "HEAD").strip()
    py_aendern(repo)
    _beleg_echt(u, repo)
    git(repo, "commit", "-q", "-am", "bau")

    aus = subprocess.run(
        [
            sys.executable,
            str(REVIEW / "review_beleg.py"),
            "pruefen",
            "--moment",
            "sitzung",
            "--repo",
            str(repo),
            "--base",
            start,
            "--files",
            "rechnen.py",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=u.env,
    )

    assert aus.returncode == 0, aus.stderr


def test_rot_beleg_nach_commit_bleibt_rot(u: Umgebung, repo: Path) -> None:
    u.modus("scharf")
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    rot = [
        {
            "titel": "x",
            "schwere": "hoch",
            "konfidenz": 95,
            "status": "behalten",
            "datei": "rechnen.py",
            "zeile": 1,
            "pruefer": "spec",
            "begruendung": "b",
        }
    ]
    _beleg_echt(u, repo, rot)
    git(repo, "commit", "-q", "-am", "bau")

    aus = u.stop(repo, aktiv=True)

    assert aus is not None and aus["decision"] == "block"
    assert "Beleg rot" in aus["reason"]


def test_neue_datei_beleg_ueberlebt_commit(u: Umgebung, repo: Path) -> None:
    """Bau-Fall: Datei ist beim Review noch untracked, danach add+commit → Diff-Format
    wechselt (untracked → new file), Inhalt gleich → Beleg muss weiter gelten."""
    start = git(repo, "rev-parse", "HEAD").strip()
    (repo / "neu.py").write_text("def drei():\n    return 3\n", encoding="utf-8")
    u.merke(repo, "neu.py")
    r = u.route(repo, "--moment", "sitzung", "--files", "neu.py")
    entwurf = {k: r[k] for k in ("sha", "fingerprint", "stufe", "dateien", "moment")}
    pfad = u.tmp / "entwurf.json"
    pfad.write_text(json.dumps(entwurf), encoding="utf-8")
    subprocess.run(
        [sys.executable, str(REVIEW / "review_beleg.py"), "schreiben", "--datei", str(pfad), "--repo", str(repo)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=u.env,
        check=True,
    )
    assert u.stop(repo) is None

    git(repo, "add", "neu.py")
    git(repo, "commit", "-q", "-m", "neu")

    assert u.stop(repo) is None
    assert _gueltig_gegen_start(u, repo, start, "neu.py")


def test_fremder_commit_im_selben_ordner_zaehlt_nicht(u: Umgebung, repo: Path) -> None:
    """Beleg 2026-09-24: parallele Session committet leitstand.py in denselben Ordner →
    stop.py forderte Review für fremden Code (mtime nach Sitzungsstart)."""
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    (repo / "fremd.py").write_text("def fremd():\n    return 9\n", encoding="utf-8")
    git(repo, "add", "fremd.py")
    git(repo, "commit", "-q", "-m", "fremde session", "--", "fremd.py")

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"
    assert "fremd.py" not in aus["reason"]
    assert "rechnen.py" in aus["reason"]


def test_mensch_noetig_nennt_grund_review_offen(u: Umgebung, repo: Path) -> None:
    log = u.tmp / "mensch_noetig.jsonl"
    u.env["REVIEW_MENSCH_NOETIG"] = str(log)
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    assert u.stop(repo)["decision"] == "block"
    u.stop(repo)
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[0])["grund"] == "review_offen"
