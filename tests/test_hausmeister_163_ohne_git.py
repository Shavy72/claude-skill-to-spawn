"""Hausmeister #163: verwaiste Ordner ohne eigenes Git unter dem Worktree-Ordner räumen.

Prüft alle Abnahme-Regeln mit Wegwerf-Ordnern (nie System-Temp):
- Verwaiste Ordner ohne ``.git`` oder mit totem ``gitdir:``-Verweis werden geräumt, wenn
  alles gilt: Ticket-Nummer im Namen, kein Prozess darin, kein offenes Ticket dazu, nichts
  jünger als die Alters-Schwelle, kein Geheimnis-Verdacht, kein eingebettetes Repo.
- Fail-closed bei Lesefehlern (Rechte, Ticket-Abfrage) und ohne Ticket-Nummer im Namen.
"""

# ruff: noqa: F811 — ``welt``/``repos`` kommen als Fixtures aus den #1368-/#176-Tests.
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_hausmeister_1368 import Welt, iso, welt  # noqa: F401 — Fixture
from test_hausmeister_ticket_repo_176 import APP, SKILL, STUNDE, Repos, repos  # noqa: F401

from to_spawn import hausmeister

TAG = 24 * 3600.0
ALT_TAGE = 30

#: gh-Attrappe für Mehr-Repo-Tests mit realistischer 404-Meldung für fehlende Issues.
GH_MEHR_REPO_404_PY = """
import json, os, sys
a = sys.argv[1:]
if a[:2] == ["issue", "view"]:
    repo = a[a.index("-R") + 1] if "-R" in a else ""
    daten = json.load(open(os.environ["GH_TICKETS_JE_REPO"], encoding="utf-8"))
    eintrag = daten.get(repo.lower(), {}).get(a[2])
    if eintrag == "FEHLT":
        sys.stderr.write(
            f"GraphQL: Could not resolve to an issue or pull request with the number of {a[2]}.\\n"
        )
        sys.exit(1)
    if eintrag == "NETZFEHLER":
        sys.stderr.write("HTTP 502: Bad Gateway\\n")
        sys.exit(1)
    if eintrag is None:
        sys.exit(1)
    print(json.dumps(eintrag))
    sys.exit(0)
if a[:2] == ["pr", "list"]:
    print("[]")
    sys.exit(0)
sys.exit(1)
"""


def _altern(welt: Welt) -> None:
    welt.jetzt += ALT_TAGE * TAG


def _ordner(welt: Welt, rel: str, dateien: tuple[str, ...] = ("arbeit.txt",)) -> Path:
    ordner = welt.heim / rel
    ordner.mkdir(parents=True, exist_ok=True)
    for name in dateien:
        ziel = ordner / name
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text("inhalt\n", encoding="utf-8")
    return ordner


