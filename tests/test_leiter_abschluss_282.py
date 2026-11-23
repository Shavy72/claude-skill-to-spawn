"""#282 P3: Die Leiter würgt keine laufende Abschluss-Session per ``/exit`` ab.

Fertig gebaut, aber es fehlt noch ein Abschluss-Schritt (Art ``abschluss``) → kein ``/exit``;
nur Art ``nichts`` beendet. Lage unklar (``None``) → nicht beenden. Endgültig zu/verworfen
beendet wie bisher. Die Art kommt an genau einer Stelle aus :func:`wiederanlauf.feststellen`
(Quelle ``Quellen.anlauf``). Dazu die Pflicht-Reste aus P1: „Klickweg entfällt“ nur, wenn
sich die Aussage auf den Klickweg bezieht und nicht verneint ist; ``git log`` gescheitert →
``feststellen`` liefert ``None``.
"""

# ruff: noqa: F811 — Fixtures kommen aus den Nachbar-Tests und werden als Parameter genannt.
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

import test_aufseher_stand_430 as stand_test
from test_eigener_handoff_542 import MINUTE, TICKET
from test_leiter_fix1_432 import _stand_repo
from test_wiederanlauf_282 import _arbeit, _beleg, _git, _issue, wa, welt  # noqa: F401

from to_spawn import aufseher_stand, belege, gh, leiter, ticket_zustand, wiederanlauf

FERTIG = [{"name": ticket_zustand.STAGING_FERTIG_LABEL}]
ABSCHLUSS = wiederanlauf.Wiederanlauf("abschluss", ("Klickweg-Beleg",), "fehlt Klickweg")
NICHTS = wiederanlauf.Wiederanlauf("nichts", (), "nichts zu tun")


def _lage(stufe: ticket_zustand.Stufe, anlauf: wiederanlauf.Wiederanlauf | None) -> leiter.Lage:
    return leiter.Lage(
        stufe=stufe,
        fenster=aufseher_stand.STILL,
        still_min=60,
        kontext_k=None,
        prompt_datei=False,
        anlauf=anlauf,
    )


def _schritt(lage: leiter.Lage, stufe: int = 0) -> leiter.Schritt:
    return leiter.entscheide(lage, leiter.Gemerkt(stufe, None), 0.0, 250.0)


# --- Teil A: reine Regel ---------------------------------------------------------------


def test_abschluss_lage_kein_exit_und_nennt_was_fehlt() -> None:
    schritt = _schritt(_lage("fertig_gebaut", ABSCHLUSS))
    assert schritt.aktion == "nichts", schritt
    assert "Klickweg-Beleg" in schritt.grund


def test_art_nichts_exit_wie_bisher() -> None:
    assert _schritt(_lage("fertig_gebaut", NICHTS)).aktion == "exit"


def test_lage_unklar_kein_exit() -> None:
    schritt = _schritt(_lage("fertig_gebaut", None))
    assert schritt.aktion == "nichts", schritt
    assert "Lage unklar" in schritt.grund


@pytest.mark.parametrize("stufe", ["zu", "verworfen"])
@pytest.mark.parametrize("anlauf", [None, NICHTS, ABSCHLUSS], ids=["unklar", "nichts", "abschluss"])
def test_endgueltig_zu_beendet_wie_bisher(
    stufe: ticket_zustand.Stufe, anlauf: wiederanlauf.Wiederanlauf | None
) -> None:
    assert _schritt(_lage(stufe, anlauf)).aktion == "exit"


def test_abschluss_nach_exit_getippt_bleibt_still() -> None:
    assert _schritt(_lage("fertig_gebaut", ABSCHLUSS), stufe=4).aktion == "nichts"


# --- Teil A: genau eine Füllstelle (echte Umwelt über Quellen.anlauf) -------------------


