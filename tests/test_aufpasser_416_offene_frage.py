"""#416: Ein Bau-Fenster mit offener Frage im Briefkasten bleibt stehen.

Lage aus dem Probe-Lauf (24.11.): Ticket hat ``staging:fertig`` (Issue noch OPEN), die
Bau-Session wartet auf die Antwort des Aufsehers (``.to-spawn/entscheidung-<N>.json`` mit
``antwort: null``), die Grenze ist voll. Bisher schloss der Aufpasser das Fenster nach 30 s
Gegenprobe — danach schloss niemand mehr das Issue.

(a) staging:fertig + offene Frage + Platznot ⇒ Fenster bleibt, Log-Zeile.
(b) gleiche Lage ohne offene Frage ⇒ wird wie bisher geschlossen.
(c) Issue CLOSED + alte offene Frage ⇒ darf schließen (keine ewig haltende Frage).
(d) Briefkasten-Lesen wirft ⇒ Fenster bleibt (fail-safe), Log-Zeile.
(e) kaputte Briefkasten-Datei ⇒ Fenster bleibt (Sperrgrund gilt als offene Frage).

Nur Linux mit tmux (wie ``test_aufpasser_236``).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem Erst-Test und wird als Parameter genannt.
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator

import pytest
from test_aufpasser_236 import (  # noqa: F401 — ``welt`` ist ein Fixture
    SPEC,
    Welt,
    fenster_namen,
    git,
    welt,
)
from test_aufpasser_platz_592 import _grenze, _lauf_kurze_karenz

from to_spawn import briefkasten, sessions_datei
from to_spawn.waechter_lauf import transkript_ordner

pytestmark = pytest.mark.braucht("tmux")  # (#68)

TICKET = 9003
FENSTER = f"bau {TICKET}"


@pytest.fixture()
def staging_9003(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Iterator[Welt]:
    """Ticket 9003 OPEN mit ``staging:fertig``, volle Grenze, idle Fake-Claude im Fenster."""
    daten = welt.tmp / "gh_daten.json"
    daten.write_text(json.dumps({str(TICKET): {"labels": ["staging:fertig"]}}), encoding="utf-8")
    monkeypatch.setenv("GH_STUB_DATEN", str(daten))
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:{TICKET}")
    monkeypatch.delenv("GH_STUB_ZU", raising=False)
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    _grenze(welt, monkeypatch, max_sessions=2)
    yield welt


def _frage_einwerfen(welt: Welt) -> str:
    """Echter Weg der Bau-Session: Frage an den Aufseher, noch ohne Antwort."""
    return briefkasten.einwerfen(
        TICKET,
        "Zweig per fast-forward nach master bringen?",
        "ja",
        3,
        ["Probe-Lauf 24.11."],
        hauptbaum=welt.repo,
    )


def _geschlossen(welt: Welt) -> bool:
    return any(f"„{FENSTER}“ geschlossen" in k for k in welt.kommentare())


def test_offene_frage_haelt_fenster_trotz_platznot(staging_9003: Welt) -> None:
    welt = staging_9003
    _frage_einwerfen(welt)
    welt.fenster_fake_claude(TICKET, str(uuid.uuid4()))
    assert _lauf_kurze_karenz(welt) == 0
    time.sleep(0.5)
    assert FENSTER in fenster_namen(), welt.log()
    assert not _geschlossen(welt), welt.kommentare()
    assert "offene Frage im Briefkasten" in welt.log()


def test_ohne_offene_frage_schliesst_wie_bisher(staging_9003: Welt) -> None:
    welt = staging_9003
    welt.fenster_fake_claude(TICKET, str(uuid.uuid4()))
    assert _lauf_kurze_karenz(welt) == 0
    time.sleep(0.5)
    assert FENSTER not in fenster_namen(), welt.log()
    assert _geschlossen(welt), welt.log()


def test_beantwortete_frage_haelt_nicht(staging_9003: Welt) -> None:
    welt = staging_9003
    fid = _frage_einwerfen(welt)
    briefkasten.antworten(TICKET, fid, "ja", ["Test"], "Zweig ist grün", hauptbaum=welt.repo)
    welt.fenster_fake_claude(TICKET, str(uuid.uuid4()))
    assert _lauf_kurze_karenz(welt) == 0
    time.sleep(0.5)
    assert FENSTER not in fenster_namen(), welt.log()
    assert _geschlossen(welt), welt.log()


def test_issue_zu_alte_frage_darf_schliessen(staging_9003: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    welt = staging_9003
    monkeypatch.setenv("GH_STUB_ZU", str(TICKET))
    _frage_einwerfen(welt)
    welt.fenster_fake_claude(TICKET, str(uuid.uuid4()))
    assert _lauf_kurze_karenz(welt) == 0
    time.sleep(0.5)
    assert FENSTER not in fenster_namen(), welt.log()
    assert any(f"„{FENSTER}“ geschlossen (Ticket zu)." in k for k in welt.kommentare())


def test_briefkasten_wirft_fenster_bleibt(staging_9003: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    welt = staging_9003

    def wirft(*_a: object, **_k: object) -> list[dict[str, str]]:
        raise OSError("Briefkasten nicht lesbar (Test)")

    monkeypatch.setattr(briefkasten, "offene", wirft)
    welt.fenster_fake_claude(TICKET, str(uuid.uuid4()))
    assert _lauf_kurze_karenz(welt) == 0
    time.sleep(0.5)
    assert FENSTER in fenster_namen(), welt.log()
    assert not _geschlossen(welt), welt.kommentare()
    assert "Briefkasten nicht lesbar" in welt.log()


def test_kaputte_briefkasten_datei_haelt_fenster(staging_9003: Welt) -> None:
    welt = staging_9003
    datei = welt.repo / briefkasten.datei_relativ(TICKET)
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("{kaputt", encoding="utf-8")
    welt.fenster_fake_claude(TICKET, str(uuid.uuid4()))
    assert _lauf_kurze_karenz(welt) == 0
    time.sleep(0.5)
    assert FENSTER in fenster_namen(), welt.log()
    assert not _geschlossen(welt), welt.kommentare()


# Fix-Runde 1 (Review PR #436): die offene Frage verhindert nur das Schließen, nicht die
# Behandlung einer beendeten Session.


def test_tote_session_mit_offener_frage_laeuft_in_stufenkette(staging_9003: Welt) -> None:
    """(f) Pane nur noch Shell (Claude beendet) + offene Frage ⇒ wie beendete Session:
    ohne Gesprächs-ID „braucht den Menschen“, statt ewig still stehen zu bleiben."""
    from test_aufpasser_236_fix import _fenster, _still_seit

    welt = staging_9003
    _frage_einwerfen(welt)
    _fenster(welt, FENSTER, "bash --norc --noprofile")
    _still_seit(welt, FENSTER, 100, stufe=0)
    assert welt.lauf() == 0
    assert FENSTER in fenster_namen(), welt.log()
    assert not _geschlossen(welt), welt.kommentare()
    treffer = [k for k in welt.kommentare() if "Session beendet, keine Gesprächs-ID — braucht den Menschen" in k]
    assert len(treffer) == 1, (welt.kommentare(), welt.log())
    assert welt.eintrag(FENSTER)["stufe"] == 3


def test_lebende_session_mit_offener_frage_bleibt_stehen(staging_9003: Welt) -> None:
    """(g) Claude lebt, wartet auf die Antwort, lange still ⇒ kein Schließen, kein
    Anstupsen, kein Fortsetzen — das Fenster bleibt unberührt."""
    from test_aufpasser_236_fix import _still_seit

    welt = staging_9003
    _frage_einwerfen(welt)
    welt.fenster_fake_claude(TICKET, str(uuid.uuid4()))
    _still_seit(welt, FENSTER, 100, stufe=0)
    assert welt.lauf() == 0
    time.sleep(0.5)
    assert FENSTER in fenster_namen(), welt.log()
    assert not _geschlossen(welt), welt.kommentare()
    assert not any("angestupst" in k or "fortgesetzt" in k for k in welt.kommentare())
    assert welt.eintrag(FENSTER).get("stufe", 0) == 0
    assert "offene Frage im Briefkasten" in welt.log()


# --- #440: Entprellung der Log-Zeile für gehaltenes Fenster + Fortsetz-Pfad ------------------


def test_gehaltenes_fenster_loggt_erst_bei_eintritt_und_aenderung(staging_9003: Welt) -> None:
    """#440: Ein gehaltenes Fenster schreibt die Log-Zeile beim Eintritt, nicht in jedem Takt;
    erst eine Änderung (weitere/andere Frage) schreibt wieder eine Zeile."""
    welt = staging_9003
    fid1 = _frage_einwerfen(welt)
    welt.fenster_fake_claude(TICKET, str(uuid.uuid4()))

    assert _lauf_kurze_karenz(welt) == 0
    zeilen = [z for z in welt.log().splitlines() if "offene Frage im Briefkasten" in z]
    assert len(zeilen) == 1 and fid1 in zeilen[0], zeilen

    # Zweiter Takt unverändert: keine weitere Log-Zeile.
    assert _lauf_kurze_karenz(welt) == 0
    zeilen = [z for z in welt.log().splitlines() if "offene Frage im Briefkasten" in z]
    assert len(zeilen) == 1, zeilen

    # Änderung (zweite offene Frage kommt dazu): eine neue Zeile.
    fid2 = _frage_einwerfen(welt)
    assert _lauf_kurze_karenz(welt) == 0
    zeilen = [z for z in welt.log().splitlines() if "offene Frage im Briefkasten" in z]
    assert len(zeilen) == 2 and fid2 in zeilen[1], zeilen

    # Noch ein Takt mit gleichem Stand: bleibt bei zwei Zeilen.
    assert _lauf_kurze_karenz(welt) == 0
    zeilen = [z for z in welt.log().splitlines() if "offene Frage im Briefkasten" in z]
    assert len(zeilen) == 2, zeilen


def test_briefkasten_wirft_loggt_entprellt(staging_9003: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """#440: Auch die Warnung bei unlesbarem Briefkasten kommt nur beim Eintritt und bei Änderung."""
    welt = staging_9003
    grund = ["Briefkasten nicht lesbar (Test A)"]

    def wirft(*_a: object, **_k: object) -> list[dict[str, str]]:
        raise OSError(grund[0])

    monkeypatch.setattr(briefkasten, "offene", wirft)
    welt.fenster_fake_claude(TICKET, str(uuid.uuid4()))

    assert _lauf_kurze_karenz(welt) == 0
    assert _lauf_kurze_karenz(welt) == 0
    zeilen = [z for z in welt.log().splitlines() if "Briefkasten nicht lesbar" in z]
    assert len(zeilen) == 1 and "Test A" in zeilen[0], zeilen

    grund[0] = "Briefkasten nicht lesbar (Test B)"
    assert _lauf_kurze_karenz(welt) == 0
    zeilen = [z for z in welt.log().splitlines() if "Briefkasten nicht lesbar" in z]
    assert len(zeilen) == 2 and "Test B" in zeilen[1], zeilen


