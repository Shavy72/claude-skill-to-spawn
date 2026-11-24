"""Abnahme-Rundschau ist ein Deck mit drei Teilen und Entscheidungs-Grafik (#529).

Echt laufen Erzeuger und Prüfer (eine Definition in ``to_spawn/rundschau_deck.py``) auf
echten Dateien; das CLI ``abschluss_paket.py ablegen`` prüft die Datei. Gestellt ist nichts.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from to_spawn import rundschau_deck as rd

SKILL = Path(__file__).resolve().parent.parent
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="40" height="20"/></svg>'


def _eintrag(t: str, ts: str, was: str = "Frage → Wahl", **mehr: str) -> dict[str, str]:
    return {
        "ticket": t,
        "ts": ts,
        "wer": "Bau-Session",
        "was": was,
        "warum": "weil",
        "beleg": "x",
        **mehr,
    }


EINTRAEGE = [
    _eintrag("2", "2026-11-24T09:00:00+01:00", "Tabelle → SQLite"),
    _eintrag("2", "2026-11-24T10:00:00+01:00", "Farbe → Blau"),
    _eintrag("3", "2026-11-24T11:00:00+01:00", "Farbe → Grün"),
    _eintrag("8", "2026-11-24T12:00:00+01:00", "Knopf → rechts", ueberholt_durch="9"),
]


@pytest.fixture
def bild(tmp_path: Path) -> Path:
    p = tmp_path / "b.png"
    p.write_bytes(_PNG + b"\0" * 80)
    return p


def _texte(bild: Path) -> dict[str, Any]:
    return {
        "titel": "Spec 5 — Neu in der App",
        "teil1": [
            {
                "titel": "Schneller",
                "kern": "Alles lädt jetzt sofort.",
                "bild": str(bild),
            }
        ],
        "teil2": [
            {
                "titel": "Früher / Jetzt",
                "kern": "Vorher rot, jetzt grün.",
                "bild": str(bild),
            },
            {"titel": "Offenes", "kern": "Handy-Test steht aus.", "art": "offenes"},
        ],
    }


def _deck(bild: Path, eintraege: list[dict[str, str]] = EINTRAEGE) -> str:
    return rd.erzeuge(_texte(bild), eintraege)


def test_gueltiges_deck_besteht(bild: Path) -> None:
    assert rd.pruefe(_deck(bild), EINTRAEGE) == []


def test_markdown_und_einfache_html_seite_fallen_durch() -> None:
    md = "# Rundschau\n\n- Punkt 1\n- Punkt 2\n"
    html = "<html><body><h1>Rundschau</h1><p>Text</p></body></html>"
    for text in (md, html):
        maengel = rd.pruefe(text, EINTRAEGE)
        assert maengel and any("Deck" in m for m in maengel), maengel


def test_fehlender_teil_faellt_durch(bild: Path) -> None:
    deck = _deck(bild).replace('data-teil="2"', 'data-teil="1"')
    assert any("Teil 2" in m for m in rd.pruefe(deck, EINTRAEGE))


def test_falsche_reihenfolge_faellt_durch(bild: Path) -> None:
    deck = _deck(bild)
    a, b = (
        deck.index('<section class="folie" data-teil="1"'),
        deck.index('<section class="folie" data-teil="2"'),
    )
    ende = deck.index("</section>", b) + len("</section>")
    getauscht = deck[:a] + deck[b:ende] + deck[a:b] + deck[ende:]
    assert any("Reihenfolge" in m for m in rd.pruefe(getauscht, EINTRAEGE))


def test_entscheidung_ohne_ticket_oder_zeitpunkt_faellt_durch(bild: Path) -> None:
    deck = _deck(bild)
    ohne_ts = deck.replace('data-ts="2026-11-24T10:00:00+01:00"', 'data-ts=""')
    assert any("Zeitpunkt" in m or "#2" in m for m in rd.pruefe(ohne_ts, EINTRAEGE))
    ohne_ticket = deck.replace('data-ticket="3"', 'data-ticket=""')
    assert any("#3" in m for m in rd.pruefe(ohne_ticket, EINTRAEGE))


def test_fehlende_entscheidung_in_grafik_faellt_durch(bild: Path) -> None:
    mehr = [*EINTRAEGE, _eintrag("9", "2026-11-24T13:00:00+01:00", "Neu → da")]
    maengel = rd.pruefe(_deck(bild), mehr)
    assert any("#9" in m for m in maengel), maengel


def test_ueberholt_durch_erscheint(bild: Path) -> None:
    deck = _deck(bild)
    assert "überholt durch #9" in deck  # ausdrücklich
    assert (
        "überholt durch #3" in deck
    )  # gleiche Frage „Farbe“, später in #3 neu entschieden


def test_null_entscheidungen_leer_folie(bild: Path) -> None:
    deck = rd.erzeuge(_texte(bild), [])
    assert "Keine Entscheidungen" in deck
    assert rd.pruefe(deck, []) == []


def test_viele_entscheidungen_bleiben_pruefbar(bild: Path) -> None:
    viele = [
        _eintrag(
            str(i % 7 + 1),
            f"2026-11-24T{8 + i // 60:02d}:{i % 60:02d}:00+01:00",
            f"F{i} → W{i}",
        )
        for i in range(80)
    ]
    deck = rd.erzeuge(_texte(bild), viele)
    assert rd.pruefe(deck, viele) == []
    assert "overflow-x:auto" in deck


def test_teil1_textwueste_faellt_durch(bild: Path) -> None:
    t = _texte(bild)
    t["teil1"][0]["kern"] = " ".join(["wort"] * (rd.MAX_WOERTER_TEIL1 + 5))
    maengel = rd.pruefe(rd.erzeuge(t, EINTRAEGE), EINTRAEGE)
    assert any("Wörter" in m for m in maengel), maengel


def test_leeres_bild_faellt_durch(bild: Path) -> None:
    deck = _deck(bild).replace(base64.b64encode(_PNG + b"\0" * 80).decode(), "AAAA")
    assert any("Bild" in m for m in rd.pruefe(deck, EINTRAEGE))


def test_inhaltsfolie_teil2_ohne_bild_faellt_durch(bild: Path) -> None:
    t = _texte(bild)
    del t["teil2"][0]["bild"]
    with pytest.raises(ValueError, match="Bild"):
        rd.erzeuge(t, EINTRAEGE)


def test_svg_bild_gilt(tmp_path: Path) -> None:
    s = tmp_path / "b.svg"
    s.write_bytes(_SVG)
    t = _texte(s)
    assert rd.pruefe(rd.erzeuge(t, EINTRAEGE), EINTRAEGE) == []


def test_skill_fehlt_klare_meldung(
    tmp_path: Path, bild: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "TO_SPAWN_RUNDSCHAU_SKILL", str(tmp_path / "nichtda" / "SKILL.md")
    )
    assert not rd.skill_da()
    assert "rundschau" in rd.SKILL_FEHLT


def _cli(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "skripte" / "rundschau_deck.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})},
        timeout=60,
        check=False,
    )


def test_cli_rundlauf_erzeugen_dann_pruefen(tmp_path: Path, bild: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("x", encoding="utf-8")
    daten = tmp_path / "e.json"
    daten.write_text(json.dumps(EINTRAEGE), encoding="utf-8")
    texte = tmp_path / "t.json"
    texte.write_text(json.dumps(_texte(bild)), encoding="utf-8")
    ziel = tmp_path / "deck.html"
    env = {"TO_SPAWN_RUNDSCHAU_SKILL": str(skill)}
    e = _cli(
        "erzeugen",
        "--texte",
        str(texte),
        "--entscheidungen",
        str(daten),
        "--nach",
        str(ziel),
        env=env,
    )
    assert e.returncode == 0, e.stdout + e.stderr
    p = _cli("pruefen", str(ziel), "--entscheidungen", str(daten))
    assert p.returncode == 0, p.stdout + p.stderr
    ohne = _cli(
        "erzeugen",
        "--texte",
        str(texte),
        "--entscheidungen",
        str(daten),
        "--nach",
        str(ziel),
        env={"TO_SPAWN_RUNDSCHAU_SKILL": str(tmp_path / "weg")},
    )
    assert ohne.returncode == 5 and "rundschau" in (ohne.stdout + ohne.stderr)


def test_abschluss_paket_ohne_deck_kein_bereit(tmp_path: Path) -> None:
    md = tmp_path / "r.md"
    md.write_text("# Rundschau\n", encoding="utf-8")
    lauf = subprocess.run(
        [
            sys.executable,
            str(SKILL / "skripte" / "abschluss_paket.py"),
            "ablegen",
            "5",
            "--repo",
            str(tmp_path),
            "--stage",
            "https://s.example",
            "--rundschau",
            "https://r.example",
            "--tests",
            "https://t.example",
            "--rundschau-datei",
            str(md),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "TO_SPAWN_ABSCHLUSS_ORDNER": str(tmp_path / "a"),
        },
        timeout=60,
        check=False,
    )
    assert lauf.returncode == 4, lauf.stdout + lauf.stderr
    assert "Deck" in lauf.stdout + lauf.stderr
