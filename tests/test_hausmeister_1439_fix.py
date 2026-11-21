"""Hausmeister #1439, Fixrunde Prüfpanel: Lease nur auf den geprüften Stand, Log-Absicht,
Warnung bei verworfenem Zweig-Ergebnis, Probelauf ohne Log, ein Fetch je Lauf, saubere
Zweig-Einträge in ``liste_fuer_david``.

Weg-Tests mit echtem Wegwerf-Git (Fixture ``welt`` aus ``test_hausmeister_1368``); gestellt
sind nur gh (Ticket-Stand) und die Remote-/Hauptrepo-Liste.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from conftest import attrappe
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    ORIGIN,
    Welt,
    git,
    welt,
)

from to_spawn import hausmeister

TAG = 24 * 3600.0


@pytest.fixture
def zw(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Welt:
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", ORIGIN)
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", str(welt.haupt))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN", "1")  # #1491: Löschen nur mit Schalter
    return welt


def _heads(w: Welt) -> list[str]:
    return sorted(w.im_bare("for-each-ref", "--format=%(refname:short)").split())


def _commit(ordner: Path, datei: str, text: str) -> str:
    (ordner / datei).write_text(text, encoding="utf-8")
    git(ordner, "add", datei)
    git(ordner, "commit", "-q", "-m", f"c {datei}")
    return git(ordner, "rev-parse", "HEAD").strip()


def _arten(w: Welt, zweig: str) -> list[str]:
    return [z["art"] for z in w.zeilen() if z.get("zweig") == zweig]


def test_nachschieben_zwischen_pruefung_und_loeschen_zweig_und_commit_bleiben(
    zw: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Befund 1: ein paralleler Lauf holt den nachgeschobenen Stand in den Zwischenspeicher —
    gelöscht werden darf trotzdem nur der geprüfte Stand."""
    wt = zw.worktree(60)
    git(
        wt, "push", "-q", "origin", "HEAD:master"
    )  # alles auf master ⇒ keine Sicherung nötig
    zw.ticket(60, "CLOSED", vor_stunden=48)
    echt = hausmeister._ticket_daten
    neu: list[str] = []

    def dazwischen(nr: int) -> object:
        daten = echt(nr)
        neu.append(_commit(wt, "nachgeschoben.txt", "neu\n"))
        git(wt, "push", "-q", "origin", "ticket/60-test")
        hausmeister._zweig_holen(
            hausmeister._zweig_ort(ORIGIN, "remote")
        )  # paralleler Lauf
        return daten

    monkeypatch.setattr(hausmeister, "_ticket_daten", dazwischen)
    r = hausmeister.zweig_aufraeumen(ORIGIN, "ticket/60-test", loeschen=True)
    assert r.art == "bleibt", r.grund
    assert "ticket/60-test" in _heads(zw)
    assert zw.im_bare("rev-parse", "refs/heads/ticket/60-test").strip() == neu[0]
    assert "gelöscht" not in _arten(zw, "ticket/60-test")


def test_loeschen_scheitert_log_zeigt_absicht_statt_geloescht(zw: Welt) -> None:
    """Befund 2: die Zeile vor dem Löschen ist eine Absicht („löscht“), nie „gelöscht“."""
    wt = zw.worktree(61)
    git(wt, "push", "-q", "origin", "HEAD:master")
    zw.ticket(61, "CLOSED", vor_stunden=48)
    haken = zw.bare / "hooks" / "pre-receive"
    attrappe(zw.bare / "hooks", "pre-receive", 'import sys; print("nein", file=sys.stderr); sys.exit(1)\n')
    r = hausmeister.zweig_aufraeumen(ORIGIN, "ticket/61-test", loeschen=True)
    assert r.art == "bleibt" and r.fehler
    assert "ticket/61-test" in _heads(zw)
    assert _arten(zw, "ticket/61-test") == ["löscht", "bleibt"]
    haken.unlink()
    r = hausmeister.zweig_aufraeumen(ORIGIN, "ticket/61-test", loeschen=True)
    assert r.art == "gelöscht"
    assert _arten(zw, "ticket/61-test")[-2:] == ["löscht", "gelöscht"]
    assert hausmeister.liste_fuer_david(zw.log) == []


def test_worktree_loeschen_warnt_bei_bleibendem_zweig_fuer_david(
    zw: Welt, caplog: pytest.LogCaptureFixture
) -> None:
    """Befund 3: ``_loeschen`` verwirft das Zweig-Ergebnis nicht still."""
    wt = zw.heim / "wt" / "wt-ohne"
    git(zw.haupt, "worktree", "add", "-q", "-b", "ticket/ohne-nummer", str(wt))
    with caplog.at_level(logging.WARNING, logger="to_spawn.hausmeister"):
        hausmeister._loeschen(wt, True)
    assert not wt.exists()
    treffer = [m for m in caplog.messages if "ticket/ohne-nummer" in m]
    assert treffer and str(wt) in treffer[0]


def test_probelauf_schreibt_kein_hausmeister_log(zw: Welt, tmp_path: Path) -> None:
    """Befund 4: Probelauf-Funde gehören nicht ins Log und nicht in ``liste_fuer_david``."""
    git(zw.haupt, "branch", "ticket/ohne-nummer")
    git(zw.haupt, "push", "-q", "origin", "ticket/ohne-nummer")
    zw.worktree(62)
    zw.ticket(62, "CLOSED", vor_stunden=48)
    pfad = hausmeister.zweige_probelauf(tmp_path / "probe.txt")
    assert "ohne-nummer" in pfad.read_text(encoding="utf-8")
    assert zw.zeilen() == []
    assert hausmeister.liste_fuer_david(zw.log) == []


def test_taeglicher_lauf_holt_je_repo_und_remote_einmal(
    zw: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Befund 5: drei Remote-Zweige, ein Hauptrepo ⇒ genau zwei Fetches (kein N+1)."""
    for nr in (63, 64, 65):
        wt = zw.worktree(nr)
        zw.ticket(nr, "OPEN")
        git(wt, "checkout", "-q", "--detach")  # lokal frei ⇒ auch lokal geprüft
    echt = hausmeister.aufpasser.git_lauf
    fetches: list[tuple[str, ...]] = []

    def zaehlen(cwd: Path, *args: str, **kw: object) -> str:
        if "fetch" in args:
            fetches.append(args)
        return echt(cwd, *args, **kw)

    monkeypatch.setattr(hausmeister.aufpasser, "git_lauf", zaehlen)
    erg = hausmeister.zweige_aufraeumen(loeschen=False, jetzt=zw.jetzt)
    geprueft = [e for e in erg if e.zweig.startswith("ticket/6")]
    assert len(geprueft) == 6 and all(
        e.art == "bleibt" and not e.fehler for e in geprueft
    )
    assert len(fetches) == 2, fetches
    fetches.clear()
    hausmeister.zweig_aufraeumen(ORIGIN, "ticket/63-test")  # Tür einzeln holt selbst
    assert len(fetches) == 1


def test_liste_fuer_david_zweig_mit_url_unverstuemmelt(zw: Welt) -> None:
    """Befund 6: ``https://`` bleibt im Zweig-Eintrag ganz."""
    zeile = {
        "remote": "https://github.com/x/y",
        "zweig": "ticket/ohne-nummer",
        "fuer_david": True,
    }
    zw.log.parent.mkdir(parents=True, exist_ok=True)
    zw.log.write_text(json.dumps(zeile) + "\n", encoding="utf-8")
    assert hausmeister.liste_fuer_david(zw.log) == [
        "https://github.com/x/y ticket/ohne-nummer"
    ]
