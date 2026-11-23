"""Steh-Erkennung löst den Entscheider-Helfer aus (Skill-Issue #372, Lastenheft L9 A60).

Zeigt ein Bau-Fenster eine Rückfrage (oder ist still und endet mit einer Frage an den
Menschen), liefert die Leiter statt ``nichts`` genau einen Auftrag: Frage in den Briefkasten,
Entscheider-Helfer beantwortet sie. Klasse 2 (Live, Kosten, Konten echter Personen) wird nur
durchgereicht. Gestellt ist nur die Außenwelt über die Naht ``umwelt``; Leitstand und
Briefkasten laufen echt im tmp-Ordner.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufseher_stand, briefkasten, leiter, ticket_zustand

SPEC = 9399
TICKET = 9372
ZIEL = "=spec-9399:4"
FRAGE = "Soll ich die alten Test-Dateien unter archive/ löschen?"
FRAGE_LIVE = "Soll ich den Stand jetzt live deployen?"


class FakeWerkzeug:
    def __init__(self) -> None:
        self.getippt: list[tuple[str, str]] = []

    def tippen(self, ziel: str, text: str) -> None:
        self.getippt.append((ziel, text))


class FakeUmwelt:
    """Lage nach Vorgabe; ``frage_einwerfen`` geht in den echten Briefkasten (tmp-Hauptbaum)."""

    def __init__(
        self,
        hauptbaum: Path,
        *,
        fenster: str = aufseher_stand.RUECKFRAGE,
        frage: str = FRAGE,
        still_min: int | None = 5,
        stufe: ticket_zustand.Stufe | None = "offen",
    ) -> None:
        self.hauptbaum = hauptbaum
        self.fenster = fenster
        self.frage = frage
        self.still_min = still_min
        self.stufe = stufe
        self.uhr = time.time()
        self.wz = FakeWerkzeug()
        self.eingeworfen: list[str] = []

    def jetzt(self) -> float:
        return self.uhr

    def lage(self, spec: int, ticket: int, seit: float | None) -> leiter.Lage:
        return leiter.Lage(
            stufe=self.stufe,
            fenster=self.fenster,
            still_min=self.still_min,
            kontext_k=None,
            prompt_datei=False,
            ziel=ZIEL,
            frage=self.frage,
        )

    def handoff_k(self) -> float:
        return 250.0

    def worktree(self, ticket: int) -> Path:
        return self.hauptbaum

    def werkzeug(self) -> Any:
        return self.wz

    def frage_einwerfen(self, spec: int, ticket: int, frage: str) -> str:
        self.eingeworfen.append(frage)
        return leiter.frage_einwerfen(self.hauptbaum, ticket, frage)


@pytest.fixture
def baum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    return tmp_path


def _lauf(u: FakeUmwelt) -> leiter.Ergebnis:
    return leiter.eingreifen(SKILL, SPEC, TICKET, umwelt=u)


# --- reine Logik -------------------------------------------------------------------------


def _lage(fenster: str, frage: str = "", still: int = 5) -> leiter.Lage:
    return leiter.Lage(stufe="offen", fenster=fenster, still_min=still, kontext_k=None, prompt_datei=False, frage=frage)


def test_rueckfrage_liefert_entscheider_auftrag_statt_nichts() -> None:
    schritt = leiter.entscheide(_lage(aufseher_stand.RUECKFRAGE, FRAGE), leiter.Gemerkt(0, None), time.time(), 250.0)
    assert schritt.aktion == "entscheider"


def test_still_mit_frage_an_den_menschen_loest_aus() -> None:
    schritt = leiter.entscheide(_lage(aufseher_stand.STILL, FRAGE), leiter.Gemerkt(0, None), time.time(), 250.0)
    assert schritt.aktion == "entscheider"


def test_still_ohne_frage_wie_bisher() -> None:
    jetzt = time.time()
    kurz = leiter.entscheide(_lage(aufseher_stand.STILL, "", 5), leiter.Gemerkt(0, None), jetzt, 250.0)
    lang = leiter.entscheide(_lage(aufseher_stand.STILL, "", 25), leiter.Gemerkt(0, None), jetzt, 250.0)
    assert kurz.aktion == "nichts"
    assert lang.aktion == "anstupsen"


def test_rueckfrage_ohne_lesbaren_text_bleibt_nichts() -> None:
    schritt = leiter.entscheide(_lage(aufseher_stand.RUECKFRAGE, ""), leiter.Gemerkt(0, None), time.time(), 250.0)
    assert schritt.aktion == "nichts"


def test_klasse_2_wird_durchgereicht() -> None:
    schritt = leiter.entscheide(
        _lage(aufseher_stand.RUECKFRAGE, FRAGE_LIVE), leiter.Gemerkt(0, None), time.time(), 250.0
    )
    assert schritt.aktion == "mensch"


def test_gemerkte_frage_loest_nicht_erneut_aus() -> None:
    gemerkt = leiter.Gemerkt(0, None, leiter.frage_schluessel(FRAGE))
    schritt = leiter.entscheide(_lage(aufseher_stand.RUECKFRAGE, FRAGE), gemerkt, time.time(), 250.0)
    assert schritt.aktion == "nichts"
    neu = leiter.entscheide(
        _lage(aufseher_stand.RUECKFRAGE, "Welchen Namen soll die Datei haben?"), gemerkt, time.time(), 250.0
    )
    assert neu.aktion == "entscheider"


# --- Tür (eingreifen) --------------------------------------------------------------------


def test_eingreifen_wirft_frage_einmal_in_den_briefkasten(baum: Path) -> None:
    u = FakeUmwelt(baum)
    erst = _lauf(u)
    assert erst.exit == 0
    assert f"Frage von Ticket {TICKET} per Entscheider-Helfer beantworten" in erst.zeile
    offen = briefkasten.offene([TICKET], hauptbaum=baum)
    assert [(f["frage"], f["klasse"]) for f in offen] == [(FRAGE, "3")]
    assert offen[0]["id"] in erst.zeile
    zweit = _lauf(u)  # nächster Takt, dieselbe Frage
    assert zweit.exit == 0
    assert len(briefkasten.offene([TICKET], hauptbaum=baum)) == 1
    assert u.eingeworfen == [FRAGE]
    assert u.wz.getippt == []


def test_eingreifen_klasse_2_braucht_den_menschen(baum: Path) -> None:
    u = FakeUmwelt(baum, frage=FRAGE_LIVE)
    erg = _lauf(u)
    assert "braucht den Menschen" in erg.zeile
    assert "Entscheider-Helfer" not in erg.zeile
    assert [f["klasse"] for f in briefkasten.offene([TICKET], hauptbaum=baum)] == ["2"]
    assert u.wz.getippt == []


def test_eingreifen_ohne_frage_kein_auftrag(baum: Path) -> None:
    u = FakeUmwelt(baum, fenster=aufseher_stand.STILL, frage="", still_min=5)
    erg = _lauf(u)
    assert erg.exit == 0
    assert u.eingeworfen == []
    assert briefkasten.offene([TICKET], hauptbaum=baum) == []


def test_arbeitet_wieder_vergisst_frage(baum: Path) -> None:
    u = FakeUmwelt(baum)
    _lauf(u)
    u.fenster = aufseher_stand.ARBEITET
    _lauf(u)
    assert leiter.gemerkte_frage(TICKET) is None
    u.fenster = aufseher_stand.RUECKFRAGE
    _lauf(u)
    assert u.eingeworfen == [FRAGE, FRAGE]


# --- Fenster-Erkennung (aufseher_stand) ----------------------------------------------------


EINGABE = "─" * 40


def test_frage_im_text_still_endet_mit_frage() -> None:
    text = f"● Tests grün.\n\n● {FRAGE}\n\n{EINGABE}\n❯ \n{EINGABE}\n  Kontext 80k"
    assert aufseher_stand.frage_im_text(text) == f"● {FRAGE}"


def test_frage_im_text_ohne_frage_leer() -> None:
    text = f"● Tests grün, committe jetzt.\n\n{EINGABE}\n❯ \n{EINGABE}\n  Kontext 80k"
    assert aufseher_stand.frage_im_text(text) == ""


def test_frage_im_text_rueckfrage_dialog() -> None:
    text = "● Bash(rm -rf archive/alt)\n\nDo you want to proceed?\n❯ 1. Yes\n  2. No\n\nEsc to cancel"
    frage = aufseher_stand.frage_im_text(text)
    assert "Do you want to proceed?" in frage and "rm -rf archive/alt" in frage
