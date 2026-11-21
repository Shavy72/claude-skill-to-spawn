"""Hausmeister #1371: Zweige ``sicherung/<N>`` gehen 90 Tage nach dem Anlegen (E41).

Weg-Tests mit echtem lokalem Bare-Remote aus ``test_hausmeister_1368`` (Fixture
``welt``): Sicherungen entstehen über das echte :func:`aufpasser.sichern`, das Alter
über ``GIT_COMMITTER_DATE`` beim Anlegen. Gestellt ist nichts außer der Remote-Liste
(Test-Naht ``TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS``, die GitHub-URL zeigt per
``insteadOf`` auf das Bare-Repo).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    ORIGIN,
    Welt,
    git,
    welt,
)

from to_spawn import aufpasser, hausmeister

TAG = 24 * 3600.0


@pytest.fixture
def zweig_welt(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Welt:
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", ORIGIN)
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN", "1")  # #1491: Löschen nur mit Schalter
    return welt


def _sicherung(
    welt: Welt, monkeypatch: pytest.MonkeyPatch, nr: int, vor_tagen: float
) -> str:
    """Echte Sicherung über ``aufpasser.sichern``, angelegt vor ``vor_tagen`` Tagen."""
    wt = welt.worktree(nr, schmutzig=True)
    zeit = datetime.fromtimestamp(welt.jetzt - vor_tagen * TAG, tz=UTC)
    monkeypatch.setenv("GIT_COMMITTER_DATE", zeit.isoformat())
    try:
        sha = aufpasser.sichern(wt, nr, jetzt=welt.jetzt - vor_tagen * TAG)
    finally:
        monkeypatch.delenv("GIT_COMMITTER_DATE")
    assert sha
    return sha


def _zweige(welt: Welt) -> list[str]:
    return sorted(welt.im_bare("for-each-ref", "--format=%(refname:short)").split())


def _zweig_zeilen(welt: Welt) -> list[dict]:
    return [z for z in welt.zeilen() if "zweig" in z]


def test_91_tage_weg_89_bleibt_ticket_zweige_unberuehrt(
    zweig_welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = zweig_welt
    _sicherung(w, monkeypatch, 31, 91)
    _sicherung(w, monkeypatch, 32, 89)
    vorher = _zweige(w)
    assert {"sicherung/31", "sicherung/32", "ticket/31-test", "ticket/32-test"} <= set(
        vorher
    )
    erg = hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=w.jetzt)
    nachher = _zweige(w)
    assert "sicherung/31" not in nachher
    assert "sicherung/32" in nachher
    assert [z for z in vorher if z != "sicherung/31"] == nachher
    assert {e.zweig: e.art for e in erg} == {
        "sicherung/31": "gelöscht",
        "sicherung/32": "bleibt",
    }


def test_log_zeile_je_loeschung(
    zweig_welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = zweig_welt
    sha31 = _sicherung(w, monkeypatch, 31, 120)
    sha33 = _sicherung(w, monkeypatch, 33, 91)
    _sicherung(w, monkeypatch, 32, 10)
    hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=w.jetzt)
    zeilen = _zweig_zeilen(w)
    assert sorted((z["zweig"], z["sha"]) for z in zeilen) == [
        ("sicherung/31", sha31),
        ("sicherung/33", sha33),
    ]
    for z in zeilen:
        assert z["art"] == "gelöscht" and z["trocken"] is False
        assert z["remote"] == ORIGIN and "Tage" in z["grund"]
        assert {"zeit", "angelegt", "fehler"} <= set(z)


def test_trockenlauf_loescht_nichts_schreibt_aber_log(
    zweig_welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = zweig_welt
    _sicherung(w, monkeypatch, 31, 91)
    erg = hausmeister.sicherungen_aufraeumen(jetzt=w.jetzt)
    assert [(e.zweig, e.art, e.trocken) for e in erg] == [
        ("sicherung/31", "gelöscht", True)
    ]
    assert "sicherung/31" in _zweige(w)
    assert [z["trocken"] for z in _zweig_zeilen(w)] == [True]


def _nachsichern(
    welt: Welt, monkeypatch: pytest.MonkeyPatch, nr: int, vor_tagen: float
) -> None:
    wt = welt.heim / "wt" / f"wt-{nr}"
    (wt / f"spaeter-{vor_tagen}.txt").write_text("neu\n", encoding="utf-8")
    zeit = datetime.fromtimestamp(welt.jetzt - vor_tagen * TAG, tz=UTC)
    monkeypatch.setenv("GIT_COMMITTER_DATE", zeit.isoformat())
    try:
        assert aufpasser.sichern(wt, nr)
    finally:
        monkeypatch.delenv("GIT_COMMITTER_DATE")


def test_alte_kette_mit_alter_nachsicherung_geht(
    zweig_welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Angelegt vor 120 Tagen, zuletzt nachgesichert vor 95 Tagen ⇒ weg."""
    w = zweig_welt
    _sicherung(w, monkeypatch, 31, 120)
    _nachsichern(w, monkeypatch, 31, 95)
    erg = hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=w.jetzt)
    assert [(e.zweig, e.art) for e in erg] == [("sicherung/31", "gelöscht")]
    assert "sicherung/31" not in _zweige(w)


