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
        return leiter.frage_einwerfen(self.hauptbaum, spec, ticket, frage)


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


# --- Fix-Runde: Kürzung darf Klasse 2 nie verlieren (Review PR 374) ----------------------


BERICHT = "● " + " ".join(
    f"Zeile {i}: Tests laufen grün, Diff geprüft, Belege abgelegt und Worktree aufgeräumt." for i in range(7)
)
LANG_LIVE = f"{BERICHT} Soll ich das jetzt live deployen?"


def test_langer_absatz_mit_live_frage_am_ende_ist_klasse_2() -> None:
    zeilen = "\n".join(f"Zeile {i}: Tests laufen grün, Diff geprüft, Belege abgelegt und Worktree aufgeräumt." for i in range(7))
    text = f"● Start.\n\n{zeilen}\nSoll ich das jetzt live deployen?\n\n{EINGABE}\n❯ \n{EINGABE}\n  Kontext 80k"
    frage = aufseher_stand.frage_im_text(text)
    assert frage.endswith("Soll ich das jetzt live deployen?")
    schritt = leiter.entscheide(_lage(aufseher_stand.STILL, frage, 25), leiter.Gemerkt(0, None), time.time(), 250.0)
    assert schritt.aktion == "mensch"


def test_langer_absatz_live_geht_nicht_an_den_entscheider(baum: Path) -> None:
    u = FakeUmwelt(baum, frage=LANG_LIVE)
    erg = _lauf(u)
    assert "braucht den Menschen" in erg.zeile
    offen = briefkasten.offene([TICKET], hauptbaum=baum)
    assert [f["klasse"] for f in offen] == ["2"]
    assert offen[0]["frage"].endswith("Soll ich das jetzt live deployen?")
    assert len(offen[0]["frage"]) <= aufseher_stand.FRAGE_MAX


def test_klasse_2_wort_vorn_bleibt_nach_kuerzen_klasse_2(baum: Path) -> None:
    frage = "Ich würde den Stand live deployen. " + BERICHT + " Passt das so?"
    assert len(frage) > aufseher_stand.FRAGE_MAX
    erg = _lauf(FakeUmwelt(baum, frage=frage))
    assert "braucht den Menschen" in erg.zeile
    offen = briefkasten.offene([TICKET], hauptbaum=baum)
    assert [f["klasse"] for f in offen] == ["2"]
    assert offen[0]["frage"].startswith("…") and offen[0]["frage"].endswith("Passt das so?")


def test_frage_kurz_behaelt_ende() -> None:
    assert aufseher_stand.frage_kurz("kurz?") == "kurz?"
    lang = aufseher_stand.frage_kurz("a " * 400 + "Ende?")
    assert len(lang) == aufseher_stand.FRAGE_MAX and lang.endswith("Ende?") and lang.startswith("…")


# --- Fix-Runde: kein Doppel-Einwurf, Merken scheitert ------------------------------------


def test_frage_einwerfen_kein_doppel(baum: Path) -> None:
    erst = leiter.frage_einwerfen(baum, SPEC, TICKET, FRAGE)
    zweit = leiter.frage_einwerfen(baum, SPEC, TICKET, FRAGE)
    assert erst == zweit
    assert len(briefkasten.offene([TICKET], hauptbaum=baum)) == 1
    anders = leiter.frage_einwerfen(baum, SPEC, TICKET, "Welchen Namen soll die Datei haben?")
    assert anders != erst
    assert len(briefkasten.offene([TICKET], hauptbaum=baum)) == 2


def test_frage_einwerfen_nennt_spec(baum: Path) -> None:
    leiter.frage_einwerfen(baum, SPEC, TICKET, FRAGE)
    daten = briefkasten._lies(briefkasten._datei(TICKET, baum)).daten
    assert f"Spec {SPEC}" in daten["fragen"][0]["belege"][0]


