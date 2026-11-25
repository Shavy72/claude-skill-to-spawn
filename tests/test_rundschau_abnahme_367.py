"""Abnahme-Rundschau (#367): Rundschau-Auftrag, Pflicht-Inhalt, eine Abschluss-Quelle, Bild-Belege.

Echt laufen ``abschluss_paket.py`` (``rundschau-auftrag``, ``ablegen``, ``nachsehen``),
``belege_uebersicht.py`` und ``to_spawn/rundschau_deck.py`` gegen ein Repo aus echten Dateien
(Manifest, Konfig, Bau-Log, Briefkasten, Bild-Belege). Gestellt ist nichts.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from rundschau_beispiel import live_bereit_marker
from rundschau_beispiel import png as _png

from to_spawn import anleitung, bau_log, belege, briefkasten, rundschau_deck as rd, waechter_takt

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
SPEC = 367
_LINKS = [
    "--stage",
    "https://stage.example",
    "--rundschau",
    "https://r.example/367",
    "--tests",
    "https://t.example/367",
]


def _lauf(skript: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKRIPTE / skript), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})},
        timeout=120,
        check=False,
    )


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "TO_SPAWN_ABSCHLUSS_ORDNER": str(tmp_path / "abschluss"),
        "TO_SPAWN_WAECHTER_ORDNER": str(tmp_path / "w"),
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repo mit Manifest (2 Bau-Tickets + 1 Abnahme-Ticket), Staging-Konfig und Entscheidungen."""
    r = tmp_path / "projekt"
    (r / ".git").mkdir(parents=True)
    (r / ".to-spawn").mkdir()
    (r / ".to-spawn" / "config.json").write_text(
        json.dumps({"staging": {"url": "https://stage.example"}}), encoding="utf-8"
    )
    manifest = {
        "spec": SPEC,
        "tickets": {
            "10": {"title": "Filter zeigt offene Punkte"},
            "11": {"title": "Export als CSV"},
            "12": {"title": "Abnahme durch den Menschen"},
        },
    }
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(json.dumps(manifest), encoding="utf-8")
    bau_log.schreibe(r, 10, "entscheidung", frage="Trennzeichen", wahl="Semikolon", grund="Excel-Standard")
    fid = briefkasten.einwerfen("11", "Dateiname?", "export.csv", 3, [], hauptbaum=r)
    briefkasten.antworten("11", fid, "export.csv", ["Lastenheft A51"], "kurz und klar", hauptbaum=r)
    return r


def _deck_schreiben(
    repo: Path,
    ziel: Path,
    teil2: list[dict[str, Any]],
    *,
    teil1: list[dict[str, Any]] | None = None,
) -> Path:
    bild = repo.parent / "shot.png"
    bild.write_bytes(_png())
    eintraege = briefkasten.liste([10, 11, 12], log_repo=repo, hauptbaum=repo)
    texte = {
        "titel": f"Spec {SPEC} — Neu",
        "teil1": teil1
        if teil1 is not None
        else [
            {
                "titel": "Filter und Export",
                "kern": "Offene Punkte filtern und als CSV laden.",
                "bild": str(bild),
            }
        ],
        "teil2": [
            *{i: v for i, v in enumerate(teil2)}.values(),
            {"titel": "Offenes", "kern": "Abnahme #12 steht aus.", "art": "offenes"},
        ],
    }
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(rd.erzeuge(texte, eintraege), encoding="utf-8")
    return ziel


def test_rundschau_auftrag_schreibt_tickets_direktlinks_pflichtfolien_und_entscheidungen(
    repo: Path, tmp_path: Path
) -> None:
    erg = _lauf(
        "abschluss_paket.py",
        "rundschau-auftrag",
        str(SPEC),
        "--repo",
        str(repo),
        "--direkt",
        "Filter #10 (als Prüfer)=https://stage.example/filter",
        env=_env(tmp_path),
    )
    assert erg.returncode == 0, erg.stdout + erg.stderr
    pfad = Path(erg.stdout.strip().splitlines()[-1])
    assert pfad.resolve() == (repo / "docs" / "agents" / f"rundschau_auftrag_{SPEC}.md").resolve()
    assert pfad.is_file()
    text = pfad.read_text(encoding="utf-8")
    assert "#10" in text and "Filter zeigt offene Punkte" in text
    assert "#11" in text and "Export als CSV" in text
    assert "https://stage.example/filter" in text
    assert "https://stage.example" in text
    assert "Bild + Klick-Schritte" in text
    assert "Für dich entschieden" in text
    assert f"docs/agents/entscheidungen_{SPEC}.md" in text
    assert "Trennzeichen → Semikolon" in text
    assert "Dateiname? → export.csv" in text
    assert f"docs/agents/rundschau_{SPEC}.html" in text


