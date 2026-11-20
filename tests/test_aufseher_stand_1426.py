"""#1426 Teil B: ein stilles Fenster gilt im Aufseher-Stand nie als „arbeitet“.

Weg-Test über ``aufseher_stand.stand`` — dieselbe Ebene, die der Aufseher liest.
Von außen gestellt sind nur tmux (Fensterliste + Bildschirm-Text) und GitHub
(Sub-Issues); Merker-Datei, Hash, Uhr-Abgleich und Urteil laufen echt. Kein echtes
tmux, keine echte Session.

Belegte Fälle vom 20.11.2026: #1301 stand 49 min am leeren Prompt mit der alten
Zeile „Waiting for background agent“ im Bild und wurde als „arbeitet“ gemeldet;
#1342 galt 12 h als „busy“ ohne jede Änderung.
"""

# ruff: noqa: F811 — ``repo``/``ordner`` sind importierte Fixtures

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aufseher_stand_430 import (  # noqa: E402,F401 — ``repo``/``ordner`` sind Fixtures
    JETZT,
    SPEC,
    FakeWelt,
    bildschirm,
    ordner,
    repo,
    zeile_fuer,
)

from to_spawn import aufpasser, aufseher_stand  # noqa: E402

MINUTE = 60.0

#: Bild von #1301: Session hat das Handoff geschrieben, steht am leeren Prompt,
#: oben noch die alte Agent-Zeile — die Uhr unten tickt weiter.
NACH_HANDOFF = (
    "⏺ Agent(Review Klickweg-Skript)\n"
    "  ⎿  Waiting for background agent…\n"
    "● Das Review des Klickweg-Skripts ist grün. Handoff steht, die nächste Session macht weiter.\n"
    "✻ Worked for 3m 12s"
)


def _zweimal(w: FakeWelt, n: int, ausgabe: str, still_min: float, repo: Path, ordner: Path) -> str:
    """Zwei Aufseher-Takte: erster sieht das Bild, zweiter ``still_min`` später dasselbe
    Bild — nur die Uhr der Statuszeile hat getickt (tmux meldet das Fenster aktiv)."""
    w.ticket(n)
    w.fenster[n] = (w.uhr, bildschirm(ausgabe, uhr="2h06m"))
    aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner)
    w.uhr = JETZT + still_min * MINUTE
    w.fenster[n] = (w.uhr, bildschirm(ausgabe, uhr="2h31m"))
    return zeile_fuer(aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner), n)


def test_leerer_prompt_25_min_unveraendert_ist_still(repo: Path, ordner: Path) -> None:
    """(1) Fall #1301: leerer Prompt, 25 min unverändert, Ticket offen → still."""
    z = _zweimal(FakeWelt(), 1301, NACH_HANDOFF, 25, repo, ordner)
    assert " · still 25 min · " in z, z
    assert " · arbeitet · " not in z


def test_gleiches_bild_5_min_noch_nicht_still(repo: Path, ordner: Path) -> None:
    """(2) Unter der Grenze bleibt das Bild mit Arbeits-Zeile „arbeitet“."""
    z = _zweimal(FakeWelt(), 1302, NACH_HANDOFF, 5, repo, ordner)
    assert " · arbeitet · " in z, z


def test_laufende_arbeit_25_min_bleibt_arbeitet(repo: Path, ordner: Path) -> None:
    """(3) Gegenfall: echte Arbeit lässt die Spinner-Zeile ticken → nie still."""
    w = FakeWelt()
    w.ticket(1303)
    for minute in range(0, 26, 5):  # Aufseher-Takt alle 5 min, 25 min lang
        w.uhr = JETZT + minute * MINUTE
        spinner = f"✻ Testlauf… ({minute}m 07s · ↓ 4.{minute}k tokens · esc to interrupt)"
        w.fenster[1303] = (w.uhr, bildschirm(f"⏺ Bash(pytest -n 8 tests)\n{spinner}"))
        z = zeile_fuer(aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner), 1303)
        assert " · arbeitet · " in z, (minute, z)


def test_helfer_unter_dem_eingabefeld_bleibt_arbeitet(repo: Path, ordner: Path) -> None:
    """(3b) Gegenfall: Hintergrund-Helfer laufen, Claude zeigt sie unter dem
    Eingabefeld (jedes Bild neu gezeichnet, nie alter Text) → bleibt „arbeitet“."""
    w = FakeWelt()
    w.ticket(1304)
    unten = "⏵⏵ bypass permissions on · 2 background agents running · ↓ to manage"
    for minute in (0, 25):
        w.uhr = JETZT + minute * MINUTE
        bild = bildschirm("● Zwei Helfer prüfen parallel.").replace(
            "  ⏵⏵ bypass permissions on (shift+tab to cycle)", f"  {unten}"
        )
        w.fenster[1304] = (w.uhr, bild)
        z = zeile_fuer(aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner), 1304)
    assert " · arbeitet · " in z, z


def test_eingefrorene_arbeitszeile_12_h_ist_still(repo: Path, ordner: Path) -> None:
    """(4) Fall #1342: Arbeits-Merker im Bild, aber 12 h keine Änderung → still."""
    eingefroren = "⏺ Bash(pytest tests)\n✻ Testlauf… (41m 07s · esc to interrupt)"
    z = _zweimal(FakeWelt(), 1342, eingefroren, 12 * 60, repo, ordner)
    assert " · still 720 min · " in z, z


