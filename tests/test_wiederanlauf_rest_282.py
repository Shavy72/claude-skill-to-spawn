"""#282 P2: weiche Punkte aus der Prüfung nach Pull-Request 321.

1. „Klickweg entfällt“ auch mit Gedankenstrich, Tabellenzelle, Klammerzusatz und „Live-Beweis“.
2. Stille Abschluss-Session (oder Lage unklar) bleibt nicht endlos offen: nach
   :data:`leiter.ABSCHLUSS_MELDE_MIN` still → ``melden`` (Aufseher prüft), nie ablösen.
3. Belege kommen aus dem Arbeitsbaum der Session, nicht aus dem Haupt-Repo.
4. Vorgabe :data:`leiter.OHNE_ANLAUF_FRAGE` festgenagelt; ``feststellen`` in echter Umwelt.
5. ``feststellen`` nur, wenn das Fenster still ist — sonst entscheidet die Art nichts.
"""

# ruff: noqa: F811 — Fixtures kommen aus den Nachbar-Tests und werden als Parameter genannt.
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

import test_aufseher_stand_430 as stand_test
import test_leiter_432 as leiter_test
from test_eigener_handoff_542 import MINUTE, TICKET
from test_leiter_abschluss_282 import ABSCHLUSS, FERTIG, _echte_lage, _lage, _schritt
from test_leiter_fix1_432 import _stand_repo
from test_wiederanlauf_282 import _git, welt  # noqa: F401

from to_spawn import aufseher_stand, belege, leiter, wiederanlauf

# --- 1: was als „Klickweg entfällt“ zählt ------------------------------------------------


@pytest.mark.parametrize(
    ("text", "entfaellt"),
    [
        ("Klickweg — entfällt\n", True),
        ("Klickweg – entfällt (reines Backend)\n", True),
        ("| Klickweg | entfällt |\n", True),
        ("| **Klickweg** | entfällt — nur Skript |\n", True),
        ("Klickweg (Nutzer-Ablauf) entfällt\n", True),
        ("Live-Beweis entfällt\n", True),
        ("Live-Beweis: entfällt (reines Backend)\n", True),
        ("Klickweg (Nutzer-Ablauf) nicht entfällt\n", False),
        ("| Klickweg | entfällt nicht |\n", False),
        ("Live-Beweis entfällt nicht\n", False),
        ("| Klickweg | fehlt | Rot-Beweis entfällt |\n", False),
    ],
)
def test_klickweg_entfaellt_weitere_schreibweisen(tmp_path: Path, text: str, entfaellt: bool) -> None:
    (tmp_path / "7.md").write_text(text, encoding="utf-8")
    beleg = belege.auswerten(tmp_path, "7")
    assert beleg.klickweg is False
    assert beleg.klickweg_entfaellt is entfaellt


# --- 2: stille Abschluss-Session wird gemeldet -------------------------------------------


def _still(lage: leiter.Lage, minuten: int) -> leiter.Lage:
    return dataclasses.replace(lage, still_min=minuten)


@pytest.mark.parametrize("anlauf", [ABSCHLUSS, None], ids=["abschluss", "unklar"])
def test_stille_abschluss_session_wird_gemeldet(anlauf: wiederanlauf.Wiederanlauf | None) -> None:
    grenze = leiter.ABSCHLUSS_MELDE_MIN
    vorher = _schritt(_still(_lage("fertig_gebaut", anlauf), grenze - 1))
    assert vorher.aktion == "nichts", vorher
    danach = _schritt(_still(_lage("fertig_gebaut", anlauf), grenze))
    assert danach.aktion == "melden", danach
    assert f"{grenze} min" in danach.grund
    if anlauf is not None:
        assert "Klickweg-Beleg" in danach.grund


def test_gemeldete_abschluss_session_wird_nie_abgeloest_auch_am_pc() -> None:
    schritt = _schritt(_still(_lage("fertig_gebaut", ABSCHLUSS), 10 * leiter.ABSCHLUSS_MELDE_MIN))
    assert schritt.aktion == "melden"
    pc = leiter.fuer_rechner(schritt, kann_tippen=False, still_min=10 * leiter.ABSCHLUSS_MELDE_MIN)
    assert pc.aktion == "melden"


def test_melde_grenze_laenger_als_stupser_und_pc_abloesung() -> None:
    """Abschluss wartet oft auf Gate/Staging — nicht früher melden als der normale Stupser."""
    assert leiter.ABSCHLUSS_MELDE_MIN > leiter.STUPS_MIN + leiter.NACH_STUPS_MIN
    assert leiter.ABSCHLUSS_MELDE_MIN > leiter.PC_ABLOESE_MIN


