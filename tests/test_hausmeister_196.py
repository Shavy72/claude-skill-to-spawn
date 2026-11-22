"""Hausmeister #196: eingetragene Arbeitsordner außerhalb des konfigurierten Ordners.

Befund: Am Windows-PC entstanden git worktrees eines App-Repos an einem falschen Ort
(``<Laufwerk>:\\c\\dev\\`` statt im konfigurierten Arbeitsordner). Der Hausmeister sah sie
nicht, weil er nur die Wurzel-Muster durchsuchte.

Soll: Die Kandidaten-Suche (:func:`hausmeister._kandidaten`) liefert zusätzlich alle laut
``git worktree list --porcelain`` eingetragenen Arbeitsordner der Hauptrepos; der
Haupt-Arbeitsbaum ist nie Kandidat. Sie laufen durch dieselbe Wegwerf-Prüfung wie alle
anderen. Weg-Tests mit echtem Wegwerf-Git und lokalem bare-„origin“ (Fixture ``welt`` aus
#1368); gestellt ist nur ``gh`` (Ticket-Zustand).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    Welt,
    git,
    welt,
)

from to_spawn import hausmeister

GEHEIM = "TOKEN=geheim-196-wert"


@pytest.fixture(autouse=True)
def eigenes_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Eigener tmux-Server je Test: die echten Sitzungen des Bau-Servers zählen nicht."""
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))


@pytest.fixture
def haupt_bekannt(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Welt:
    """Das Hauptrepo der Welt ist dem Hausmeister bekannt (wie ``repos[].pfad`` der Maschine)."""
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", str(welt.haupt))
    return welt


def _draussen(welt: Welt, nr: int) -> Path:
    """Echter worktree des Hauptrepos außerhalb aller Wurzeln (wie ``C:\\c\\dev\\wt-<nr>``)."""
    wt = welt.tmp / "c" / "dev" / f"wt-{nr}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    zweig = f"ticket/{nr}-test"
    git(welt.haupt, "worktree", "add", "-q", "-b", zweig, str(wt))
    (wt / "arbeit.txt").write_text("fertig\n", encoding="utf-8")
    git(wt, "add", "arbeit.txt")
    git(wt, "commit", "-q", "-m", "arbeit")
    git(wt, "push", "-q", "-u", "origin", zweig)
    return wt


def _gleich(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def _ergebnis_fuer(rundgang: hausmeister.Rundgang, ordner: Path) -> hausmeister.Ergebnis:
    treffer = [e for e in rundgang.ergebnisse if _gleich(e.ordner, ordner)]
    assert len(treffer) == 1, [str(e.ordner) for e in rundgang.ergebnisse]
    return treffer[0]


def test_draussen_ist_kandidat_haupt_nie(haupt_bekannt: Welt) -> None:
    welt = haupt_bekannt
    wt = _draussen(welt, 1961)
    drinnen = welt.worktree(1962)
    kandidaten = hausmeister._kandidaten()
    assert sum(_gleich(k, wt) for k in kandidaten) == 1, kandidaten
    # ohne Doppelte: der eingetragene Ordner im Wurzel-Muster kommt nur einmal
    assert sum(_gleich(k, drinnen) for k in kandidaten) == 1, kandidaten
    assert not any(_gleich(k, welt.haupt) for k in kandidaten), kandidaten


def test_draussen_ticket_zu_sauber_wird_geraeumt(haupt_bekannt: Welt) -> None:
    welt = haupt_bekannt
    wt = _draussen(welt, 1963)
    welt.ticket(1963, "CLOSED", 48)
    r = hausmeister.rundgang(loeschen=True, jetzt=welt.jetzt)
    assert r.gelaufen, r.grund
    erg = _ergebnis_fuer(r, wt)
    assert erg.art == "gelöscht" and not erg.trocken, erg.grund
    assert not wt.exists()
    assert str(wt) not in git(welt.haupt, "worktree", "list")
    assert welt.haupt.is_dir() and (welt.haupt / "README.md").is_file()


def test_draussen_mit_ungesichertem_bleibt_mit_grund(haupt_bekannt: Welt) -> None:
    welt = haupt_bekannt
    wt = _draussen(welt, 1964)
    # nicht sicherbar: Geheimnis-Datei, die das Hauptrepo nicht hat
    (wt / ".env").write_text(GEHEIM + "\n", encoding="utf-8")
    welt.ticket(1964, "CLOSED", 48)
    r = hausmeister.rundgang(loeschen=True, jetzt=welt.jetzt)
    assert r.gelaufen, r.grund
    erg = _ergebnis_fuer(r, wt)
    assert erg.art == "bleibt" and ".env" in erg.grund, erg.grund
    assert (wt / ".env").read_text(encoding="utf-8") == GEHEIM + "\n"
    zeilen = [z for z in welt.zeilen() if "wt-1964" in str(z.get("ordner", ""))]
    assert zeilen and ".env" in str(zeilen[-1].get("grund", "")), welt.zeilen()


def test_draussen_ticket_offen_bleibt(haupt_bekannt: Welt) -> None:
    welt = haupt_bekannt
    wt = _draussen(welt, 1965)
    welt.ticket(1965, "OPEN")
    r = hausmeister.rundgang(loeschen=True, jetzt=welt.jetzt)
    erg = _ergebnis_fuer(r, wt)
    assert erg.art == "bleibt", erg.grund
    assert (wt / "arbeit.txt").is_file()


def test_haupt_arbeitsbaum_nie_im_bereich(haupt_bekannt: Welt) -> None:
    welt = haupt_bekannt
    _draussen(welt, 1966)
    assert not hausmeister._im_bereich(welt.haupt)
    r = welt.aufraeumen(welt.haupt, loeschen=True)
    assert r.art == "bleibt", r.grund
    assert (welt.haupt / "README.md").is_file()


def test_ohne_bekanntes_hauptrepo_bleibt_draussen_unsichtbar(welt: Welt) -> None:
    """Ohne Hauptrepo-Liste (Test-Standard leer) ändert sich nichts: keine fremden Ordner."""
    wt = _draussen(welt, 1967)
    assert not any(_gleich(k, wt) for k in hausmeister._kandidaten())
    assert not hausmeister._im_bereich(wt)