def test_rundschau_auftrag_passwort_schutz_exit_2(repo: Path, tmp_path: Path) -> None:
    erg = _lauf(
        "abschluss_paket.py",
        "rundschau-auftrag",
        str(SPEC),
        "--repo",
        str(repo),
        "--direkt",
        "Filter=https://nutzer:geheim@stage.example/filter",
        env=_env(tmp_path),
    )
    assert erg.returncode == 2
    assert "Passwort" in erg.stderr
    assert not (repo / "docs" / "agents" / f"rundschau_auftrag_{SPEC}.md").exists()


def test_rundschau_deck_klickschritte_und_inhalt(tmp_path: Path) -> None:
    bild = tmp_path / "b.png"
    bild.write_bytes(_png())
    eintraege = [
        {
            "ticket": "10",
            "ts": "2026-11-24T09:00:00+01:00",
            "wer": "Bau-Session",
            "was": "Trennzeichen → Semikolon",
            "warum": "Excel",
            "beleg": "x",
        }
    ]
    texte = {
        "titel": "Spec 367",
        "teil1": [{"titel": "Filter", "kern": "Filtert sofort.", "bild": str(bild)}],
        "teil2": [
            {
                "titel": "Ticket #10 — Filter",
                "kern": "Filter auf Staging.",
                "bild": str(bild),
                "ticket": "10",
                "klickschritte": ["Stage öffnen", "Filter anklicken"],
            },
            {
                "titel": "Ticket #11 — Export",
                "kern": "CSV-Export.",
                "bild": str(bild),
                "ticket": "11",
                "klickschritte": ["Export klicken", "Datei prüfen"],
            },
            {"titel": "Offenes", "kern": "Nichts.", "art": "offenes"},
        ],
    }
    deck = rd.erzeuge(texte, eintraege)
    assert '<ol class="klickschritte">' in deck
    assert rd.pruefe(deck, eintraege, tickets=["10", "11"]) == []
    info = rd.inhalt(deck)
    assert info["bilder"] == 3
    assert info["klickschritte"] == 2
    assert info["entscheidungen"] is True

    # Ticket 11 ohne Klick-Schritte → Mangel
    del texte["teil2"][1]["klickschritte"]
    ohne_klick = rd.erzeuge(texte, eintraege)
    maengel = rd.pruefe(ohne_klick, eintraege, tickets=["10", "11"])
    assert any("#11" in m and "Klick-Schritte" in m for m in maengel), maengel