def test_verwaister_ordner_ohne_git_wird_geraeumt(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/wt-1631", ("arbeit.txt", "unter/daten.txt"))
    _altern(welt)
    welt.ticket(1631, "CLOSED", 48)

    trocken = welt.aufraeumen(ordner, loeschen=False)
    assert trocken.art == "gelöscht" and trocken.trocken and trocken.ticket == 1631, trocken.grund
    assert ordner.is_dir(), "Probelauf darf nie löschen"

    echt = welt.aufraeumen(ordner, loeschen=True)
    assert echt.art == "gelöscht" and not echt.trocken and not echt.fehler, echt.grund
    assert not ordner.exists()


def test_verwaister_worktree_mit_totem_gitdir_wird_geraeumt(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/wt-1632", ("arbeit.txt",))
    (ordner / ".git").write_text(
        f"gitdir: {welt.tmp / 'weg' / 'worktrees' / 'wt-1632'}\n", encoding="utf-8"
    )
    _altern(welt)
    welt.ticket(1632, "CLOSED", 48)

    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "gelöscht" and not r.fehler, r.grund
    assert not ordner.exists()


def test_ohne_git_mit_prozess_bleibt(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/wt-1633")
    _altern(welt)
    welt.ticket(1633, "CLOSED", 48)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], cwd=ordner
    )
    try:
        r = welt.aufraeumen(ordner, loeschen=True)
        assert r.art == "bleibt" and "Prozess" in r.grund, r.grund
        assert ordner.is_dir()
    finally:
        proc.kill()
        proc.wait()


def test_ohne_git_mit_offenem_ticket_bleibt(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/wt-1634")
    _altern(welt)
    welt.ticket(1634, "OPEN")

    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "bleibt" and not r.fehler and "offen" in r.grund, r.grund
    assert ordner.is_dir()


def test_ohne_git_mehrere_repos_offen_haelt_und_404_im_anderen_erlaubt(
    welt: Welt, repos: Repos, monkeypatch: pytest.MonkeyPatch
) -> None:
    repos.repo(SKILL)
    repos.maschine([(APP, APP), (SKILL, SKILL)])
    stub = welt.tmp / "gh_404.py"
    stub.write_text(GH_MEHR_REPO_404_PY, encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))

    offen_ordner = _ordner(welt, "wt/skill-1635")
    zu_ordner = _ordner(welt, "wt/wt-1636")
    netz_ordner = _ordner(welt, "wt/wt-1637")
    _altern(welt)

    daten = {
        APP.lower(): {
            "1635": {"state": "CLOSED", "closedAt": iso(welt.jetzt - 48 * STUNDE)},
            "1636": {"state": "CLOSED", "closedAt": iso(welt.jetzt - 48 * STUNDE)},
            "1637": {"state": "CLOSED", "closedAt": iso(welt.jetzt - 48 * STUNDE)},
        },
        SKILL.lower(): {
            "1635": {"state": "OPEN"},
            "1636": "FEHLT",
            "1637": "NETZFEHLER",
        },
    }
    repos.tickets.write_text(json.dumps(daten), encoding="utf-8")

    r_offen = welt.aufraeumen(offen_ordner, loeschen=True)
    assert r_offen.art == "bleibt" and "offen" in r_offen.grund, r_offen.grund
    assert offen_ordner.is_dir()

    r_zu = welt.aufraeumen(zu_ordner, loeschen=True)
    assert r_zu.art == "gelöscht" and not r_zu.fehler, r_zu.grund
    assert not zu_ordner.exists()

    r_netz = welt.aufraeumen(netz_ordner, loeschen=True)
    assert r_netz.art == "bleibt" and r_netz.fehler, r_netz.grund
    assert netz_ordner.is_dir()


def test_ohne_git_frisch_oder_frische_unterdatei_bleibt(welt: Welt) -> None:
    frisch = _ordner(welt, "wt/wt-1638")
    welt.ticket(1638, "CLOSED", 48)
    r1 = welt.aufraeumen(frisch, loeschen=True)
    assert r1.art == "bleibt" and "frisch" in r1.grund, r1.grund
    assert frisch.is_dir()

    unter_frisch = _ordner(welt, "wt/wt-1639", ("alt.txt", "unter/neu.txt"))
    _altern(welt)
    welt.ticket(1639, "CLOSED", 48)
    # Nur die verschachtelte Datei ist frisch (Zeitpunkt knapp vor welt.jetzt)
    os.utime(unter_frisch / "unter" / "neu.txt", (welt.jetzt - 60, welt.jetzt - 60))
    r2 = welt.aufraeumen(unter_frisch, loeschen=True)
    assert r2.art == "bleibt" and "frisch" in r2.grund, r2.grund
    assert unter_frisch.is_dir()


def test_ohne_git_frisch_bleibt_ohne_ticket_abfrage_und_ohne_fehler(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein frischer Ordner ohne Git bleibt wegen Alter ohne gh-Abfrage (kein Fehler)."""
    frisch = _ordner(welt, "wt/wt-1648")
    aufrufe: list[list[str]] = []

    def kein_gh(args: list[str], **_: object) -> tuple[int, str]:
        aufrufe.append(args)
        return 1, "darf nicht aufgerufen werden"

    monkeypatch.setattr(hausmeister.gh, "lauf", kein_gh)
    r = welt.aufraeumen(frisch, loeschen=True)
    assert r.art == "bleibt" and not r.fehler and "frisch" in r.grund, r.grund
    assert aufrufe == []
    assert frisch.is_dir()


@pytest.mark.parametrize(
    "datei",
    [
        ".env",
        "web/.env.local",
        "config/zugangsdaten.txt",
        "ssl/server.key",
        "certs/ca.pem",
        ".credentials.json",
        "credentials.json",
        "ssh/id_rsa",
        "daten/echt.db",
    ],
)
def test_ohne_git_geheimnis_verdacht_oder_fremde_db_bleibt(welt: Welt, datei: str) -> None:
    ordner = _ordner(welt, "wt/wt-1640", ("arbeit.txt", datei))
    _altern(welt)
    welt.ticket(1640, "CLOSED", 48)

    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david, r.grund
    assert (ordner / datei).is_file()


def test_ohne_git_erzeugnisse_und_kleine_test_db_halten_nicht(welt: Welt) -> None:
    ordner = _ordner(
        welt,
        "wt/wt-1641",
        (
            "scripts/__pycache__/tempo_zugang.cpython-311.pyc",
            "data/.secret_key",
            "accounts.db",
        ),
    )
    _altern(welt)
    welt.ticket(1641, "CLOSED", 48)

    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "gelöscht" and not r.fehler, r.grund
    assert not ordner.exists()


def test_ohne_git_eingebettetes_repo_bleibt(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/wt-1642", ("unter/.git/HEAD",))
    _altern(welt)
    welt.ticket(1642, "CLOSED", 48)

    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and "eingebettetes Repo" in r.grund, r.grund
    assert ordner.is_dir()


def test_ohne_git_ticket_abfrage_fehler_fail_closed(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/wt-1643")
    _altern(welt)
    # #1643 fehlt in GH_TICKETS -> gh endet mit Exit 1 ohne 404-Meldung

    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "bleibt" and r.fehler, r.grund
    assert ordner.is_dir()


def test_ohne_git_lesefehler_scandir_fail_closed(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    ordner = _ordner(welt, "wt/wt-1644", ("zu/datei.txt",))
    _altern(welt)
    welt.ticket(1644, "CLOSED", 48)
    echt_scandir = os.scandir

    def gesperrt(pfad: os.PathLike[str] | str) -> os.ScandirIterator[str]:
        if Path(pfad).name == "zu":
            raise PermissionError("kein Leserecht")
        return echt_scandir(pfad)

    monkeypatch.setattr(os, "scandir", gesperrt)
    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "bleibt" and r.fehler, r.grund
    assert ordner.is_dir()


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="chmod 0 sperrt nur unter POSIX ohne root",
)
def test_ohne_git_chmod_null_fail_closed(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/wt-1645", ("zu/datei.txt",))
    _altern(welt)
    welt.ticket(1645, "CLOSED", 48)
    (ordner / "zu").chmod(0)
    try:
        r = welt.aufraeumen(ordner, loeschen=True)
        assert r.art == "bleibt" and r.fehler, r.grund
        assert ordner.is_dir()
    finally:
        (ordner / "zu").chmod(0o755)


def test_ohne_git_ohne_ticket_nummer_bleibt(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/reste-notizen")
    _altern(welt)

    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and not r.fehler, r.grund
    assert ordner.is_dir()


def test_ohne_git_im_rundgang_aufraeumen_alles(welt: Welt) -> None:
    weg = _ordner(welt, "wt/wt-1646")
    bleibt = _ordner(welt, "wt/wt-1647")
    _altern(welt)
    welt.ticket(1646, "CLOSED", 48)
    welt.ticket(1647, "OPEN")

    ergebnisse = {Path(e.ordner).name: e for e in hausmeister.aufraeumen_alles(loeschen=True, jetzt=welt.jetzt)}
    assert ergebnisse["wt-1646"].art == "gelöscht"
    assert not weg.exists()
    assert ergebnisse["wt-1647"].art == "bleibt"
    assert bleibt.is_dir()
