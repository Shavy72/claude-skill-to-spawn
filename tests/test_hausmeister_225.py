"""Hausmeister #225: offene Punkte aus PR #222 (#196).

Prüft alle Punkte aus #225 mit echten Git-Repos (lokaler bare-origin, echtes
``git worktree add`` und ``git worktree lock``); gestellt ist nur ``gh`` sowie
die Windows-Prozessabfrage über die vorhandenen Nähte (#709):
- Gesperrter Worktree (``locked``, mit/ohne Grund, innen und außen) bleibt als
  Regel mit Grund „gesperrt (locked)“ (``fehler=False``, kein „Löschen gescheitert“).
- Langlebiger Worktree ohne Ticket-Nummer außerhalb der Wurzeln (z. B. ``deploy-tmp``,
  frisch oder alt und sauber) bleibt still stehen, nie in ``liste_fuer_david``.
- Anzahl ``git worktree list``-Aufrufe je Rundgang entspricht genau der Anzahl Hauptrepos.
- Eine Parse-Stelle (``_worktree_eintraege``) für alle Worktree-Abfragen.
- Bare-Hauptrepo, Pfade mit Leerzeichen/Umlauten und Windows-Pfadvergleich (Groß/Klein).
"""

# ruff: noqa: F811 — ``welt``/``repos`` kommen als Fixtures aus den #1368-/#176-Tests.
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from test_hausmeister_196 import _ergebnis_fuer, _gleich, haupt_bekannt  # noqa: F401
from test_hausmeister_1368 import Welt, git, welt  # noqa: F401
from test_hausmeister_ticket_repo_176 import APP, SKILL, Repos, repos  # noqa: F401

from to_spawn import aufpasser, hausmeister, hausmeister_windows, prozessbaum

TAG = 24 * 3600.0