def test_ablegen_prueft_bild_klickschritte_repo_pfad_und_speichert_inhalt(repo: Path, tmp_path: Path) -> None:
    bild = repo.parent / "shot.png"
    bild.write_bytes(_png())
    deck_pfad = repo / "docs" / "agents" / f"rundschau_{SPEC}.html"

    # 1. Ticket #11 ohne Klick-Schritte → Exit 4, nichts abgelegt.
    _deck_schreiben(
        repo,
        deck_pfad,
        [
            {
                "titel": "Ticket #10 — Filter",
                "kern": "Filter läuft.",
                "bild": str(bild),
                "ticket": "10",
                "klickschritte": ["Stage öffnen", "Filter wählen"],
            },
            {
                "titel": "Ticket #11 — Export",
                "kern": "Export läuft.",
                "bild": str(bild),
                "ticket": "11",
            },
        ],
    )
    rot_klick = _lauf(
        "abschluss_paket.py",
        "ablegen",
        str(SPEC),
        "--repo",
        str(repo),
        *_LINKS,
        "--rundschau-datei",
        str(deck_pfad),
        env=_env(tmp_path),
    )
    assert rot_klick.returncode == 4, rot_klick.stdout + rot_klick.stderr
    assert "#11" in rot_klick.stdout and "Klick-Schritte" in rot_klick.stdout
    assert not (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").exists()

    # 2. Ticket #11 fehlt ganz (kein Bild, keine Klick-Schritte) → Exit 4.
    _deck_schreiben(
        repo,
        deck_pfad,
        [
            {
                "titel": "Ticket #10 — Filter",
                "kern": "Filter läuft.",
                "bild": str(bild),
                "ticket": "10",
                "klickschritte": ["Stage öffnen", "Filter wählen"],
            },
        ],
    )
    rot_bild = _lauf(
        "abschluss_paket.py",
        "ablegen",
        str(SPEC),
        "--repo",
        str(repo),
        *_LINKS,
        "--rundschau-datei",
        str(deck_pfad),
        env=_env(tmp_path),
    )
    assert rot_bild.returncode == 4, rot_bild.stdout + rot_bild.stderr
    assert "#11" in rot_bild.stdout and "Bild" in rot_bild.stdout
    assert not (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").exists()

    # 3. Gültiges Deck außerhalb des Repos → Exit 4.
    draussen = tmp_path / "ausserhalb.html"
    voll_teil2 = [
        {
            "titel": "Ticket #10 — Filter",
            "kern": "Filter läuft.",
            "bild": str(bild),
            "ticket": "10",
            "klickschritte": ["Stage öffnen", "Filter wählen"],
        },
        {
            "titel": "Ticket #11 — Export",
            "kern": "Export läuft.",
            "bild": str(bild),
            "ticket": "11",
            "klickschritte": ["Export klicken", "CSV öffnen"],
        },
    ]
    _deck_schreiben(repo, draussen, voll_teil2)
    rot_ort = _lauf(
        "abschluss_paket.py",
        "ablegen",
        str(SPEC),
        "--repo",
        str(repo),
        *_LINKS,
        "--rundschau-datei",
        str(draussen),
        env=_env(tmp_path),
    )
    assert rot_ort.returncode == 4, rot_ort.stdout + rot_ort.stderr
    assert "Repo" in rot_ort.stdout
    assert not (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").exists()

    # 4. Vollständiges Deck im Repo (relativer Pfad) + Selbstprüfung (#368) + Bereit-für-Live (#369) → Exit 0.
    _deck_schreiben(repo, deck_pfad, voll_teil2)
    live_bereit_marker(repo, SPEC)
    sp_dir = repo / ".to-spawn" / "selbstpruefung" / str(SPEC)
    sp_dir.mkdir(parents=True, exist_ok=True)
    (sp_dir / "ergebnis.json").write_text(
        json.dumps(
            {
                "spec": SPEC,
                "stage": "https://stage.example",
                "browser": "kopflos",
                "testdaten_bereinigt": True,
                "folgen": [
                    {
                        "ticket": "10",
                        "titel": "Ticket #10 — Filter",
                        "schritte": [{"schritt": "Stage öffnen", "ergebnis": "passt", "beleg": "HTTP 200"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    gruen = _lauf(
        "abschluss_paket.py",
        "ablegen",
        str(SPEC),
        "--repo",
        str(repo),
        *_LINKS,
        "--belege",
        "https://b.example/367",
        "--rundschau-datei",
        f"docs/agents/rundschau_{SPEC}.html",
        "--spec-fertig",
        "2026-11-24T10:00:00+01:00",
        env=_env(tmp_path),
    )
    assert gruen.returncode == 0, gruen.stdout + gruen.stderr
    ablage = json.loads((repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").read_text(encoding="utf-8"))
    paket = ablage["paket"]
    assert paket["rundschau_datei"] == f"docs/agents/rundschau_{SPEC}.html"
    assert paket["rundschau_inhalt"] == {"bilder": 3, "klickschritte": 2, "entscheidungen": True}

    probe = _lauf(
        "abschluss_paket.py",
        "nachsehen",
        str(SPEC),
        "--repo",
        str(repo),
        "--jetzt",
        "2026-11-24T13:00:00+01:00",
        "--dry-run",
        env=_env(tmp_path),
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert f"docs/agents/rundschau_{SPEC}.html" in probe.stdout
    assert "3 Folien mit Bild" in probe.stdout
    assert "2 Klick-Schrittfolgen" in probe.stdout
    assert "Entscheidungs-Folie: ja" in probe.stdout


def test_abschluss_anweisung_eine_quelle_und_rundschau_auftrag() -> None:
    modul_spec = importlib.util.spec_from_file_location("wache_367", SKRIPTE / "wache.py")
    assert modul_spec and modul_spec.loader
    wache = importlib.util.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(wache)

    quelle: str = anleitung.ABSCHLUSS_ANWEISUNG
    assert quelle in waechter_takt.PROMPT
    assert quelle in wache.PROMPT
    assert "abschluss_paket.py rundschau-auftrag {S}" in quelle
    assert "docs/agents/rundschau_{S}.html" in quelle
    # Beide Prompts formatieren weiter sauber mit ihren Platzhaltern.
    takt_text = waechter_takt.PROMPT.format(S=367, REPO="o/r", SKILL="/skill", LISTE="- x", NOTIZ="(leer)")
    wache_text = wache.PROMPT.format(S=367, REPO="o/r", DATUM="2026-11-25", TAKT=1800, SKILL="/skill")
    assert "abschluss_paket.py rundschau-auftrag 367" in takt_text
    assert "abschluss_paket.py rundschau-auftrag 367" in wache_text


def test_belege_uebersicht_bettet_bild_belege_als_img_ein(tmp_path: Path) -> None:
    r = tmp_path / "projekt"
    (r / ".git").mkdir(parents=True)
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(
        json.dumps(
            {
                "spec": SPEC,
                "tickets": {
                    "7": {"title": "Ansicht Desktop und Mobil"},
                    "8": {"title": "Nur Text-Beleg"},
                },
            }
        ),
        encoding="utf-8",
    )
    vh = r / "docs" / "verify-hard"
    (vh / "7").mkdir(parents=True)
    (vh / "7.md").write_text("# Beleg #7\n**ABNAHME: ABGENOMMEN**\n", encoding="utf-8")
    (vh / "7_desktop_a.png").write_bytes(_png())
    (vh / "7_mobil390_b.webp").write_bytes(b"RIFF\0\0\0\0WEBP")
    (vh / "7" / "detail.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    (vh / "70_fremd.png").write_bytes(_png())
    (vh / "8.md").write_text("# Beleg #8\n**ABNAHME: ABGENOMMEN**\n", encoding="utf-8")

    # to_spawn.belege.finde_bilder liefert genau die 3 Bilder von #7, nie #70 oder .md.
    gefunden = [p.relative_to(vh).as_posix() for p in belege.finde_bilder(vh, "7")]
    assert gefunden == ["7/detail.jpg", "7_desktop_a.png", "7_mobil390_b.webp"]

    # Standard-Ausgabeort docs/belege-uebersicht/<datum>_spec367.html → relative Pfade ../verify-hard/…
    erg = _lauf("belege_uebersicht.py", str(SPEC), "--repo", str(r), "--ohne-github")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    html_pfad = Path(erg.stdout.strip().splitlines()[-1])
    seite = html_pfad.read_text(encoding="utf-8")
    srcs = re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', seite)
    assert "../verify-hard/7_desktop_a.png" in srcs
    assert "../verify-hard/7_mobil390_b.webp" in srcs
    assert "../verify-hard/7/detail.jpg" in srcs
    assert not any("70_fremd.png" in s for s in srcs)
    for src in srcs:
        assert (html_pfad.parent / src).resolve().is_file()

    # Eigener Ausgabeort per --aus → relative Pfade stimmen ebenfalls.
    custom = tmp_path / "woanders" / "belege.html"
    erg2 = _lauf(
        "belege_uebersicht.py",
        str(SPEC),
        "--repo",
        str(r),
        "--ohne-github",
        "--aus",
        str(custom),
    )
    assert erg2.returncode == 0, erg2.stdout + erg2.stderr
    srcs2 = re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', custom.read_text(encoding="utf-8"))
    assert len(srcs2) == 3
    for src in srcs2:
        assert not Path(src).is_absolute()
        assert (custom.parent / src).resolve().is_file()