# --- 4: Vorgabe ohne Anlauf-Frage -------------------------------------------------------


def test_vorgabe_ohne_anlauf_frage_ist_nichts_und_beendet_wie_vor_p3() -> None:
    assert leiter.OHNE_ANLAUF_FRAGE == wiederanlauf.Wiederanlauf("nichts", (), "")
    lage = leiter.Lage(
        stufe="fertig_gebaut", fenster=aufseher_stand.STILL, still_min=60, kontext_k=None, prompt_datei=False
    )
    assert lage.anlauf is leiter.OHNE_ANLAUF_FRAGE
    assert _schritt(lage).aktion == "exit"


# --- 3 + 4: echte Umwelt, echtes feststellen, Belege im Arbeitsbaum ----------------------


def test_echte_umwelt_feststellen_liest_belege_im_arbeitsbaum(
    welt: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Arbeitsbaum hat Klickweg + grünen Lauf, Haupt-Repo nicht → Art ``nichts`` → ``/exit``."""
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    repo = _stand_repo(tmp_path, monkeypatch)
    wt = tmp_path / "wts" / f"wt-{TICKET}"
    _git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(wt))
    ordner = wt / "docs" / "verify-hard"
    ordner.mkdir(parents=True)
    (ordner / f"{TICKET}.md").write_text(
        f"# Beleg #{TICKET}\n\nKlickweg live: Seite geöffnet, Ergebnis sichtbar.\n\n12 passed in 0.4s\n",
        encoding="utf-8",
    )
    welt["daten"].write_text(json.dumps({str(TICKET): {"labels": ["staging:fertig"]}}), encoding="utf-8")
    w = stand_test.FakeWelt()
    w.ticket(TICKET)
    w.tickets[TICKET]["labels"] = FERTIG
    w.fenster[TICKET] = (stand_test.JETZT - 60 * MINUTE, stand_test.bildschirm("✻ Baked for 40s"))
    q = dataclasses.replace(w.quellen(repo), anlauf=wiederanlauf.feststellen)
    lage = leiter._EchteUmwelt(repo, q).lage(stand_test.SPEC, TICKET, None)
    assert lage.anlauf is not None, "feststellen las das Haupt-Repo statt den Arbeitsbaum"
    assert lage.anlauf.art == "nichts", lage.anlauf
    assert _schritt(lage).aktion == "exit"


# --- 5: feststellen nur bei stillem Fenster ----------------------------------------------


def _lage_mit_fenster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fenster: str | None
) -> tuple[leiter.Lage, list[Path]]:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    repo = _stand_repo(tmp_path, monkeypatch)
    w = stand_test.FakeWelt()
    w.ticket(TICKET)
    w.tickets[TICKET]["labels"] = FERTIG
    if fenster is not None:
        w.fenster[TICKET] = (w.uhr, stand_test.bildschirm(fenster))
    aufrufe: list[Path] = []

    def anlauf(n: int, ort: Path) -> wiederanlauf.Wiederanlauf | None:
        aufrufe.append(ort)
        return ABSCHLUSS

    q = dataclasses.replace(w.quellen(repo), anlauf=anlauf)
    return leiter._EchteUmwelt(repo, q).lage(stand_test.SPEC, TICKET, None), aufrufe


@pytest.mark.parametrize("fenster", ["● Lese\n✻ Baking… (esc to interrupt)", None], ids=["arbeitet", "kein_fenster"])
def test_fertig_gebaut_ohne_stilles_fenster_fragt_feststellen_nicht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fenster: str | None
) -> None:
    lage, aufrufe = _lage_mit_fenster(tmp_path, monkeypatch, fenster)
    assert lage.fenster != aufseher_stand.STILL
    assert aufrufe == []


def test_fertig_gebaut_stilles_fenster_fragt_feststellen_weiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lage, aufrufe = _echte_lage(tmp_path, monkeypatch, FERTIG, ABSCHLUSS)
    assert lage.fenster == aufseher_stand.STILL
    assert len(aufrufe) == 1


# --- Fix-Runde 1 (Prüfung zu Pull-Request 392) -------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Live-Beweis entfällt, Klickweg aber nötig\n",
        "Klickweg entfällt keinesfalls\n",
        "Klickweg entfällt — nicht! gemacht\n",
        "Klickweg entfällt nicht\n",
        "Klickweg nicht entfällt\n",
        "| Klickweg | entfällt nicht |\n",
        "Live-Beweis (Staging) entfällt nicht\n",
    ],
)
def test_entfaellt_im_zweifel_nicht(tmp_path: Path, text: str) -> None:
    (tmp_path / "7.md").write_text(text, encoding="utf-8")
    assert belege.auswerten(tmp_path, "7").klickweg_entfaellt is False


def _abschluss_umwelt(ordner: Path) -> leiter_test.FakeUmwelt:
    class AbschlussUmwelt(leiter_test.FakeUmwelt):
        def lage(self, spec: int, ticket: int, seit: float | None) -> leiter.Lage:
            return dataclasses.replace(super().lage(spec, ticket, seit), anlauf=ABSCHLUSS)

    return AbschlussUmwelt(ordner, still_min=leiter.ABSCHLUSS_MELDE_MIN, stufe="fertig_gebaut")


def test_abschluss_meldet_einmal_bis_das_fenster_sich_bewegt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    wt = tmp_path / "wt"
    (wt / "docs" / "handoffs").mkdir(parents=True)
    u = _abschluss_umwelt(wt)
    erste = leiter_test._lauf(u)
    assert erste.exit == leiter.EXIT_FEHLER, erste
    zweite = leiter_test._lauf(u)
    assert zweite.exit == leiter.EXIT_OK, zweite
    u.still_min += 60
    assert leiter_test._lauf(u).exit == leiter.EXIT_OK
    assert u.wz.getippt == []
    u.fenster, u.still_min = aufseher_stand.ARBEITET, 0  # Fenster bewegt sich
    assert leiter_test._lauf(u).exit == leiter.EXIT_OK
    u.fenster, u.still_min = aufseher_stand.STILL, leiter.ABSCHLUSS_MELDE_MIN - 1
    assert leiter_test._lauf(u).exit == leiter.EXIT_OK
    u.still_min = leiter.ABSCHLUSS_MELDE_MIN
    assert leiter_test._lauf(u).exit == leiter.EXIT_FEHLER
    assert leiter_test._lauf(u).exit == leiter.EXIT_OK
    assert u.wz.getippt == []


# --- Fix-Runde 2 (Prüfung zu Pull-Request 392) -------------------------------------------


@pytest.mark.parametrize(
    ("text", "entfaellt"),
    [
        ("Klickweg entfällt\n", True),
        ("Klickweg entfällt.\n", True),
        ("Klickweg entfällt (reine Rechenlogik)\n", True),
        ("Klickweg entfällt, keine Oberfläche\n", True),
        ("Klickweg entfällt, kein UI\n", True),
        ("Klickweg entfällt: nur Doku\n", True),
        ("Klickweg entfällt? Nein.\n", False),
        ("Klickweg entfällt (nicht!)\n", False),
        ("Klickweg entfällt — trotzdem nötig\n", False),
        ("Klickweg entfällt — falsch, UI betroffen\n", False),
        ("Klickweg entfällt, Live-Beweis nötig\n", False),
        ("Klickweg entfällt nur auf Staging, live Pflicht\n", False),
        ("Klickweg entfällt erst nach Deploy\n", False),
        ("~~Klickweg entfällt~~\n", False),
        ("Behauptung „Klickweg entfällt“ ist falsch\n", False),
        ('Behauptung "Klickweg entfällt" ist falsch\n', False),
    ],
)
def test_entfaellt_nur_mit_erlaubter_fortsetzung(
    tmp_path: Path, text: str, entfaellt: bool
) -> None:
    (tmp_path / "7.md").write_text(text, encoding="utf-8")
    assert belege.auswerten(tmp_path, "7").klickweg_entfaellt is entfaellt


def test_abschluss_meldet_wieder_nach_kurzer_arbeit_zwischen_zwei_takten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    wt = tmp_path / "wt"
    (wt / "docs" / "handoffs").mkdir(parents=True)
    u = _abschluss_umwelt(wt)
    assert leiter_test._lauf(u).exit == leiter.EXIT_FEHLER  # gemeldet
    u.still_min = 20  # kurz gearbeitet, kein Takt sah „arbeitet“
    assert leiter_test._lauf(u).exit == leiter.EXIT_OK
    u.still_min = leiter.ABSCHLUSS_MELDE_MIN + 5
    assert leiter_test._lauf(u).exit == leiter.EXIT_FEHLER  # genau eine neue Meldung
    assert leiter_test._lauf(u).exit == leiter.EXIT_OK
    assert u.wz.getippt == []