def _draussen(
    haupt: Path, ziel: Path, zweig: str, *, datei: str = "arbeit.txt", push: bool = True
) -> Path:
    """Legt einen echten Worktree von ``haupt`` außerhalb der Wurzeln an."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    git(haupt, "worktree", "add", "-q", "-b", zweig, str(ziel))
    (ziel / datei).write_text("stand\n", encoding="utf-8")
    git(ziel, "add", datei)
    git(ziel, "commit", "-q", "-m", "stand")
    if push:
        git(ziel, "push", "-q", "-u", "origin", zweig)
    return ziel


def test_gesperrter_worktree_draussen_bleibt_als_regel(haupt_bekannt: Welt) -> None:
    welt = haupt_bekannt
    wt = _draussen(welt.haupt, welt.tmp / "c" / "dev" / "wt-2251", "ticket/2251-gesperrt")
    git(welt.haupt, "worktree", "lock", str(wt))
    welt.ticket(2251, "CLOSED", 48)

    r = hausmeister.rundgang(loeschen=True, jetzt=welt.jetzt)
    assert r.gelaufen, r.grund
    erg = _ergebnis_fuer(r, wt)
    assert erg.art == "bleibt" and not erg.fehler and not erg.fuer_david, erg
    assert erg.grund == "gesperrt (locked)", erg.grund
    assert "Löschen gescheitert" not in erg.grund
    assert wt.is_dir()


def test_gesperrter_worktree_in_wurzel_mit_grund_bleibt_als_regel(welt: Welt) -> None:
    wt = welt.worktree(2252)
    git(welt.haupt, "worktree", "lock", "--reason", "Wartung", str(wt))
    welt.ticket(2252, "CLOSED", 48)

    for loeschen in (False, True):
        erg = welt.aufraeumen(wt, loeschen=loeschen)
        assert erg.art == "bleibt" and not erg.fehler and not erg.fuer_david, erg
        assert "gesperrt (locked)" in erg.grund and "Wartung" in erg.grund, erg.grund
        assert wt.is_dir()


def test_deploy_tmp_draussen_ohne_nummer_bleibt_still_und_nicht_an_nutzer(
    haupt_bekannt: Welt,
) -> None:
    """Sowohl frisch als auch alt+sauber: ``deploy-tmp`` außerhalb bleibt still, nie in ``liste_fuer_david``."""
    welt = haupt_bekannt
    wt = _draussen(welt.haupt, welt.tmp / "ext" / "deploy-tmp", "deploy-tmp")

    # Frisch (< 24 h)
    r_frisch = hausmeister.rundgang(loeschen=True, jetzt=welt.jetzt)
    assert r_frisch.gelaufen, r_frisch.grund
    erg_frisch = _ergebnis_fuer(r_frisch, wt)
    assert erg_frisch.art == "bleibt" and not erg_frisch.fehler and not erg_frisch.fuer_david, erg_frisch
    assert wt.is_dir()
    assert not any(_gleich(Path(p), wt) for p in hausmeister.liste_fuer_david(welt.log) if isinstance(p, Path))

    # Alt (> 24 h) und sauber gepusht: darf trotzdem nicht gelöscht werden und nicht an den Nutzer gehen
    r_alt = hausmeister.rundgang(loeschen=True, jetzt=welt.jetzt + 30 * TAG)
    assert r_alt.gelaufen, r_alt.grund
    erg_alt = _ergebnis_fuer(r_alt, wt)
    assert erg_alt.art == "bleibt" and not erg_alt.fehler and not erg_alt.fuer_david, erg_alt
    assert wt.is_dir()
    assert not any(_gleich(Path(p), wt) for p in hausmeister.liste_fuer_david(welt.log) if isinstance(p, Path))


def test_worktree_list_einmal_je_hauptrepo_pro_rundgang(
    welt: Welt, repos: Repos, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anzahl ``git worktree list``-Aufrufe im Rundgang = Anzahl Hauptrepos (unabhängig von Kandidaten)."""
    repos.repo(SKILL)
    repos.maschine([(APP, APP), (SKILL, SKILL)])
    haupt_app = repos.haupt[APP]
    haupt_skill = repos.haupt[SKILL]
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", f"{haupt_app} {haupt_skill}")
    monkeypatch.setattr(hausmeister_windows, "_prozesse", list)
    monkeypatch.setattr(prozessbaum, "arbeitsordner_in_windows", lambda _ordner: {})

    # Mehrere Worktrees je Hauptrepo (innen, außen, gesperrt, ohne Nummer)
    wt_a1 = _draussen(haupt_app, welt.tmp / "ext" / "wt-2253", "ticket/2253-a")
    wt_a2 = _draussen(haupt_app, welt.tmp / "ext" / "deploy-tmp", "deploy-tmp")
    git(haupt_app, "worktree", "lock", str(wt_a1))
    wt_s1 = _draussen(haupt_skill, welt.tmp / "ext" / "skill-2254", "l4/2254-s")
    wt_s2 = _draussen(haupt_skill, welt.tmp / "ext" / "pruef-tmp", "pruef-tmp")
    repos.ticket(APP, 2253, "CLOSED", 48)
    repos.ticket(SKILL, 2254, "OPEN")

    echt_git = aufpasser.git_lauf
    aufrufe: list[Path] = []

    def spion(cwd: Path, *args: str, **kw: object) -> str:
        if len(args) >= 2 and args[0] == "worktree" and args[1] == "list":
            aufrufe.append(Path(cwd))
        return echt_git(cwd, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(aufpasser, "git_lauf", spion)

    r = hausmeister.rundgang(loeschen=True, jetzt=welt.jetzt)
    assert r.gelaufen, r.grund
    assert _ergebnis_fuer(r, wt_a1).grund == "gesperrt (locked)"
    assert not _ergebnis_fuer(r, wt_a2).fuer_david
    assert _ergebnis_fuer(r, wt_s1).art == "bleibt"
    assert not _ergebnis_fuer(r, wt_s2).fuer_david
    assert len(aufrufe) == 2, f"erwartet 2 (Anzahl Hauptrepos), erhalten {len(aufrufe)}: {aufrufe}"


def test_bare_hauptrepo_mit_worktree_und_sperre(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bare-Hauptrepo: Worktrees werden erkannt, normale geräumt, gesperrte als Regel behalten."""
    bare_haupt = welt.tmp / "bare-haupt.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(welt.bare), str(bare_haupt)], check=True)
    git(bare_haupt, "remote", "set-url", "origin", "https://github.com/Shavy72/duoplus-management")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", str(bare_haupt))
    monkeypatch.setattr(hausmeister_windows, "_prozesse", list)
    monkeypatch.setattr(prozessbaum, "arbeitsordner_in_windows", lambda _ordner: {})

    wt_weg = _draussen(bare_haupt, welt.tmp / "ext" / "wt-2255", "ticket/2255-bare")
    wt_gesperrt = _draussen(bare_haupt, welt.tmp / "ext" / "wt-2256", "ticket/2256-bare-lock")
    git(bare_haupt, "worktree", "lock", str(wt_gesperrt))
    welt.ticket(2255, "CLOSED", 48)
    welt.ticket(2256, "CLOSED", 48)

    assert not hausmeister._im_bereich(bare_haupt)
    r = hausmeister.rundgang(loeschen=True, jetzt=welt.jetzt)
    assert r.gelaufen, r.grund
    assert _ergebnis_fuer(r, wt_weg).art == "gelöscht"
    assert not wt_weg.exists()
    erg_lock = _ergebnis_fuer(r, wt_gesperrt)
    assert erg_lock.art == "bleibt" and not erg_lock.fehler and erg_lock.grund == "gesperrt (locked)"
    assert wt_gesperrt.is_dir()


def test_pfad_mit_leerzeichen_und_umlaut_wird_erkannt_und_geraeumt(haupt_bekannt: Welt) -> None:
    welt = haupt_bekannt
    wt = _draussen(
        welt.haupt,
        welt.tmp / "pfad mit leer" / "ümlaut äöü" / "wt-2257",
        "ticket/2257-umlaut",
    )
    welt.ticket(2257, "CLOSED", 48)

    r = hausmeister.rundgang(loeschen=True, jetzt=welt.jetzt)
    assert r.gelaufen, r.grund
    erg = _ergebnis_fuer(r, wt)
    assert erg.art == "gelöscht" and not erg.fehler, erg.grund
    assert not wt.exists()


def test_als_worktree_eingetragen_nutzt_eine_parse_stelle_und_normcase(
    haupt_bekannt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_als_worktree_eingetragen`` nutzt ``_worktree_eintraege`` und vergleicht über ``_pfad_schluessel``."""
    welt = haupt_bekannt
    wt = _draussen(welt.haupt, welt.tmp / "ext" / "wt-2258", "ticket/2258-parse")
    aufrufe = 0
    echt = hausmeister._worktree_eintraege

    def spion(repo: Path) -> list[object]:
        nonlocal aufrufe
        aufrufe += 1
        return echt(repo)

    monkeypatch.setattr(hausmeister, "_worktree_eintraege", spion)
    assert hausmeister._als_worktree_eingetragen(welt.haupt, wt) is True
    assert hausmeister._als_worktree_eingetragen(welt.haupt, welt.haupt) is False
    assert aufrufe == 2

    # Windows-Pfadvergleich (Laufwerksbuchstabe / Groß-Klein über normcase)
    monkeypatch.setattr(os.path, "normcase", lambda s: s.replace("/", "\\").lower())
    assert hausmeister._pfad_schluessel(Path("C:/Dev/WT-1")) == hausmeister._pfad_schluessel(
        Path("c:\\dev\\wt-1")
    )


def test_docstrings_nennen_erweiterten_bereich_und_schutz() -> None:
    doc_wurzel = hausmeister._wurzel_erlaubt.__doc__ or ""
    doc_bereich = hausmeister._im_bereich.__doc__ or ""
    assert "_im_bereich" in doc_wurzel and "Worktree" in doc_wurzel
    assert "Sicherung" in doc_bereich or "Wegwerf" in doc_bereich
