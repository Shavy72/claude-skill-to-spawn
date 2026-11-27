"""#269: Bau-Session wartet laut eigener Meldung auf Blocker, die schon erledigt sind.

Die Session prüft mit ``to_spawn.py blocker <N>`` und schläft bei ``WARTET: …`` 20 min
(ScheduleWakeup). Wird der Blocker in dieser Zeit fertig, wartet sie zu Unrecht. Der
Aufpasser liest ihre letzte Blocker-Zeile im Fenster und fragt dieselbe Regel
(``gh.blocker_gruende``): leer ⇒ Vorfall ``blocker_falsch_gewartet`` mit Ticketnummern
und sofort ein Anstoß ins Fenster. Wartet sie zu Recht, passiert nichts.

Echt: tmux, git, Aufpasser-Lauf; ersetzt nur ``gh`` (externer Dienst, ``gh_stub``).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem Erst-Test und wird als Parameter genannt.
from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from test_aufpasser_236 import (  # noqa: F401 — ``welt`` ist ein Fixture
    Welt,
    git,
    pane_text,
    welt,
)

from to_spawn import aufpasser, bau_log

TICKET = 9002
BLOCKER = 9010
FENSTER = f"bau {TICKET}"


@pytest.fixture()
def welt_269(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Iterator[Welt]:
    """Ticket 9002 offen, Blocker 9010 zu; Vorfälle landen im Repo der Welt."""
    for name in bau_log.RUECKFALL_VARIABLEN:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GH_STUB_BLOCKER", f"{TICKET}:{BLOCKER}")
    yield welt


def _commit_auf_master(welt: Welt, betreff: str) -> None:
    git(welt.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", betreff)
    git(welt.repo, "push", "-q", "origin", "master")


def _fenster_mit(welt: Welt, zeile: str) -> None:
    """Fenster zeigt die letzte Ausgabe des Blocker-Befehls wie im Claude-Pane, dann Ruhe."""
    welt.fenster_still(TICKET, f"bash -c 'printf \"  ⎿  {zeile}\\n\"; exec cat'")


def _vorfaelle(welt: Welt) -> list[dict]:
    zeilen = bau_log.lese(welt.repo, TICKET)
    return [z for z in zeilen if z.get("typ") == "vorfall" and z.get("regel") == "blocker_falsch_gewartet"]


def test_wartet_auf_erledigten_blocker_vorfall_und_anstoss(welt_269: Welt) -> None:
    welt = welt_269
    _commit_auf_master(welt, f"feat: Vorgänger (#{BLOCKER}) [skip ci]")
    _fenster_mit(welt, f"WARTET: #{BLOCKER} offen")
    assert welt.lauf() == 0
    time.sleep(1)
    vorfaelle = _vorfaelle(welt)
    assert len(vorfaelle) == 1, welt.log()
    assert f"#{BLOCKER}" in vorfaelle[0]["beispiel"]
    assert f"#{TICKET}" in vorfaelle[0]["beispiel"]
    assert f"to_spawn.py blocker {TICKET}" in pane_text(FENSTER), pane_text(FENSTER)


def test_blocker_ohne_commit_wartet_zu_recht(welt_269: Welt) -> None:
    welt = welt_269
    _fenster_mit(welt, f"WARTET: #{BLOCKER} zu, aber kein Commit")
    assert welt.lauf() == 0
    time.sleep(1)
    assert _vorfaelle(welt) == []
    assert "to_spawn.py blocker" not in pane_text(FENSTER)


def test_frei_gemeldet_kein_vorfall(welt_269: Welt) -> None:
    welt = welt_269
    _commit_auf_master(welt, f"feat: Vorgänger (#{BLOCKER}) [skip ci]")
    _fenster_mit(welt, "FREI")
    assert welt.lauf() == 0
    time.sleep(1)
    assert _vorfaelle(welt) == []
    assert "to_spawn.py blocker" not in pane_text(FENSTER)


@pytest.mark.parametrize(
    ("text", "erwartet"),
    [
        ("  ⎿  WARTET: #5 offen; #6 offen\n", "#5 offen; #6 offen"),
        ("  ⎿  WARTET: #5 offen\n…\n  ⎿  FREI\n", None),
        ("  ⎿  FREI\n…\n  ⎿  WARTET: #7 offen\n", "#7 offen"),
        ("Ticket #5 wartet (Blocker offen)\n", None),
        ("Session schreibt: WARTET: #5 offen heißt …\n", None),
    ],
)
def test_gemeldete_blocker(text: str, erwartet: str | None) -> None:
    assert aufpasser.gemeldete_blocker(text) == erwartet


def _gh_blocked_by_ersetzen(welt: Welt, verhalten: str) -> None:
    """``gh api …/blocked_by`` antwortet nach ``verhalten``, alles andere bleibt beim ``gh_stub``.

    ``fehler`` = Exit 1, ``kaputt`` = kein JSON, ``offen`` = Blocker noch offen.
    """
    import sys

    from conftest import attrappe
    from test_aufpasser_236 import HILFEN

    attrappe(
        welt.bin,
        "gh",
        f"""
        import json, os, sys
        if any("dependencies/blocked_by" in a for a in sys.argv[1:]):
            verhalten = {verhalten!r}
            if verhalten == "fehler":
                print("HTTP 502: Bad Gateway", file=sys.stderr)
                sys.exit(1)
            if verhalten == "kaputt":
                print("<html>kein JSON</html>")
                sys.exit(0)
            print(json.dumps([{{"number": {BLOCKER}, "state": "open"}}]))
            sys.exit(0)
        os.execv({sys.executable!r}, [{sys.executable!r}, {str(HILFEN / "gh_stub.py")!r}, *sys.argv[1:]])
        """,
    )


@pytest.mark.parametrize("verhalten", ["fehler", "kaputt"])
def test_blocker_abfrage_scheitert_kein_vorfall_kein_anstoss(welt_269: Welt, verhalten: str) -> None:
    """GitHub-Abfrage scheitert ⇒ Lage unbekannt: kein Vorfall, kein Anstoß, Lauf endet sauber."""
    welt = welt_269
    _commit_auf_master(welt, f"feat: Vorgänger (#{BLOCKER}) [skip ci]")
    _gh_blocked_by_ersetzen(welt, verhalten)
    _fenster_mit(welt, f"WARTET: #{BLOCKER} offen")
    assert welt.lauf() == 0
    time.sleep(1)
    assert _vorfaelle(welt) == [], welt.log()
    assert "to_spawn.py blocker" not in pane_text(FENSTER)
    assert not welt.eintrag(FENSTER).get("stupser")


def test_blocker_wirklich_offen_kein_vorfall_kein_anstoss(welt_269: Welt) -> None:
    welt = welt_269
    _commit_auf_master(welt, f"feat: Vorgänger (#{BLOCKER}) [skip ci]")
    _gh_blocked_by_ersetzen(welt, "offen")
    _fenster_mit(welt, f"WARTET: #{BLOCKER} offen")
    assert welt.lauf() == 0
    time.sleep(1)
    assert _vorfaelle(welt) == [], welt.log()
    assert "to_spawn.py blocker" not in pane_text(FENSTER)
    assert not welt.eintrag(FENSTER).get("stupser")


def test_zweiter_takt_kein_zweiter_vorfall(welt_269: Welt) -> None:
    """Zweiter Lauf am selben Ticket: Vorfall steht schon im Bau-Log, nur ein weiterer Anstoß."""
    welt = welt_269
    _commit_auf_master(welt, f"feat: Vorgänger (#{BLOCKER}) [skip ci]")
    _fenster_mit(welt, f"WARTET: #{BLOCKER} offen")
    assert welt.lauf() == 0
    time.sleep(1)
    assert welt.lauf() == 0
    time.sleep(1)
    assert len(_vorfaelle(welt)) == 1, welt.log()
    assert len(welt.eintrag(FENSTER)["stupser"]) == 2, welt.eintrag(FENSTER)


def test_stupser_max_erreicht_kein_weiterer_anstoss(welt_269: Welt) -> None:
    """Grenze R5: nach ``STUPSER_MAX`` Anstupsern stößt der Blocker-Weg nicht mehr an."""
    welt = welt_269
    _commit_auf_master(welt, f"feat: Vorgänger (#{BLOCKER}) [skip ci]")
    _fenster_mit(welt, f"WARTET: #{BLOCKER} offen")
    jetzt = time.time()
    welt.stand_setzen(
        FENSTER,
        hash=welt.hash_von(FENSTER),
        seit=jetzt,
        stufe=0,
        stupser=[jetzt - 60] * aufpasser.STUPSER_MAX,
    )
    assert welt.lauf() == 0
    time.sleep(1)
    assert "to_spawn.py blocker" not in pane_text(FENSTER), pane_text(FENSTER)
    assert len(welt.eintrag(FENSTER)["stupser"]) == aufpasser.STUPSER_MAX, welt.eintrag(FENSTER)
