"""Abnahme-Rundschau ist ein Deck mit drei Teilen und Entscheidungs-Grafik (#529).

Echt laufen Erzeuger und Prüfer (eine Definition in ``to_spawn/rundschau_deck.py``) auf
echten Dateien; das CLI ``abschluss_paket.py ablegen`` prüft die Datei. Gestellt ist nichts.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from rundschau_beispiel import live_bereit_marker
from rundschau_beispiel import png as _png

from to_spawn import rundschau_deck as rd

SKILL = Path(__file__).resolve().parent.parent


_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="240"><rect width="400" height="240"/></svg>'


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
    p.write_bytes(_png())
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
    assert (
        'width="360"' in deck
    )  # senkrechter Zeitstrahl, nicht 170 px je Eintrag in die Breite


def test_teil1_textwueste_faellt_durch(bild: Path) -> None:
    t = _texte(bild)
    t["teil1"][0]["kern"] = " ".join(["wort"] * (rd.MAX_WOERTER_TEIL1 + 5))
    maengel = rd.pruefe(rd.erzeuge(t, EINTRAEGE), EINTRAEGE)
    assert any("Wörter" in m for m in maengel), maengel


def test_leeres_bild_faellt_durch(bild: Path) -> None:
    deck = _deck(bild).replace(base64.b64encode(_png()).decode(), "AAAA")
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


# --- Fix-Runde 535 --------------------------------------------------------------


def _ohne_bild(deck: str, titel: str, ersatz: str = "") -> str:
    """Ersetzt in der Folie mit dieser Überschrift die Bild-Figur."""
    a = deck.index(f"<h2>{titel}</h2>")
    b, c = deck.index("<figure>", a), deck.index("</figure>", a) + len("</figure>")
    return deck[:b] + ersatz + deck[c:]


def test_pflicht_rundschau_datei_bei_abnahme(tmp_path: Path, bild: Path) -> None:
    def lauf(*extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SKILL / "skripte" / "abschluss_paket.py"), "ablegen", "5", "--repo", str(tmp_path),
             "--stage", "https://s.example", "--rundschau", "https://r.example", "--tests", "https://t.example", *extra],
            capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "TO_SPAWN_ABSCHLUSS_ORDNER": str(tmp_path / "a")},
        )  # fmt: skip

    ohne = lauf()
    assert ohne.returncode == 4, ohne.stdout + ohne.stderr
    assert "--rundschau-datei" in ohne.stdout + ohne.stderr
    assert not (tmp_path / ".to-spawn").exists(), "nichts darf abgelegt sein"
    deck = tmp_path / "d.html"
    deck.write_text(_deck(bild, []), encoding="utf-8")
    live_bereit_marker(tmp_path, 5)  # #369: Abnahme verlangt grüne Bereit-für-Live-Prüfung
    assert lauf("--rundschau-datei", str(deck)).returncode == 0
    assert lauf("--stand", "live").returncode == 0  # Pflicht gilt nur für die Abnahme


def test_aufseher_text_nennt_den_schalter() -> None:
    import importlib.util

    from to_spawn import waechter_takt

    spec = importlib.util.spec_from_file_location(
        "wache_529", SKILL / "skripte" / "wache.py"
    )
    assert spec and spec.loader
    wache = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wache)
    for prompt in (waechter_takt.PROMPT, wache.PROMPT):
        ablegen = prompt.index("abschluss_paket.py ablegen {S} --stand abnahme")
        assert "--rundschau-datei" in prompt[ablegen : ablegen + 200]


def test_offenes_ist_pflicht_in_teil_2(bild: Path) -> None:
    deck = _deck(bild).replace('data-art="offenes"', 'data-art="x"')
    assert any("Offenes" in m for m in rd.pruefe(deck, EINTRAEGE))
    t = _texte(bild)
    del t["teil2"][1]["art"]
    with pytest.raises(ValueError, match="Offenes"):
        rd.erzeuge(t, EINTRAEGE)


def test_bild_mindestmass_aus_dem_bildkopf(tmp_path: Path, bild: Path) -> None:
    klein = tmp_path / "k.png"
    klein.write_bytes(_png(20, 10))
    with pytest.raises(ValueError, match="kleiner"):
        rd.erzeuge(_texte(klein), EINTRAEGE)
    deck = _deck(bild).replace(
        base64.b64encode(_png()).decode(), base64.b64encode(_png(20, 10)).decode()
    )
    assert any("Bild" in m for m in rd.pruefe(deck, EINTRAEGE))
    jpeg = b"\xff\xd8\xff\xc0" + struct.pack(">HBHH", 17, 8, 240, 400) + b"\0" * 20
    webp = (
        b"RIFF\0\0\0\0WEBPVP8X\x0a\0\0\0\0\0\0\0"
        + (399).to_bytes(3, "little")
        + (239).to_bytes(3, "little")
    )
    assert rd.bild_masse(jpeg) == (400, 240)
    assert rd.bild_masse(webp) == (400, 240)
    assert rd.bild_masse(b"nichts") is None


def test_svg_viz_zaehlt_nur_mit_formen(bild: Path) -> None:
    deck = _deck(bild)
    leer = _ohne_bild(deck, "Früher / Jetzt", '<svg class="viz"></svg>')
    assert any("Bild" in m for m in rd.pruefe(leer, EINTRAEGE))
    voll = _ohne_bild(
        deck, "Früher / Jetzt", '<svg class="viz"><rect width="9" height="9"/></svg>'
    )
    assert rd.pruefe(voll, EINTRAEGE) == []


def test_wortgrenze_zaehlt_svg_text_mit(bild: Path) -> None:
    text = " ".join(["wort"] * (rd.MAX_WOERTER_TEIL1 + 5))
    deck = _ohne_bild(
        _deck(bild), "Schneller", f'<svg class="viz"><text>{text}</text></svg>'
    )
    assert any("Wörter" in m for m in rd.pruefe(deck, EINTRAEGE))


def test_grafik_nur_sichtbar_und_im_zeitstrahl(bild: Path) -> None:
    deck = _deck(bild)
    ausserhalb = deck.replace('<svg class="zeitstrahl"', '<svg class="anderes"')
    assert any("sichtbare" in m for m in rd.pruefe(ausserhalb, EINTRAEGE))
    versteckt = deck.replace(
        '<svg class="zeitstrahl"', '<div style="display: none"><svg class="zeitstrahl"'
    )
    versteckt = versteckt.replace("</svg></section>", "</svg></div></section>")
    assert any("sichtbare" in m for m in rd.pruefe(versteckt, EINTRAEGE))
    hidden = deck.replace(
        '<svg class="zeitstrahl"', '<div hidden><svg class="zeitstrahl"'
    ).replace("</svg></section>", "</svg></div></section>")
    assert any("sichtbare" in m for m in rd.pruefe(hidden, EINTRAEGE))


def test_entscheidung_braucht_frage_und_wahl_im_text(bild: Path) -> None:
    deck = _deck(bild)
    assert any(
        "Frage" in m for m in rd.pruefe(deck.replace(">Tabelle<", ">Weg<"), EINTRAEGE)
    )
    assert any(
        "Wahl" in m for m in rd.pruefe(deck.replace("→ SQLite", "→ ?"), EINTRAEGE)
    )


def test_lange_texte_werden_umbrochen_nicht_gekappt(bild: Path) -> None:
    lang = "Welche Datenbank nehmen wir für die neue Auswertung der Handy-Zustände im Dashboard heute"
    e = [
        _eintrag(
            "4",
            "2026-11-24T09:00:00+01:00",
            f"{lang} → Die zweite, weil sie schon läuft und niemand sie warten muss",
        )
    ]
    deck = rd.erzeuge(_texte(bild), e)
    assert rd.pruefe(deck, e) == []
    assert "heute" in deck and "warten" in deck and "muss</text>" in deck


def test_ticket_spuren_und_senkrechte_form(bild: Path) -> None:
    deck = rd.erzeuge(_texte(bild), EINTRAEGE)
    for t in ("2", "3", "8"):
        assert deck.count(f"Ticket #{t}</text>") == 1


def test_aussehen_bedienung_zaehler_und_hell_dunkel(bild: Path) -> None:
    deck = _deck(bild)
    assert 'id="zaehler"' in deck and " / 5</span>" in deck  # Titel + 1 + 2 + Schluss
    assert 'id="weiter"' in deck and 'id="zurueck"' in deck and 'id="bar"' in deck
    assert "prefers-color-scheme:dark" in deck and "min-height:48px" in deck
    assert deck.count("data-label=") == 5
    assert 'data-art="titel"' in deck and "Spec 5" in deck
    import re

    assert not re.search(
        r'(?:src|href)="https?:|@import|url\(\s*https?:', deck
    )  # keine externen Dateien
    assert "font:17px" in deck  # Fließtext >= 16 px


def test_titel_folie_mit_datum(bild: Path) -> None:
    t = _texte(bild)
    t["datum"] = "24.11.2026"
    assert "24.11.2026" in rd.erzeuge(t, EINTRAEGE)


def test_ueberholt_nur_bei_gleicher_frage() -> None:
    e = [
        _eintrag("1", "2026-11-24T09:00:00+01:00", "Farbe → Blau"),
        _eintrag("2", "2026-11-24T10:00:00+01:00", "Schrift → Groß"),
        _eintrag("3", "2026-11-24T11:00:00+01:00", "Farbe → Grün"),
    ]
    assert rd.ueberholt_map(e) == {0: "3"}


def test_svg_text_in_tspan_zaehlt_bei_wortgrenze(bild: Path) -> None:
    text = " ".join(["wort"] * (rd.MAX_WOERTER_TEIL1 + 5))
    deck = _ohne_bild(
        _deck(bild),
        "Schneller",
        f'<svg class="viz"><rect width="9" height="9"/><text><tspan>{text}</tspan></text></svg>',
    )
    assert any("Wörter" in m for m in rd.pruefe(deck, EINTRAEGE))


@pytest.mark.parametrize(
    "form",
    [
        '<rect width="0" height="0"/>',
        '<rect width="9" height="0"/>',
        '<circle r="0"/>',
        '<line x1="1" y1="1" x2="1" y2="1"/>',
        '<path d=""/>',
    ],
)
def test_svg_viz_ohne_sichtbare_ausdehnung_gilt_nicht(bild: Path, form: str) -> None:
    deck = _ohne_bild(_deck(bild), "Früher / Jetzt", f'<svg class="viz">{form}</svg>')
    assert any("Bild" in m for m in rd.pruefe(deck, EINTRAEGE))


@pytest.mark.parametrize(
    ("alt", "neu"),
    [
        ('data-teil="1" data-label', 'data-teil="1" style="display:none" data-label'),
        (
            'data-teil="1" data-label',
            'data-teil="1" style="visibility: hidden" data-label',
        ),
        (
            'data-teil="1" data-label',
            'data-teil="1" style="color:transparent" data-label',
        ),
        ('data-teil="1" data-label', 'data-teil="1" hidden data-label'),
    ],
)
def test_versteckte_teil1_folie_zaehlt_nicht(bild: Path, alt: str, neu: str) -> None:
    deck = _deck(bild)
    assert alt in deck
    maengel = rd.pruefe(deck.replace(alt, neu), EINTRAEGE)
    assert any("versteckt" in m or "nur die Titel" in m for m in maengel), maengel


def test_versteckte_teil1_folie_per_klasse_zaehlt_nicht(bild: Path) -> None:
    deck = _deck(bild).replace("</style>", ".weg{color:transparent}</style>", 1)
    deck = deck.replace(
        'class="folie" data-teil="1"', 'class="folie weg" data-teil="1"'
    )
    assert 'class="folie weg"' in deck
    assert any(
        "versteckt" in m or "nur die Titel" in m for m in rd.pruefe(deck, EINTRAEGE)
    )


def test_notweg_ohne_rundschau_traegt_vermerk(tmp_path: Path) -> None:
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
            "fehlt",
            "--tests",
            "fehlt",
            "--ohne-rundschau",
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
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    paket = tmp_path / ".to-spawn" / "abschluss_5_paket.json"
    assert "nicht abnahmebereit" in paket.read_text(encoding="utf-8")
