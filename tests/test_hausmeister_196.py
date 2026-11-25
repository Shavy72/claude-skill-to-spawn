"""Hausmeister #196: eingetragene Arbeitsordner außerhalb des konfigurierten Ordners.

Befund: Am Windows-PC entstanden git worktrees eines App-Repos an einem falschen Ort
(``<Laufwerk>:\\c\\dev\\`` statt im konfigurierten Arbeitsordner). Der Hausmeister sah sie
nicht, weil er nur die Wurzel-Muster durchsuchte.

Soll: Die Kandidaten-Suche (:func:`hausmeister._kandidaten`) liefert zusätzlich alle laut
``git worktree list --porcelain`` eingetragenen Arbeitsordner der Hauptrepos; der
Haupt-Arbeitsbaum ist nie Kandidat. Sie laufen durch dieselbe Wegwerf-Prüfung wie alle
anderen. Weg-Tests mit echtem Wegwerf-Git und lokalem bare-„origin“ (Fixture ``welt`` aus
#1368); gestellt sind ``gh`` (Ticket-Zustand) sowie in :func:`haupt_bekannt` die
Windows-Prozessabfrage über die vorhandenen Nähte (#709).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    Welt,
    git,
    welt,
)

from to_spawn import hausmeister, hausmeister_windows, prozessbaum

GEHEIM = "TOKEN=geheim-196-wert"


@pytest.fixture
def haupt_bekannt(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Welt:
    """Das Hauptrepo der Welt ist dem Hausmeister bekannt (wie ``repos[].pfad`` der Maschine).

    Windows-Prozessabfragen laufen über die vorhandenen Nähte mit eigener leerer Prozessliste
    (#709), damit die Tests nur Worktree- und Sicherungsregeln prüfen — nicht fremde Prozesse
    des Rechners.
    """
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", str(welt.haupt))
    monkeypatch.setattr(hausmeister_windows, "_prozesse", list)
    monkeypatch.setattr(prozessbaum, "arbeitsordner_in_windows", lambda _ordner: {})
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


def _windows_zweig(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stellt den Windows-Zweig portabel (auch auf Linux) nach."""
    monkeypatch.setattr(hausmeister, "_PLATTFORM", "win32")
    monkeypatch.setattr(hausmeister_windows, "sys", SimpleNamespace(platform="win32"))
    altbestand = hausmeister.altbestand_pfad()
    altbestand.parent.mkdir(parents=True, exist_ok=True)
    altbestand.write_text('{"freigegeben": false, "ordner": []}\n', encoding="utf-8")


def test_draussen_rundgang_liest_keine_echte_windows_prozessliste(
    haupt_bekannt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#709: ``haupt_bekannt`` liest keine echte Windows-Prozessliste (weder PEB noch PowerShell)."""
    welt = haupt_bekannt
    _windows_zweig(monkeypatch)
    wt = _draussen(welt, 1968)
    (wt / ".env").write_text(GEHEIM + "\n", encoding="utf-8")
    welt.ticket(1968, "CLOSED", 48)
    aufrufe: list[str] = []
    echt_run = subprocess.run

    def _fremde_momentaufnahme() -> dict[int, tuple[int, int]]:
        aufrufe.append("momentaufnahme")
        raise OSError("echte Windows-Momentaufnahme im Test gelesen")

    def _spion_run(befehl: list[str], *a: object, **kw: object) -> subprocess.CompletedProcess[str]:
        if befehl and "powershell" in str(befehl[0]).lower():
            aufrufe.append("powershell")
            raise AssertionError("PowerShell darf im Test nicht laufen")
        return echt_run(befehl, *a, **kw)  # type: ignore[call-overload]

    monkeypatch.setattr(prozessbaum, "_ordner_von", lambda pfad: ((1, 100), rf"\\?\{pfad}"))
    monkeypatch.setattr(prozessbaum, "_momentaufnahme_windows", _fremde_momentaufnahme)
    monkeypatch.setattr(hausmeister_windows.subprocess, "run", _spion_run)

    r = hausmeister.rundgang(loeschen=True, jetzt=welt.jetzt)
    assert r.gelaufen, r.grund
    erg = _ergebnis_fuer(r, wt)
    assert aufrufe == [], f"echte Windows-Prozessabfrage lief: {aufrufe}"
    assert erg.art == "bleibt" and not erg.fehler and ".env" in erg.grund, erg.grund


def test_draussen_eigene_prozessliste_ueber_naht_wirkt(haupt_bekannt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """#709: Rechenlogik bleibt aktiv — eine eingespeiste Prozessliste sperrt den Ordner weiter."""
    welt = haupt_bekannt
    _windows_zweig(monkeypatch)
    wt = _draussen(welt, 1969)
    (wt / ".env").write_text(GEHEIM + "\n", encoding="utf-8")
    welt.ticket(1969, "CLOSED", 48)
    monkeypatch.setattr(
        prozessbaum,
        "arbeitsordner_in_windows",
        lambda _ordner: {4228: prozessbaum.ArbeitsordnerBefund(str(wt), True)},
    )
    r = hausmeister.rundgang(loeschen=True, jetzt=welt.jetzt)
    assert r.gelaufen, r.grund
    erg = _ergebnis_fuer(r, wt)
    assert erg.art == "bleibt" and not erg.fehler and "4228" in erg.grund, erg.grund