def test_tote_session_mit_offener_frage_wird_fortgesetzt(staging_9003: Welt) -> None:
    """#440 (Review RÜCKLAUF163-R436b): Pane nur noch Shell + offene Frage + gespeicherte
    Gesprächs-ID mit Transkript ⇒ Fenster wird nicht geschlossen, sondern mit ``--resume``
    fortgesetzt, und der schmutzige Worktree vorher gesichert."""
    from test_aufpasser_236_fix import _bau_claude_vorlage, _fenster, _still_seit

    welt = staging_9003
    welt.worktree(TICKET, schmutzig=True)
    _frage_einwerfen(welt)
    sid = str(uuid.uuid4())
    sessions_datei.schreiben(welt.repo, str(TICKET), sid, welt.repo, 1)
    ordner = transkript_ordner(welt.repo, welt.heim)
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / f"{sid}.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")

    _fenster(welt, FENSTER, "bash --norc --noprofile")
    _still_seit(welt, FENSTER, 100, stufe=0)

    assert welt.lauf("--bau-vorlage", _bau_claude_vorlage(welt)) == 0
    assert FENSTER in fenster_namen(), welt.log()
    assert not _geschlossen(welt), welt.kommentare()
    assert welt.protokoll_bau.read_text(encoding="utf-8").splitlines() == [f"{TICKET} --resume {sid}"]
    assert git(welt.origin, "branch", "--list", f"sicherung/{TICKET}").strip()
    assert welt.eintrag(FENSTER)["stufe"] == 2
    assert any("fortgesetzt" in k and sid[:8] in k for k in welt.kommentare()), welt.kommentare()
