"""L1 #112: ein Wegwerf-Eintrag in der Maschinen-Liste legt den Hausmeister nicht still.

1. Quelle: ``einrichten`` trägt nur Repos mit GitHub-origin ein; ein lokaler Pfad als
   origin ergibt keinen geratenen ``slug``.
2. Hausmeister: ein Eintrag ohne Ticket-Repo wird übersprungen und einmal gemeldet, die
   übrigen Einträge arbeiten weiter; Ordner des kaputten Eintrags bleiben.
3. Trockenlauf und echter Lauf zeigen dieselben Fehler.
4. ``_im_bereich`` prüft den aufgelösten Pfad auf versteckte Ordner (``[.]ssh``, Symlink).

Alles nur in temporären Ordnern mit temporärer Maschinen-Liste (``TO_SPAWN_MASCHINE``).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

import pytest
from test_hausmeister_1368 import Welt, git, welt  # noqa: F401 — ``welt`` ist ein Fixture
from test_hausmeister_l1_konfig import _home, _maschine, ohne_naehte  # noqa: F401 — Fixtures
from test_hausmeister_ticket_repo_176 import APP, SKILL, WEGWERF, Repos

from to_spawn import config, einrichten, gh, hausmeister, vorgaben

SKILL_ROOT = Path(__file__).resolve().parent.parent
PROBE_SLUG = "bau/duoplus-leit"


def _repo_mit_origin(ordner: Path, url: str) -> Path:
    subprocess.run(["git", "init", "-q", str(ordner)], check=True)
    subprocess.run(["git", "-C", str(ordner), "remote", "add", "origin", url], check=True)
    return ordner.resolve()


def _liste_mit_probe(probe_pfad: str) -> None:
    """Die Maschinen-Liste vom 21.11.: zwei Vorgaben + Probe-Klon-Eintrag (Issue #112)."""
    repos = [dict(e) for e in vorgaben.REPOS]
    repos.append(
        {
            "slug": PROBE_SLUG,
            "pfad": probe_pfad,
            "ticket_repo": "",
            "worktree_basis": "",
        }
    )
    _maschine({"repos": repos})


# --- 1 Quelle ------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["/home/bau/duoplus-leit", r"C:\Users\nutzer\org\fern.git"])
def test_github_slug_nur_aus_github_origin(tmp_path: Path, url: str) -> None:
    repo = _repo_mit_origin(tmp_path / "klon", url)
    assert gh.github_slug_aus_origin(repo) == ""
    gh_repo = _repo_mit_origin(tmp_path / "echt", "git@github.com:Ich/App.git")
    assert gh.github_slug_aus_origin(gh_repo) == "Ich/App"


def test_lokaler_origin_kommt_nicht_in_die_maschinen_liste(tmp_path: Path) -> None:
    klon = _repo_mit_origin(tmp_path / "probe-klon", "/home/bau/duoplus-leit")
    datei = config.maschine_datei()
    config.maschine_eintragen(klon)
    eintraege = json.loads(datei.read_text(encoding="utf-8"))["repos"] if datei.is_file() else []
    assert all(Path(e["pfad"]).expanduser().resolve() != klon for e in eintraege)
    assert all(e["slug"] != PROBE_SLUG for e in eintraege)
    befund, text = einrichten._pruefe_maschine(einrichten.Ort(klon, None))  # type: ignore[arg-type]
    assert befund == einrichten.LUECKE
    assert "GitHub" in text and "git remote set-url origin" in text


# --- 2 Hausmeister: kaputter Eintrag ---------------------------------------------------


def test_ticket_repo_je_ordner_mit_probe_eintrag(
    ohne_naehte: None,  # noqa: F811
    tmp_path: Path,
) -> None:
    _liste_mit_probe(str(tmp_path / "probe-klon"))
    assert hausmeister._ticket_repo() is None  # Vertrag Fix-Runde 1 (#66) bleibt
    assert hausmeister._ticket_repo_fuer("Shavy72/duoplus-management") == "Shavy72/duoplus-management"
    assert (
        hausmeister._ticket_repo_fuer("shavy72/claude-skill-to-spawn")
        == "Shavy72/claude-skill-to-spawn"  # #235: Skill-Repo hat eigene Issues
    )
    assert hausmeister._ticket_repo_fuer(PROBE_SLUG) is None  # eigener Eintrag kaputt ⇒ bleibt
    assert hausmeister._ticket_repo_fuer("") is None


def _probe_welt(welt: Welt) -> tuple[Path, Path]:  # noqa: F811
    """Worktree #11 (DuoPlus, zu seit 48 h) und Ordner #12 mit origin des Probe-Eintrags."""
    _liste_mit_probe(str(welt.tmp / "probe-klon"))
    wt = welt.worktree(11)
    welt.ticket(11, "CLOSED", 48)
    probe = welt.klon("wt-12")  # eigener Klon: Worktrees teilen die origin-Konfig
    git(probe, "remote", "set-url", "origin", f"https://github.com/{PROBE_SLUG}")
    welt.ticket(12, "CLOSED", 48)
    hausmeister._ABGELEHNT_GEMELDET.clear()
    return wt, probe


def test_probe_eintrag_legt_hausmeister_nicht_still(
    welt: Welt,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    wt, probe = _probe_welt(welt)
    with caplog.at_level(logging.WARNING, logger=hausmeister.log.name):
        gut = welt.aufraeumen(wt)
        schlecht = welt.aufraeumen(probe)
        welt.aufraeumen(probe)
    assert (gut.art, gut.fehler) == ("gelöscht", False), gut.grund
    assert schlecht.art == "bleibt" and schlecht.fehler, schlecht.grund
    meldungen = [r.getMessage() for r in caplog.records if PROBE_SLUG in r.getMessage()]
    assert len(meldungen) == 1, meldungen  # einmal, nicht je Ordner
    assert "ticket_repo" in meldungen[0] and str(config.maschine_datei()) in meldungen[0]


# --- 3 Trocken = echt ----------------------------------------------------------------------


def test_trocken_und_echt_melden_dieselben_fehler(welt: Welt) -> None:  # noqa: F811
    wt, probe = _probe_welt(welt)
    welt.ticket(11, "OPEN")  # echter Lauf löscht hier nichts
    trocken = [welt.aufraeumen(o) for o in (wt, probe)]
    echt = [welt.aufraeumen(o, loeschen=True) for o in (wt, probe)]
    assert [(e.art, e.grund, e.fehler) for e in trocken] == [(e.art, e.grund, e.fehler) for e in echt]
    assert sum(e.fehler for e in echt) == 1
    assert wt.is_dir() and probe.is_dir()


# --- 4 _im_bereich: aufgelöster Pfad ohne versteckte Ordner --------------------------------


def test_im_bereich_glob_klasse_fuer_punkt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    heim = _home(monkeypatch, tmp_path)
    ssh = heim / "x" / "wt-1" / ".ssh"
    ssh.mkdir(parents=True)
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", os.path.expanduser("~/x/wt-*/[.]ssh"))
    assert not hausmeister._im_bereich(ssh)


def test_im_bereich_symlink_auf_versteckten_ordner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    heim = _home(monkeypatch, tmp_path)
    (heim / "x" / ".ssh").mkdir(parents=True)
    link = heim / "x" / "wt-1"
    link.symlink_to(heim / "x" / ".ssh", target_is_directory=True)
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", os.path.expanduser("~/x/*"))
    assert not hausmeister._im_bereich(link)


# --- 5 #129: Zweig-Aufräumen mit Wegwerf-Eintrag, eine Slug-Regel, .review/ in .gitignore ---


def _zweig_auf_origin(haupt: Path, hauptzweig: str, zweig: str) -> None:
    """Legt einen gemergten Ticket-Zweig lokal und auf ``origin`` an, ohne ihn ausgecheckt zu lassen."""
    git(haupt, "checkout", "-q", "-b", zweig)
    datei = haupt / f"{zweig.replace('/', '_')}.txt"
    datei.write_text("fertig\n", encoding="utf-8")
    git(haupt, "add", datei.name)
    git(haupt, "commit", "-q", "-m", f"arbeit {zweig}")
    git(haupt, "push", "-q", "-u", "origin", zweig)
    git(haupt, "checkout", "-q", hauptzweig)
    git(haupt, "merge", "-q", "--ff-only", zweig)
    git(haupt, "push", "-q", "origin", hauptzweig)


def test_zweige_aufraeumen_mit_wegwerf_eintrag_verarbeitet_gesunde_repos(
    welt: Welt,  # noqa: F811
    ohne_naehte: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#129 (a): 2 gesunde Repos + 1 Wegwerf-Eintrag — gesunde Zweige werden geräumt, Wegwerf bleibt."""
    mehr = Repos(welt, monkeypatch)
    mehr.repo(SKILL)
    mehr.repo(WEGWERF)
    mehr.maschine([(APP, APP), (SKILL, SKILL), (WEGWERF, "")])
    _zweig_auf_origin(mehr.haupt[APP], mehr.hauptzweig[APP], "ticket/301-test")
    _zweig_auf_origin(mehr.haupt[SKILL], mehr.hauptzweig[SKILL], "ticket/302-test")
    _zweig_auf_origin(mehr.haupt[WEGWERF], mehr.hauptzweig[WEGWERF], "ticket/303-test")
    mehr.ticket(APP, 301, "CLOSED", 48)
    mehr.ticket(SKILL, 302, "CLOSED", 48)
    mehr.ticket(APP, 303, "CLOSED", 48)  # fremdes Ticket-Repo zählt für WEGWERF nicht
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN", "1")
    hausmeister._ABGELEHNT_GEMELDET.clear()

    with caplog.at_level(logging.WARNING, logger=hausmeister.log.name):
        erg = hausmeister.zweige_aufraeumen(loeschen=True, jetzt=welt.jetzt)

    assert {(e.ort, e.zweig, e.art) for e in erg} == {
        ("lokal", "ticket/301-test", "gelöscht"),
        ("remote", "ticket/301-test", "gelöscht"),
        ("lokal", "ticket/302-test", "gelöscht"),
        ("remote", "ticket/302-test", "gelöscht"),
        ("lokal", "ticket/303-test", "bleibt"),
        ("remote", "ticket/303-test", "bleibt"),
    }
    assert "ticket/301-test" not in git(mehr.haupt[APP], "branch", "--format=%(refname:short)").split()
    assert "ticket/301-test" not in git(mehr.haupt[APP], "ls-remote", "--heads", "origin")
    assert "ticket/302-test" not in git(mehr.haupt[SKILL], "branch", "--format=%(refname:short)").split()
    assert "ticket/302-test" not in git(mehr.haupt[SKILL], "ls-remote", "--heads", "origin")
    assert "ticket/303-test" in git(mehr.haupt[WEGWERF], "branch", "--format=%(refname:short)").split()
    assert "ticket/303-test" in git(mehr.haupt[WEGWERF], "ls-remote", "--heads", "origin")
    meldungen = [r.getMessage() for r in caplog.records if WEGWERF in r.getMessage()]
    assert len(meldungen) == 1, meldungen
    assert "ticket_repo" in meldungen[0]


@pytest.mark.parametrize(
    ("url", "erwartet"),
    [
        ("https://github.com/A/b", "A/b"),
        ("https://github.com/A/b.git", "A/b"),
        ("https://github.com/A/b/", "A/b"),
        ("https://github.com/A/b.git/", "A/b"),
        ("git@github.com:A/b", "A/b"),
        ("git@github.com:A/b.git", "A/b"),
        ("git@github.com:A/b/", "A/b"),
        ("ssh://git@github.com/A/b", "A/b"),
        ("ssh://git@github.com/A/b.git", "A/b"),
        ("ssh://git@github.com/A/b/", "A/b"),
        ("/home/bau/duoplus-leit", ""),
        (r"C:\Users\nutzer\org\fern.git", ""),
        ("/srv/ghq/github.com/A/b.git", ""),
        ("https://notgithub.com/A/b", ""),
        ("http://github.com/A/b", ""),
        ("file:///srv/git@github.com:A/b", ""),
        ("https://evil.example/https://github.com/A/b", ""),
    ],
)
def test_eine_slug_regel_in_gh_und_hausmeister(url: str, erwartet: str) -> None:
    """#129 (b): ``gh`` und ``hausmeister`` liefern für jede URL-Form denselben Slug."""
    assert gh._github_slug(url) == erwartet
    assert hausmeister._slug(url) == erwartet


def test_github_slug_aus_origin_mit_schraegstrich_am_ende(tmp_path: Path) -> None:
    """#129 (b): ``https://github.com/A/b/`` wird auch über ``github_slug_aus_origin`` erkannt."""
    repo = _repo_mit_origin(tmp_path / "slash", "https://github.com/A/b/")
    assert gh.github_slug_aus_origin(repo) == "A/b"


def test_hausmeister_hat_keine_eigene_slug_regex() -> None:
    """#129 (b): ``hausmeister`` nutzt die eine Slug-Regel aus ``gh`` ohne eigene Regex."""
    assert not hasattr(hausmeister, "_GITHUB_SLUG")
    quelle = (SKILL_ROOT / "to_spawn" / "hausmeister.py").read_text(encoding="utf-8")
    assert r"github\.com" not in quelle


def test_review_ordner_in_gitignore(tmp_path: Path) -> None:
    """#129 (c): ``.review/`` steht in ``.gitignore``; ``git status --short`` bleibt mit ``.review/`` leer."""
    gitignore = (SKILL_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".review/" in [z.strip() for z in gitignore]
    probe = tmp_path / "probe"
    subprocess.run(["git", "init", "-q", str(probe)], check=True)
    (probe / ".gitignore").write_text((SKILL_ROOT / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "-C", str(probe), "add", ".gitignore"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(probe),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=t@example.org",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        check=True,
    )
    beleg = probe / ".review" / "beleg.md"
    beleg.parent.mkdir()
    beleg.write_text("beleg\n", encoding="utf-8")
    status = subprocess.run(
        ["git", "-C", str(probe), "status", "--short"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    assert status == ""