def _echte_lage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    labels: list[dict[str, str]],
    antwort: wiederanlauf.Wiederanlauf | None,
) -> tuple[leiter.Lage, list[tuple[int, Path]]]:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    repo = _stand_repo(tmp_path, monkeypatch)
    w = stand_test.FakeWelt()
    w.ticket(TICKET)
    w.tickets[TICKET]["labels"] = labels
    w.fenster[TICKET] = (stand_test.JETZT - 60 * MINUTE, stand_test.bildschirm("✻ Baked for 40s"))
    aufrufe: list[tuple[int, Path]] = []

    def anlauf(n: int, ort: Path) -> wiederanlauf.Wiederanlauf | None:
        aufrufe.append((n, ort))
        return antwort

    q = dataclasses.replace(w.quellen(repo), anlauf=anlauf)
    return leiter._EchteUmwelt(repo, q).lage(stand_test.SPEC, TICKET, None), aufrufe


def test_echte_umwelt_fertig_gebaut_fragt_anlauf_und_kein_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lage, aufrufe = _echte_lage(tmp_path, monkeypatch, FERTIG, ABSCHLUSS)
    assert [n for n, _ in aufrufe] == [TICKET]
    assert lage.anlauf == ABSCHLUSS
    assert _schritt(lage).aktion == "nichts"


def test_echte_umwelt_offen_fragt_anlauf_nicht(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, aufrufe = _echte_lage(tmp_path, monkeypatch, [], ABSCHLUSS)
    assert aufrufe == []


def test_echte_quellen_holen_anlauf_aus_feststellen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gh, "gh_befehl", lambda: ["gh"])
    assert aufseher_stand.echte_quellen(tmp_path, "o/r").anlauf is wiederanlauf.feststellen


# --- Teil A: was feststellen bei endgültig zu liefert -----------------------------------


def test_feststellen_zu_aber_origin_weg_ist_none_leiter_beendet_trotzdem(
    wa: ModuleType, welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Zu + git unerreichbar → ``None``; die Leiter beendet ein zu-Ticket trotzdem (wie bisher)."""
    monkeypatch.setenv("GH_STUB_ZU", "7")
    _git(welt["klon"], "remote", "set-url", "origin", str(tmp_path / "gibt-es-nicht.git"))
    assert wa.feststellen(7, welt["klon"]) is None
    assert _schritt(_lage("zu", None)).aktion == "exit"


# --- Teil B 5: git log gescheitert → None -----------------------------------------------


def test_commit_betreffe_none_gibt_none(wa: ModuleType, welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _arbeit(welt["klon"])
    _issue(welt, labels=["staging:fertig"])
    monkeypatch.setattr(gh, "commit_betreffe", lambda *a, **k: None)
    assert wa.feststellen(7, welt["klon"]) is None


# --- Teil B 4: „Klickweg entfällt“ nur echt, auf den Klickweg bezogen --------------------


@pytest.mark.parametrize(
    ("text", "entfaellt"),
    [
        ("Klickweg: entfällt\n", True),
        ("Klickweg entfällt (Backend)\n", True),
        ("**Klickweg:** entfällt — reines Backend.\n", True),
        ("Klickweg nicht nötig (nur Skript).\n", True),
        ("kein Klickweg nötig\n", True),
        ("Klickweg nicht entfällt\n", False),
        ("Klickweg entfällt nicht\n", False),
        ("Klickweg fehlt, Rot-Beweis nicht erforderlich\n", False),
        ("Live-Beweis nicht nötig für Rot-Test\n", False),
        ("kein Klickweg gemacht, Lauf n/a\n", False),
    ],
)
def test_klickweg_entfaellt_nur_auf_klickweg_bezogen(tmp_path: Path, text: str, entfaellt: bool) -> None:
    (tmp_path / "7.md").write_text(text, encoding="utf-8")
    beleg = belege.auswerten(tmp_path, "7")
    assert beleg.klickweg is False
    assert beleg.klickweg_entfaellt is entfaellt


def test_klickweg_nicht_entfaellt_ist_abschluss(wa: ModuleType, welt: dict[str, Path]) -> None:
    _arbeit(welt["klon"])
    _beleg(welt["klon"], "# Beleg #7\n\nKlickweg nicht entfällt.\n\n12 passed in 0.4s\n")
    _issue(welt, labels=["staging:fertig"])
    lage = wa.feststellen(7, welt["klon"])
    assert lage is not None and lage.art == "abschluss" and lage.fehlt == ("Klickweg-Beleg",)