def test_frische_nachsicherung_haelt_den_zweig(
    zweig_welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prüfpanel #1371: Ticket läuft > 90 Tage, Aufpasser sichert noch ⇒ bleibt.

    Sonst Lösch-Schleife: der Aufpasser legt den Zweig mit der alten Kette neu an,
    der Hausmeister löscht ihn am nächsten Tag wieder.
    """
    w = zweig_welt
    _sicherung(w, monkeypatch, 31, 91)
    _nachsichern(w, monkeypatch, 31, 0)
    erg = hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=w.jetzt)
    assert [(e.zweig, e.art) for e in erg] == [("sicherung/31", "bleibt")]
    assert "sicherung/31" in _zweige(w)


def test_hoechstens_einmal_taeglich(
    zweig_welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = zweig_welt
    _sicherung(w, monkeypatch, 31, 91)
    assert hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=w.jetzt)
    _sicherung(w, monkeypatch, 33, 95)
    assert hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=w.jetzt + 3600) == []
    assert "sicherung/33" in _zweige(w)
    erg = hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=w.jetzt + 25 * 3600)
    assert [(e.zweig, e.art) for e in erg] == [("sicherung/33", "gelöscht")]


def test_trockenlauf_verbraucht_den_tag_nicht(
    zweig_welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = zweig_welt
    _sicherung(w, monkeypatch, 31, 91)
    hausmeister.sicherungen_aufraeumen(jetzt=w.jetzt)
    erg = hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=w.jetzt + 60)
    assert [(e.zweig, e.art, e.trocken) for e in erg] == [
        ("sicherung/31", "gelöscht", False)
    ]


def test_laeuft_im_takt_von_aufraeumen_alles(
    zweig_welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = zweig_welt
    _sicherung(w, monkeypatch, 31, 91)
    hausmeister.aufraeumen_alles(loeschen=True, jetzt=w.jetzt)
    assert "sicherung/31" not in _zweige(w)
    _sicherung(w, monkeypatch, 33, 91)
    hausmeister.aufraeumen_alles(loeschen=True, jetzt=w.jetzt + 3600)
    assert "sicherung/33" in _zweige(w)


def test_andere_zweige_nie_angefasst(
    zweig_welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nur ``sicherung/<Zahl>``: alte Zweige mit anderem Namen bleiben."""
    w = zweig_welt
    alt = datetime.fromtimestamp(w.jetzt - 200 * TAG, tz=UTC).isoformat()
    monkeypatch.setenv("GIT_COMMITTER_DATE", alt)
    monkeypatch.setenv("GIT_AUTHOR_DATE", alt)
    saat = w.tmp / "alt"
    git(w.tmp, "clone", "-q", ORIGIN, str(saat))
    git(saat, "commit", "-q", "--allow-empty", "-m", "Aufpasser: Sicherung #7 alt")
    for zweig in ("sicherung/abc", "sicherung-7", "ticket/7-alt", "sicherung/7/x"):
        git(saat, "push", "-q", "origin", f"HEAD:refs/heads/{zweig}")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    vorher = _zweige(w)
    assert hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=w.jetzt) == []
    assert _zweige(w) == vorher


def test_ohne_aufpasser_betreff_bleibt_der_zweig(
    zweig_welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anlegezeit nicht belegbar ⇒ im Zweifel bleibt (keine Schätzung über die Spitze)."""
    w = zweig_welt
    alt = datetime.fromtimestamp(w.jetzt - 200 * TAG, tz=UTC).isoformat()
    monkeypatch.setenv("GIT_COMMITTER_DATE", alt)
    saat = w.tmp / "alt"
    git(w.tmp, "clone", "-q", ORIGIN, str(saat))
    git(saat, "commit", "-q", "--allow-empty", "-m", "von Hand")
    git(saat, "push", "-q", "origin", "HEAD:refs/heads/sicherung/8")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    erg = hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=w.jetzt)
    assert [(e.zweig, e.art) for e in erg] == [("sicherung/8", "bleibt")]
    assert "sicherung/8" in _zweige(w)


def test_remote_nicht_erreichbar_wirft_nie(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", str(welt.tmp / "gibt-es-nicht.git")
    )
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN", "1")  # #1491: Löschen nur mit Schalter
    erg = hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=welt.jetzt)
    assert len(erg) == 1 and erg[0].fehler and erg[0].art == "bleibt"
    assert [z["fehler"] for z in _zweig_zeilen(welt)] == [True]
    # Kein Tagesstempel nach reinem Fehler: der nächste Takt versucht es wieder.
    erg = hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=welt.jetzt + 900)
    assert len(erg) == 1 and erg[0].fehler


def test_zweig_bewegt_seit_dem_lesen_bleibt(
    zweig_welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Löschen nur mit Lease auf den gelesenen Stand — neue Sicherung dazwischen bleibt."""
    w = zweig_welt
    _sicherung(w, monkeypatch, 31, 91)
    echt = hausmeister._zweig_loeschen

    def dazwischen(cache: Path, zweig: str, sha: str) -> None:
        wt = w.heim / "wt" / "wt-31"
        (wt / "dazwischen.txt").write_text("neu\n", encoding="utf-8")
        assert aufpasser.sichern(wt, 31)
        echt(cache, zweig, sha)

    monkeypatch.setattr(hausmeister, "_zweig_loeschen", dazwischen)
    erg = hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=w.jetzt)
    assert [(e.zweig, e.art, e.fehler) for e in erg] == [
        ("sicherung/31", "bleibt", True)
    ]
    assert "sicherung/31" in _zweige(w)


def test_standard_remotes_sind_die_eigenen_github_repos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", raising=False)
    assert hausmeister._sicherung_remotes() == [
        "https://github.com/Shavy72/duoplus-management",
        "https://github.com/Shavy72/claude-skill-to-spawn",
    ]
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", "")
    assert hausmeister._sicherung_remotes() == []
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", "a  https://x/y")
    assert hausmeister._sicherung_remotes() == ["a", "https://x/y"]
