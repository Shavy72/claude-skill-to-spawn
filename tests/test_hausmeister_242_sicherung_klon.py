"""Hausmeister #242: Klon ohne Remote-Ref von ``sicherung/<N>`` darf nicht jeden Takt neu sichern.

Prüft:
- Ursprünglicher Befund (#242, im Kern durch #179 mit ``ls-remote`` mitgelöst):
  App-Klon (kein Worktree, abgehängter Kopf, 3 geänderte Dateien, lokaler Zweig
  ``sicherung/<N>``, verengter Fetch-Refspec ohne ``refs/remotes/origin/sicherung/*``)
  wird einmal gesichert, gegen ``origin`` geprüft und geräumt.
- Rest-Lücke A: Existiert ``refs/heads/sicherung/<N>`` bereits am Server (z. B. aus
  einem anderen Klon/Worktree oder früheren Lauf) und fehlt im Klon als Tracking-Ref
  oder lokal, hängt die Sicherung sauber an den Server-Stand an (kein Non-Fast-Forward,
  kein endloses Nachsichern in jedem Takt).
- Rest-Lücke B: Scheitert der Push in ``aufpasser.sichern``, bleibt lokal kein
  ungepushter ``refs/heads/sicherung/<N>`` zurück.
- Rest-Lücke C: Liegt der abgehängte Kopf eines Einzelzweig-Klons bereits auf
  ``origin`` unter ``sicherung/<N>`` (Baum sauber), räumt der Hausmeister ohne
  unnötigen neuen Sicherungs-Commit.

Echte Git-Repos (bare-„origin“ über ``insteadOf``) wie in den #1368-Tests.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus den #1368-Tests.
from __future__ import annotations

from pathlib import Path

import pytest
from test_hausmeister_1368 import Welt, _commit, git, welt  # noqa: F401 — Fixture

from to_spawn import aufpasser


def _einzelzweig_klon(welt: Welt, name: str) -> Path:
    """Klon mit verengtem Fetch-Refspec (nur ``master`` — kein ``refs/remotes/origin/sicherung/*``)."""
    klon = welt.klon(name)
    git(klon, "config", "remote.origin.fetch", "+refs/heads/master:refs/remotes/origin/master")
    return klon


def test_242_klon_ohne_sicherung_tracking_ref_wird_einmal_gesichert_und_geraeumt(welt: Welt) -> None:
    """Befund #242: Klon, abgehängter Kopf, 3 geänderte Dateien, lokaler Zweig
    ``sicherung/<N>``, kein ``refs/remotes/origin/sicherung/*`` ⇒ gesichert und gelöscht."""
    klon = _einzelzweig_klon(welt, "wt/wt-2421")
    git(klon, "checkout", "-q", "--detach")
    _commit(klon, "nur-lokal.txt", "lokaler commit\n", push=False)
    # Lokaler Zweig sicherung/2421 auf älterem Stand, noch nie gepusht
    git(klon, "branch", "sicherung/2421", "HEAD")
    for nr in (1, 2, 3):
        (klon / f"datei{nr}.txt").write_text(f"änderung {nr}\n", encoding="utf-8")
    welt.ticket(2421, "CLOSED", 48)

    r = welt.aufraeumen(klon, loeschen=True)

    assert r.art == "gesichert und gelöscht" and r.sicherung and not r.fehler, r
    assert not klon.exists()
    assert welt.im_bare("rev-parse", "refs/heads/sicherung/2421").strip() == r.sicherung
    for nr in (1, 2, 3):
        assert welt.im_bare("show", f"sicherung/2421:datei{nr}.txt") == f"änderung {nr}\n"


def test_242_server_hat_bereits_sicherung_klon_ohne_tracking_ref_kettet_sauber_an(welt: Welt) -> None:
    """Rest-Lücke A: ``origin`` hat bereits ``sicherung/<N>`` aus einem früheren Lauf/anderen
    Ordner; ein Einzelzweig-Klon ohne lokales ``sicherung/<N>`` (oder mit älterem Stand)
    muss daran anketten und darf nicht mit Non-Fast-Forward in jedem Takt neu sichern."""
    # Frühere Sicherung aus einem anderen Worktree auf origin ablegen
    alt = welt.worktree(2422, schmutzig=True)
    vorher = aufpasser.sichern(alt, 2422, welt.jetzt)
    assert vorher is not None

    klon = _einzelzweig_klon(welt, "duoplus-2422")
    git(klon, "checkout", "-q", "--detach")
    for nr in (1, 2, 3):
        (klon / f"neu{nr}.txt").write_text(f"neu {nr}\n", encoding="utf-8")
    welt.ticket(2422, "CLOSED", 48)

    r = welt.aufraeumen(klon, loeschen=True)

    assert r.art == "gesichert und gelöscht" and r.sicherung and not r.fehler, r
    assert not klon.exists()
    eltern = welt.im_bare("log", "-1", "--format=%P", "refs/heads/sicherung/2422").split()
    assert vorher in eltern
    assert welt.im_bare("show", "sicherung/2422:neu1.txt") == "neu 1\n"


def test_242_fehlgeschlagener_push_hinterlaesst_keinen_lokalen_sicherung_zweig(welt: Welt) -> None:
    """Rest-Lücke B: Scheitert der Push in ``aufpasser.sichern``, bleibt lokal kein neuer
    ungepushter ``refs/heads/sicherung/<N>`` liegen, der im nächsten Takt erneut sichert."""
    klon = _einzelzweig_klon(welt, "wt/wt-2423")
    (klon / "arbeit.txt").write_text("offen\n", encoding="utf-8")
    git(klon, "config", "remote.origin.url", str(welt.tmp / "nicht-da.git"))

    with pytest.raises(RuntimeError):
        aufpasser.sichern(klon, 2423, welt.jetzt)

    refs = git(klon, "for-each-ref", "--format=%(refname)", "refs/heads/sicherung/2423").strip()
    assert refs == ""


def test_242_sauberer_einzelzweig_klon_auf_server_sicherung_ohne_neue_sicherung_geraeumt(welt: Welt) -> None:
    """Rest-Lücke C: Liegt der abgehängte Kopf eines sauberen Einzelzweig-Klons bereits
    auf ``origin`` unter ``sicherung/<N>`` (lokal noch nicht geholt), erkennt der Hausmeister
    ihn nach dem Holen von ``sicherung/<N>`` und löscht ohne zweiten Sicherungs-Commit."""
    klon = _einzelzweig_klon(welt, "wt/wt-2424")
    git(klon, "checkout", "-q", "--detach")
    sha = _commit(klon, "gesichert.txt", "schon oben\n", push=False)
    # Sicherung auf origin existiert (z. B. von einem anderen Rechner/Klon), aber lokal fehlt sie
    git(klon, "push", "-q", "origin", f"{sha}:refs/heads/sicherung/2424")
    # Zusätzlicher Sicherungs-Commit auf origin über einen zweiten Klon, sodass unser Klon
    # den aktuellen Server-Kopf von sicherung/2424 gar nicht in seiner Objekt-Datenbank hat
    zweit = welt.klon("tmp-zweit-2424")
    git(zweit, "fetch", "-q", "origin", "refs/heads/sicherung/2424:refs/heads/sicherung/2424")
    git(zweit, "checkout", "-q", "sicherung/2424")
    kopf_server = _commit(zweit, "zusatz.txt", "weiter\n", push=False)
    git(zweit, "push", "-q", "origin", "refs/heads/sicherung/2424:refs/heads/sicherung/2424")
    welt.ticket(2424, "CLOSED", 48)

    r = welt.aufraeumen(klon, loeschen=True)

    assert r.art == "gelöscht" and r.sicherung is None and not r.fehler, r
    assert not klon.exists()
    assert welt.im_bare("rev-parse", "refs/heads/sicherung/2424").strip() == kopf_server
