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

from to_spawn import config, einrichten, gh, hausmeister, vorgaben

PROBE_SLUG = "bau/duoplus-leit"


def _repo_mit_origin(ordner: Path, url: str) -> Path:
    subprocess.run(["git", "init", "-q", str(ordner)], check=True)
    subprocess.run(
        ["git", "-C", str(ordner), "remote", "add", "origin", url], check=True
    )
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


@pytest.mark.parametrize(
    "url", ["/home/bau/duoplus-leit", r"C:\Users\nutzer\org\fern.git"]
)
def test_github_slug_nur_aus_github_origin(tmp_path: Path, url: str) -> None:
    repo = _repo_mit_origin(tmp_path / "klon", url)
    assert gh.github_slug_aus_origin(repo) == ""
    gh_repo = _repo_mit_origin(tmp_path / "echt", "git@github.com:Ich/App.git")
    assert gh.github_slug_aus_origin(gh_repo) == "Ich/App"


def test_lokaler_origin_kommt_nicht_in_die_maschinen_liste(tmp_path: Path) -> None:
    klon = _repo_mit_origin(tmp_path / "probe-klon", "/home/bau/duoplus-leit")
    datei = config.maschine_datei()
    config.maschine_eintragen(klon)
    eintraege = (
        json.loads(datei.read_text(encoding="utf-8"))["repos"]
        if datei.is_file()
        else []
    )
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
    assert (
        hausmeister._ticket_repo_fuer("Shavy72/duoplus-management")
        == "Shavy72/duoplus-management"
    )
    assert (
        hausmeister._ticket_repo_fuer("shavy72/claude-skill-to-spawn")
        == "Shavy72/duoplus-management"
    )
    assert (
        hausmeister._ticket_repo_fuer(PROBE_SLUG) is None
    )  # eigener Eintrag kaputt ⇒ bleibt
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
    assert (
        "ticket_repo" in meldungen[0] and str(config.maschine_datei()) in meldungen[0]
    )


# --- 3 Trocken = echt ----------------------------------------------------------------------


def test_trocken_und_echt_melden_dieselben_fehler(welt: Welt) -> None:  # noqa: F811
    wt, probe = _probe_welt(welt)
    welt.ticket(11, "OPEN")  # echter Lauf löscht hier nichts
    trocken = [welt.aufraeumen(o) for o in (wt, probe)]
    echt = [welt.aufraeumen(o, loeschen=True) for o in (wt, probe)]
    assert [(e.art, e.grund, e.fehler) for e in trocken] == [
        (e.art, e.grund, e.fehler) for e in echt
    ]
    assert sum(e.fehler for e in echt) == 1
    assert wt.is_dir() and probe.is_dir()


# --- 4 _im_bereich: aufgelöster Pfad ohne versteckte Ordner --------------------------------


def test_im_bereich_glob_klasse_fuer_punkt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    heim = _home(monkeypatch, tmp_path)
    ssh = heim / "x" / "wt-1" / ".ssh"
    ssh.mkdir(parents=True)
    monkeypatch.setenv(
        "TO_SPAWN_HAUSMEISTER_WURZELN", os.path.expanduser("~/x/wt-*/[.]ssh")
    )
    assert not hausmeister._im_bereich(ssh)


def test_im_bereich_symlink_auf_versteckten_ordner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    heim = _home(monkeypatch, tmp_path)
    (heim / "x" / ".ssh").mkdir(parents=True)
    link = heim / "x" / "wt-1"
    link.symlink_to(heim / "x" / ".ssh", target_is_directory=True)
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", os.path.expanduser("~/x/*"))
    assert not hausmeister._im_bereich(link)
