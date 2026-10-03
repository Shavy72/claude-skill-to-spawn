"""Aufpasser #429: tickende Uhr in der Statuszeile ist keine Arbeit (E18).

Der Aufpasser unterscheidet „arbeitet“ von „still“ über den Hash des
Bildschirm-Texts. Die Statuszeile unter dem Eingabefeld (Sitzungsdauer, Reset-Uhr,
Kontext-Stand) ändert sich von allein — sie darf den Hash nicht verändern.
Die Bildschirm-Texte unten sind echten tmux-Mitschnitten vom Bau-Server
nachgebaut (04.10.2026).
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser

LINIE = "─" * 80


def bildschirm(ausgabe: str, dauer: str, reset: str, eingabe: str = "") -> str:
    """Pane-Text wie Claude Code ihn zeigt: Ausgabe, Eingabefeld, Statuszeile."""
    return "\n".join(
        [
            ausgabe,
            "",
            LINIE,
            f"❯ {eingabe}",
            LINIE,
            f"  Opus 5.5 (1M context) · 91.7k (9.0%) · {dauer}",
            f"  📁 spawn-399 · ⎇ HEAD · 5h: 10% (reset {reset}) · 7d: 58%",
            "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
            "",
        ]
    )


def test_nur_uhr_der_statuszeile_anders_gleicher_hash() -> None:
    """Rot vor dem Fix: die Uhr allein änderte den Hash → Session galt als arbeitend."""
    vorher = bildschirm("✻ Baked for 40s · done 12:09 AM", "2h06m", "03:20")
    nachher = bildschirm("✻ Baked for 40s · done 12:09 AM", "2h07m", "03:20")
    assert aufpasser.pane_hash(vorher) == aufpasser.pane_hash(nachher)


def test_kontext_und_reset_in_statuszeile_gleicher_hash() -> None:
    vorher = bildschirm("fertig", "2h06m", "03:20")
    nachher = bildschirm("fertig", "3h00m", "08:20").replace("91.7k (9.0%)", "92.0k")
    assert aufpasser.pane_hash(vorher) == aufpasser.pane_hash(nachher)


def test_echte_ausgabe_aenderung_anderer_hash() -> None:
    """Neue Zeile über dem Eingabefeld = echte Arbeit, auch wenn die Uhr gleich bleibt."""
    vorher = bildschirm("● Lese Datei", "2h06m", "03:20")
    nachher = bildschirm("● Lese Datei\n● Schreibe Test", "2h06m", "03:20")
    assert aufpasser.pane_hash(vorher) != aufpasser.pane_hash(nachher)


def test_eingabefeld_aenderung_anderer_hash() -> None:
    """Was im Eingabefeld steht, gehört nicht zur Statuszeile."""
    vorher = bildschirm("fertig", "2h06m", "03:20")
    nachher = bildschirm("fertig", "2h06m", "03:20", eingabe="weiter")
    assert aufpasser.pane_hash(vorher) != aufpasser.pane_hash(nachher)


def test_ohne_trennlinie_ganzer_text_zaehlt() -> None:
    """Kein Claude-Bildschirm (z. B. bau.py-Warteschleife): nichts abschneiden."""
    assert aufpasser.pane_hash("warte [08:00]") != aufpasser.pane_hash("warte [08:10]")


def test_eintrag_still_bei_tickender_uhr(tmp_path: Path) -> None:
    """Weg über ``Aufpasser.eintrag``: tickt nur die Uhr, bleibt ``seit`` stehen;
    neue Ausgabe setzt ``seit`` auf jetzt."""
    a = aufpasser.Aufpasser(
        aufpasser.Einstellungen(
            zustand=tmp_path / "stand.json",
            tmux_socket="nie-benutzt",
            trocken=True,
            jetzt=lambda: 1000.0,
        )
    )
    a.eintrag("spec-1/bau 9", bildschirm("fertig", "2h06m", "03:20"))
    a.jetzt = 1600.0
    e = a.eintrag("spec-1/bau 9", bildschirm("fertig", "2h16m", "03:20"))
    assert e["seit"] == 1000.0, "nur die Uhr tickte — Fenster muss still bleiben"
    a.jetzt = 2200.0
    e = a.eintrag("spec-1/bau 9", bildschirm("fertig\n● neu", "2h26m", "03:20"))
    assert e["seit"] == 2200.0, "echte Ausgabe — Fenster arbeitet"


def test_dialog_mit_einer_linie_ganzer_text_zaehlt() -> None:
    """Auswahl-Dialog ohne Eingabefeld: Änderung unter der Linie bleibt Arbeit."""
    vorher = f"Ausgabe\n{LINIE}\n Plan übernehmen?\n ❯ 1. Ja\n   2. Nein\n"
    nachher = f"Ausgabe\n{LINIE}\n Plan übernehmen?\n   1. Ja\n ❯ 2. Nein\n"
    assert aufpasser.pane_hash(vorher) != aufpasser.pane_hash(nachher)


def test_shell_ausgabe_mit_linie_ganzer_text_zaehlt() -> None:
    """Strichlinie in normaler Ausgabe ohne Eingabefeld: nichts abschneiden."""
    vorher = f"{LINIE}\nBericht\n{LINIE}\nZeile 1\n"
    nachher = f"{LINIE}\nBericht\n{LINIE}\nZeile 1\nZeile 2\n"
    assert aufpasser.pane_hash(vorher) != aufpasser.pane_hash(nachher)


def test_linie_mit_titel_ist_kein_rahmen() -> None:
    """``──── Titel ────`` ist keine Rahmenlinie des Eingabefelds."""
    titel = "─" * 30 + " Titel " + "─" * 30
    vorher = bildschirm("fertig", "2h06m", "03:20").replace(LINIE, titel, 1)
    nachher = bildschirm("fertig", "2h07m", "03:20").replace(LINIE, titel, 1)
    assert aufpasser.pane_hash(vorher) != aufpasser.pane_hash(nachher)