def test_merken_scheitert_dauerhaft_kein_doppel_einwurf(baum: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def kaputt(name: str, wert: str) -> None:
        raise OSError("Platte voll")

    monkeypatch.setattr(leiter.leitstand, "setze_notiz", kaputt)
    u = FakeUmwelt(baum)
    for _ in range(3):
        erg = _lauf(u)
        assert erg.exit == leiter.EXIT_FEHLER
        assert "nicht gemerkt" in erg.zeile
    offen = briefkasten.offene([TICKET], hauptbaum=baum)
    assert len(offen) == 1 and offen[0]["frage"] == FRAGE
    assert offen[0]["id"] in erg.zeile


# --- Grenzen des Still-Frage-Zweigs (leiter.py, ruhig + Stufe 0/1) -------------------------


def test_still_frage_unter_mindest_ruhe_loest_nicht_aus() -> None:
    zu_frueh = leiter.MIN_RUHE_MIN - 1
    schritt = leiter.entscheide(
        _lage(aufseher_stand.STILL, FRAGE, zu_frueh), leiter.Gemerkt(0, None), time.time(), 250.0
    )
    assert schritt.aktion not in ("entscheider", "mensch")
    genau = leiter.entscheide(
        _lage(aufseher_stand.STILL, FRAGE, leiter.MIN_RUHE_MIN), leiter.Gemerkt(0, None), time.time(), 250.0
    )
    assert genau.aktion == "entscheider"


@pytest.mark.parametrize("stufe, erwartet", [(0, True), (1, True), (2, False), (3, False)])
def test_still_frage_nur_ab_stufe_0_und_1(stufe: int, erwartet: bool) -> None:
    schritt = leiter.entscheide(
        _lage(aufseher_stand.STILL, FRAGE, 25), leiter.Gemerkt(stufe, time.time() - 60), time.time(), 250.0
    )
    assert (schritt.aktion == "entscheider") is erwartet


# --- Fix-Runde 2: Doppel-Schutz und Absatz-Grenze dürfen Klasse 2 nicht verlieren ----------


def test_doppel_schutz_verschluckt_live_wort_im_abgeschnittenen_anfang_nicht(
    baum: Path,
) -> None:
    ende = f"{BERICHT} {BERICHT} Passt das so?"
    harmlos = "Ich würde den Stand prüfen. " + ende
    live = "Ich würde den Stand live deployen. " + ende
    assert aufseher_stand.frage_kurz(harmlos) == aufseher_stand.frage_kurz(live)
    erst = leiter.frage_einwerfen(baum, SPEC, TICKET, harmlos)
    zweit = leiter.frage_einwerfen(baum, SPEC, TICKET, live)
    assert zweit != erst
    offen = briefkasten.offene([TICKET], hauptbaum=baum)
    assert sorted(f["klasse"] for f in offen) == ["2", "3"]
    assert (
        leiter.frage_einwerfen(baum, SPEC, TICKET, live) == zweit
    )  # Klasse-2-Doppel bleibt einer


def test_live_wort_im_absatz_davor_braucht_den_menschen() -> None:
    text = f"● Ich würde den Stand jetzt live deployen.\n\nPasst das so?\n\n{EINGABE}\n❯ \n{EINGABE}\n  Kontext 80k"
    frage = aufseher_stand.frage_im_text(text)
    assert frage.endswith("Passt das so?")
    schritt = leiter.entscheide(
        _lage(aufseher_stand.STILL, frage, 25),
        leiter.Gemerkt(0, None),
        time.time(),
        250.0,
    )
    assert schritt.aktion == "mensch"


# --- Fix-Runde 3: Klasse an allen Zeilen über dem Eingabefeld (Review PR 374) -----------

BERICHT_ZEILEN = "\n".join(f"Zeile {i}: Tests grün, Diff geprüft, Belege abgelegt." for i in range(9))


def test_rueckfrage_live_wort_weit_ueber_auswahl_ist_klasse_2() -> None:
    text = (
        "● Bash(bash scripts/deploy.sh --live)\n"
        f"{BERICHT_ZEILEN}\n\n"
        "Do you want to proceed?\n"
        "❯ 1. Yes\n"
        "  2. Yes, and don't ask again for this command\n"
        "  3. No, and tell Claude what to do differently\n\n"
        "Esc to cancel · Tab to amend · ctrl+e to explain"
    )
    frage = aufseher_stand.frage_im_text(text)
    assert "Do you want to proceed?" in frage
    schritt = leiter.entscheide(_lage(aufseher_stand.RUECKFRAGE, frage), leiter.Gemerkt(0, None), time.time(), 250.0)
    assert schritt.aktion == "mensch"


def test_still_live_wort_weit_ueber_frage_ist_klasse_2() -> None:
    text = (
        f"● Ich würde den Stand jetzt live deployen.\n{BERICHT_ZEILEN}\n\nPasst das so?\n\n"
        f"{EINGABE}\n❯ \n{EINGABE}\n  Kontext 80k"
    )
    frage = aufseher_stand.frage_im_text(text)
    assert frage.endswith("Passt das so?")
    schritt = leiter.entscheide(_lage(aufseher_stand.STILL, frage, 25), leiter.Gemerkt(0, None), time.time(), 250.0)
    assert schritt.aktion == "mensch"


# --- Fix-Runde 4: Klasse nur an der aktuellen Antwort (letzte ●-Zeile, Review PR 374) ------


def test_alte_ausgabe_mit_live_wort_vor_letzter_antwort_darf_der_entscheider() -> None:
    text = (
        f"● Live-Deploy von gestern ist durch.\n{BERICHT_ZEILEN}\n\n"
        f"● Tests grün.\n\nSoll ich mit Ticket 12 weitermachen?\n\n"
        f"{EINGABE}\n❯ \n{EINGABE}\n  Kontext 80k"
    )
    frage = aufseher_stand.frage_im_text(text)
    assert frage == "Soll ich mit Ticket 12 weitermachen?"
    schritt = leiter.entscheide(_lage(aufseher_stand.STILL, frage, 25), leiter.Gemerkt(0, None), time.time(), 250.0)
    assert schritt.aktion == "entscheider"


def test_ohne_antwort_zeile_zaehlt_alles_und_bleibt_klasse_2() -> None:
    text = (
        f"Live-Deploy von gestern ist durch.\n{BERICHT_ZEILEN}\n\nSoll ich mit Ticket 12 weitermachen?\n\n"
        f"{EINGABE}\n❯ \n{EINGABE}\n  Kontext 80k"
    )
    frage = aufseher_stand.frage_im_text(text)
    assert frage.endswith("Soll ich mit Ticket 12 weitermachen?")
    schritt = leiter.entscheide(_lage(aufseher_stand.STILL, frage, 25), leiter.Gemerkt(0, None), time.time(), 250.0)
    assert schritt.aktion == "mensch"


def test_live_zeile_steht_im_gekuerzten_fragetext(baum: Path) -> None:
    text = (
        f"● Ich würde den Stand jetzt live deployen.\n{BERICHT_ZEILEN}\n{BERICHT_ZEILEN}\n"
        f"{BERICHT_ZEILEN}\n\n{BERICHT[2:]}\nPasst das so?\n\n{EINGABE}\n❯ \n{EINGABE}\n  Kontext 80k"
    )
    frage = aufseher_stand.frage_im_text(text)
    assert frage.endswith("Passt das so?")
    erg = _lauf(FakeUmwelt(baum, frage=frage))
    assert "braucht den Menschen" in erg.zeile
    offen = briefkasten.offene([TICKET], hauptbaum=baum)
    assert [f["klasse"] for f in offen] == ["2"]
    assert "live deployen" in offen[0]["frage"]