@pytest.mark.parametrize("still_min", [25.0, 12 * 60.0])
def test_busy_merker_haelt_stilles_fenster_nicht(still_min: float) -> None:
    """(4) ``status == busy`` aus der Session-JSON (Aufpasser-Weg) zählt nach der
    Grenze nicht mehr — die eine Regel gilt für Aufpasser und Aufseher-Stand."""
    bild = bildschirm(NACH_HANDOFF)
    assert aufpasser.arbeitet(bild, "busy", still_min) is False
    assert aufpasser.arbeitet(bild, "busy", 5.0) is True
    assert aufpasser.arbeitet(bild, "busy") is True  # Dauer unbekannt: altes Urteil


def test_ohne_eingabefeld_gilt_die_grenze_nicht() -> None:
    """Warte-Zeile von bau.py (kein Claude-Eingabefeld) bleibt „arbeitet“, auch nach
    Stunden — Kennzeichen „still“ ist der leere Prompt."""
    warten = "Ticket #9 wartet (#8 offen) — nächste Prüfung in 10 min [08:00]\n"
    assert aufpasser.arbeitet(warten, None, 600.0) is True


def test_limit_wartet_bleibt_arbeitet() -> None:
    """Session wartet am Limit auf die Freigabe von außen → nie still."""
    bild = bildschirm("● You've hit your limit · resets 3pm")
    assert aufpasser.arbeitet(bild, None, 240.0) is True


#: Echtes Bild vom Bau-Server (20.11.2026, unterer Rand): Hauptsession arbeitet,
#: ein Hintergrund-Helfer läuft, seine Zeile steht unter dem Eingabefeld.
_LINIE = "─" * 36
_HELFER_UNTEN = (
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ↓ to man…\n"
    "\n"
    "  ● main\n"
    "  ◯ general-purpose  Suche Einfügestelle für Abnahme-Skr… 53s · ↓ 68.3k tokens"
)
ECHTES_HELFER_BILD = (
    "* Cooking… (1m 26s · ↓ 8.7k tokens)\n"
    "  ⎿  Tip: Use /btw to ask a side question without interrupting Claude\n"
    "\n"
    f"{_LINIE}\n❯ \n{_LINIE}\n\n{_HELFER_UNTEN}"
)


def test_echtes_helfer_bild_bleibt_arbeitet() -> None:
    """Review #1426 (a): echtes Bild mit Helfer-Zeile unter dem Feld → arbeitet."""
    assert aufpasser.arbeitet(ECHTES_HELFER_BILD, None, 25.0) is True


def test_hauptsession_fertig_helfer_laeuft_bleibt_arbeitet() -> None:
    """Review #1426 (a): Hauptsession hat ihren Zug beendet (kein Spinner, kein
    „esc to interrupt“), oben steht nur noch die alte Agent-Zeile, unten tickt die
    Zeile des laufenden Helfers. Oben ändert sich 25 min nichts — trotzdem Arbeit,
    sonst stupst die Leiter nach 20 min an und löst danach ab."""
    unten = (
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ↓ to manage\n"
        "\n"
        "  ● main\n"
        "  ◯ general-purpose  Gate-Lauf prüfen… 24m 10s · ↓ 112.4k tokens"
    )
    bild = f"{NACH_HANDOFF}\n\n{_LINIE}\n❯ \n{_LINIE}\n\n{unten}"
    assert aufpasser.arbeitet(bild, None, 25.0) is True
    # Ohne Helfer-Zeile bleibt es beim Urteil „still“ (#1301).
    ohne = f"{NACH_HANDOFF}\n\n{_LINIE}\n❯ \n{_LINIE}\n\n  ⏵⏵ bypass permissions on · ↓ to manage\n\n  ● main"
    assert aufpasser.arbeitet(ohne, None, 25.0) is False


def test_getippter_text_im_feld_faellt_auf_die_muster_zurueck() -> None:
    """Review #1426 B2: Feld nicht leer (eingereihte Nachricht) → Grenze greift nicht."""
    bild = f"{NACH_HANDOFF}\n\n{_LINIE}\n❯ mach weiter\n{_LINIE}\n\n  ⏵⏵ bypass permissions on"
    assert aufpasser.arbeitet(bild, None, 600.0) is True


def test_rueckfrage_verfaellt_nie() -> None:
    """Review #1426 B2: offene Rückfrage bleibt auch nach Stunden „arbeitet“."""
    bild = (
        f"⏺ Bash(rm -rf build)\n  Do you want to proceed?\n  ❯ 1. Yes\n    2. No\n\n"
        f"{_LINIE}\n❯ \n{_LINIE}"
    )
    assert aufpasser.rueckfrage(bild)
    assert aufpasser.arbeitet(bild, None, 600.0) is True


def test_grenze_genau_bei_zwanzig_minuten() -> None:
    """Review #1426 B3: knapp darunter noch das alte Urteil, ab 20 min still."""
    bild = bildschirm(NACH_HANDOFF)
    grenze = aufpasser.ARBEITET_STILL_MIN
    assert grenze == 20
    assert aufpasser.arbeitet(bild, None, grenze - 0.1) is True
    assert aufpasser.arbeitet(bild, None, float(grenze)) is False
