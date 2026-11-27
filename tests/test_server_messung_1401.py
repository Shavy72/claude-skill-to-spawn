"""Unit-Tests für das 14-Tage-Auswerte-Skript ``messung/server_messung_1401.py`` (#905 / App #1401)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from messung import server_messung_1401 as sm


def _schreibe_jsonl(pfad: Path, zeilen: list[dict[str, object] | str]) -> Path:
    inhalt = [z if isinstance(z, str) else json.dumps(z, ensure_ascii=False) for z in zeilen]
    pfad.write_text("\n".join(inhalt) + "\n", encoding="utf-8")
    return pfad


def _beispiel_fahrtenbuch() -> list[dict[str, object] | str]:
    return [
        # Außerhalb des 14-Tage-Fensters (vor 2026-11-13T23:59:59+01:00) -> muss ignoriert werden
        {
            "zeit": "2026-11-01T10:00:00+01:00",
            "sha": "alt0001",
            "ergebnis": "ticket-rot",
            "rot_art": "technik",
            "gate_s": 9999,
        },
        # Defekte Zeile -> robust überspringen
        "{keine gueltige json zeile",
        # Innerhalb der 14 Tage (2026-11-14 .. 2026-11-27)
        {"zeit": "2026-11-15T08:00:00+01:00", "sha": "a1", "ergebnis": "ticket-gruen", "gate_s": 120},
        {"zeit": "2026-11-16T09:00:00+01:00", "sha": "a2", "ergebnis": "ticket-gruen", "gate_s": 180},
        {"zeit": "2026-11-17T10:00:00+01:00", "sha": "a3", "ergebnis": "ticket-gruen", "gate_s": 240},
        {
            "zeit": "2026-11-18T11:00:00+01:00",
            "sha": "a4",
            "ergebnis": "ticket-rot",
            "rot_art": "eigener_fehler",
            "gate_s": 300,
        },
        {
            "zeit": "2026-11-19T12:00:00+01:00",
            "sha": "a5",
            "ergebnis": "ticket-rot",
            "rot_art": "master_geerbt",
            "gate_s": 210,
        },
        {
            "zeit": "2026-11-20T13:00:00+01:00",
            "sha": "a6",
            "ergebnis": "ticket-rot",
            "rot_art": "wackler",
            "gate_s": 190,
        },
        {
            "zeit": "2026-11-21T14:00:00+01:00",
            "sha": "a7",
            "ergebnis": "ticket-gate-fehler",
            "rot_art": "technik",
            "gate_s": 420,
        },
        {
            "zeit": "2026-11-22T15:00:00+01:00",
            "sha": "a8",
            "ergebnis": "buendel-rot",
            "gate_s": 260,
        },
        {"zeit": "2026-11-25T16:00:00+01:00", "sha": "a9", "ergebnis": "staging"},
    ]


def _beispiel_nachtlauf() -> list[dict[str, object]]:
    return [
        {
            "zeit": "2026-11-01T03:00:00+01:00",
            "dateien": {"tests/test_uralt.py": 999.0},
        },
        {
            "zeit": "2026-11-18T03:00:00+01:00",
            "dateien": {
                "tests/test_langsam.py": 85.0,
                "tests/test_mittel.py": 25.0,
                "tests/test_schnell.py": 4.0,
            },
        },
        {
            "zeit": "2026-11-22T03:00:00+01:00",
            "dateien": {
                "tests/test_langsam.py": 115.0,
                "tests/test_mittel.py": 35.0,
            },
        },
        # Einzelzeilen-Format (z. B. aus Nachtlauf-Langsam-Auszug)
        {
            "zeit": "2026-11-24T03:00:00+01:00",
            "datei": "tests/test_einzel.py",
            "dauer_s": 62.5,
        },
    ]


def _beispiel_auslastung_entspannt() -> list[dict[str, object]]:
    return [
        # Nicht-Auslastungszeile -> ignorieren
        {"zeit": "2026-11-20T10:00:00+01:00", "typ": "auftrag", "ticket": 905},
        # Außerhalb 14-Tage-Fenster -> ignorieren
        {
            "zeit": "2026-11-01T10:00:00+01:00",
            "typ": "auslastung",
            "platte_frei_gib": 1.0,
            "ram_frei_mib": 256,
            "sessions": 12,
        },
        {
            "zeit": "2026-11-20T10:15:00+01:00",
            "typ": "auslastung",
            "platte_frei_gib": 42.0,
            "ram_frei_mib": 8192,
            "sessions": 3,
            "sessions_ohne_ticket": 0,
        },
        {
            "zeit": "2026-11-20T10:30:00+01:00",
            "typ": "auslastung",
            "platte_frei_gib": 41.5,
            "ram_frei_mib": 7680,
            "sessions": 4,
            "sessions_ohne_ticket": 1,
        },
        {
            "zeit": "2026-11-21T10:45:00+01:00",
            "typ": "auslastung",
            "platte_frei_gib": 40.0,
            "ram_frei_mib": 7168,
            "sessions": 3,
            "sessions_ohne_ticket": 0,
        },
    ]


def test_gate_dauer_und_rot_arten_14_tage(tmp_path: Path) -> None:
    """14-Tage-Fenster filtert Altzeilen und zählt Gate-Dauer sowie alle Rot-Arten korrekt."""
    fb = _schreibe_jsonl(tmp_path / "fahrtenbuch.jsonl", _beispiel_fahrtenbuch())
    nl = _schreibe_jsonl(tmp_path / "laufzeit.jsonl", _beispiel_nachtlauf())
    ep = _schreibe_jsonl(tmp_path / "ereignisse.jsonl", _beispiel_auslastung_entspannt())

    befund = sm.auswerten(
        fahrtenbuch_pfad=fb,
        nachtlauf_pfad=nl,
        ereignis_pfad=ep,
        stichtag="2026-11-27",
        tage=14,
    )

    assert befund.fenster.start.date().isoformat() == "2026-11-13"
    assert befund.fenster.ende.date().isoformat() == "2026-11-27"
    # 8 Urteilszeilen innerhalb der 14 Tage (alt0001 außerhalb, staging kein Urteil, 1 defekt)
    assert befund.fahrtenbuch.urteile_gesamt == 8
    assert befund.fahrtenbuch.rote_gesamt == 5
    assert befund.fahrtenbuch.rot_quote_pct == pytest.approx(62.5)
    assert befund.fahrtenbuch.rot_nach_art == {
        "eigener_fehler": 1,
        "master_geerbt": 1,
        "wackler": 1,
        "technik": 1,
        "unbekannt": 1,
    }
    assert befund.fahrtenbuch.gate_gesamt.anzahl == 8
    assert befund.fahrtenbuch.gate_gesamt.max_s == pytest.approx(420.0)
    assert befund.fahrtenbuch.gate_je_ergebnis["ticket-gruen"].median_s == pytest.approx(180.0)


def test_nachtlauf_langsamlaufer_sortierung_und_fenster(tmp_path: Path) -> None:
    """Nachtlauf-Langsamläufer ignorieren Altzeilen und sortieren nach Max-Dauer absteigend."""
    nl = _schreibe_jsonl(tmp_path / "laufzeit.jsonl", _beispiel_nachtlauf())
    befund = sm.auswerten(
        fahrtenbuch_pfad=None,
        nachtlauf_pfad=nl,
        ereignis_pfad=None,
        stichtag="2026-11-27",
        tage=14,
        top_n=2,
    )

    dateien = [e.datei for e in befund.nachtlauf.langsamste]
    assert dateien == ["tests/test_langsam.py", "tests/test_einzel.py"]
    assert befund.nachtlauf.langsamste[0].max_s == pytest.approx(115.0)
    assert befund.nachtlauf.langsamste[0].median_s == pytest.approx(100.0)
    assert befund.nachtlauf.langsamste[0].anzahl == 2
    assert "tests/test_uralt.py" not in dateien


def test_auslastungsverlauf_kennzahlen_und_tagesuebersicht(tmp_path: Path) -> None:
    """15-Minuten-Auslastung liefert Platte, Speicher, Sessions und Tageswerte."""
    ep = _schreibe_jsonl(tmp_path / "ereignisse.jsonl", _beispiel_auslastung_entspannt())
    befund = sm.auswerten(
        fahrtenbuch_pfad=None,
        nachtlauf_pfad=None,
        ereignis_pfad=ep,
        stichtag="2026-11-27",
        tage=14,
    )

    aus = befund.auslastung
    assert aus.messpunkte == 3
    assert aus.platte_frei_gib.min_wert == pytest.approx(40.0)
    assert aus.ram_frei_mib.min_wert == pytest.approx(7168.0)
    assert aus.sessions.max_wert == pytest.approx(4.0)
    assert aus.sessions_ohne_ticket.max_wert == pytest.approx(1.0)
    assert [t.tag for t in aus.tagesverlauf] == ["2026-11-20", "2026-11-21"]


def test_empfehlung_nichts_kaufen_bei_entspannter_lage(tmp_path: Path) -> None:
    """Genügend RAM/Platte und schnelle Gates -> Empfehlung ``nichts_kaufen``."""
    fb = _schreibe_jsonl(
        tmp_path / "fahrtenbuch.jsonl",
        [
            {"zeit": f"2026-11-{15 + i:02d}T10:00:00+01:00", "sha": f"s{i}", "ergebnis": "ticket-gruen", "gate_s": 140}
            for i in range(6)
        ],
    )
    ep = _schreibe_jsonl(tmp_path / "ereignisse.jsonl", _beispiel_auslastung_entspannt())
    befund = sm.auswerten(fahrtenbuch_pfad=fb, nachtlauf_pfad=None, ereignis_pfad=ep, stichtag="2026-11-27")

    assert befund.empfehlung.option == "nichts_kaufen"


def test_empfehlung_groesserer_server_bei_ram_und_session_engpass(tmp_path: Path) -> None:
    """Anhaltender RAM-Engpass plus hohe Session-/Gate-Last -> Empfehlung ``groesserer_server``."""
    fb = _schreibe_jsonl(
        tmp_path / "fahrtenbuch.jsonl",
        [
            {
                "zeit": f"2026-11-{15 + i:02d}T10:00:00+01:00",
                "sha": f"g{i}",
                "ergebnis": "ticket-gate-fehler" if i % 2 == 0 else "ticket-gruen",
                "rot_art": "technik" if i % 2 == 0 else None,
                "gate_s": 780,
            }
            for i in range(6)
        ],
    )
    ep = _schreibe_jsonl(
        tmp_path / "ereignisse.jsonl",
        [
            {
                "zeit": f"2026-11-20T{10 + i:02d}:00:00+01:00",
                "typ": "auslastung",
                "platte_frei_gib": 35.0,
                "ram_frei_mib": 1100,
                "sessions": 8,
            }
            for i in range(6)
        ],
    )
    befund = sm.auswerten(fahrtenbuch_pfad=fb, nachtlauf_pfad=None, ereignis_pfad=ep, stichtag="2026-11-27")

    assert befund.empfehlung.option == "groesserer_server"


def test_empfehlung_eigene_staging_maschine_bei_platten_engpass(tmp_path: Path) -> None:
    """Platte anhaltend knapp bei ausreichendem RAM -> Empfehlung ``eigene_staging_maschine``."""
    fb = _schreibe_jsonl(
        tmp_path / "fahrtenbuch.jsonl",
        [
            {"zeit": f"2026-11-{15 + i:02d}T10:00:00+01:00", "sha": f"p{i}", "ergebnis": "ticket-gruen", "gate_s": 210}
            for i in range(5)
        ],
    )
    ep = _schreibe_jsonl(
        tmp_path / "ereignisse.jsonl",
        [
            {
                "zeit": f"2026-11-21T{10 + i:02d}:00:00+01:00",
                "typ": "auslastung",
                "platte_frei_gib": 5.2,
                "ram_frei_mib": 6400,
                "sessions": 4,
            }
            for i in range(6)
        ],
    )
    befund = sm.auswerten(fahrtenbuch_pfad=fb, nachtlauf_pfad=None, ereignis_pfad=ep, stichtag="2026-11-27")

    assert befund.empfehlung.option == "eigene_staging_maschine"


def test_reproduzierbarkeit_und_cli_ausgabe(tmp_path: Path) -> None:
    """Gleiche Eingabe liefert bit-identische Markdown- und JSON-Ausgaben über die CLI."""
    fb = _schreibe_jsonl(tmp_path / "fahrtenbuch.jsonl", _beispiel_fahrtenbuch())
    nl = _schreibe_jsonl(tmp_path / "laufzeit.jsonl", _beispiel_nachtlauf())
    ep = _schreibe_jsonl(tmp_path / "ereignisse.jsonl", _beispiel_auslastung_entspannt())
    aus1 = tmp_path / "befund1.md"
    aus2 = tmp_path / "befund2.md"
    json_aus = tmp_path / "befund.json"

    argv1 = [
        "--fahrtenbuch",
        str(fb),
        "--nachtlauf",
        str(nl),
        "--ereignis-protokoll",
        str(ep),
        "--stichtag",
        "2026-11-27",
        "--tage",
        "14",
        "--aus",
        str(aus1),
        "--json-aus",
        str(json_aus),
    ]
    argv2 = [*argv1[:-4], "--aus", str(aus2)]

    assert sm.main(argv1) == 0
    assert sm.main(argv2) == 0

    text1 = aus1.read_text(encoding="utf-8")
    text2 = aus2.read_text(encoding="utf-8")
    assert text1 == text2
    assert "2026-11-13" in text1 and "2026-11-27" in text1
    assert "Gate-Dauer" in text1
    assert "Rot-Quote" in text1
    assert "Nachtlauf-Langsamläufer" in text1
    assert "15-Minuten-Auslastungsverlauf" in text1
    assert "Größerer Server" in text1
    assert "Eigene Maschine für Staging" in text1
    assert "Nichts kaufen" in text1
    assert "Trade-off" in text1

    daten = json.loads(json_aus.read_text(encoding="utf-8"))
    assert daten["empfehlung"]["option"] in {"nichts_kaufen", "eigene_staging_maschine", "groesserer_server"}
    assert daten["fahrtenbuch"]["urteile_gesamt"] == 8


def test_fehlende_oder_leere_eingaben_stuerzen_nicht_ab(tmp_path: Path) -> None:
    """Ohne Daten entsteht ein sauberer Bericht mit Hinweis auf fehlende Datengrundlage."""
    befund = sm.auswerten(
        fahrtenbuch_pfad=tmp_path / "fehlt_fb.jsonl",
        nachtlauf_pfad=tmp_path / "fehlt_nl.jsonl",
        ereignis_pfad=tmp_path / "fehlt_ep.jsonl",
        stichtag="2026-11-27",
    )
    markdown = sm.formatiere_markdown(befund)
    assert befund.fahrtenbuch.urteile_gesamt == 0
    assert befund.auslastung.messpunkte == 0
    assert befund.empfehlung.option == "nichts_kaufen"
    assert "Keine" in markdown or "keine" in markdown
