"""KI-Selbstprüfung klickt die Rundschau gegen Staging (#368).

Echt laufen ``skripte/abschluss_paket.py`` (``selbstpruefung planen|sammeln``, ``ablegen``,
``nachsehen``) und ``to_spawn/rundschau_deck.py`` gegen ein Repo aus echten Dateien
(Manifest, Konfig, Rundschau-Auftrag, Rundschau-Deck, Ergebnis-Datei). Gestellt ist nichts.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from rundschau_beispiel import png as _png

from to_spawn import anleitung, rundschau_deck as rd, waechter_takt

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
SPEC = 368
_LINKS = [
    "--stage",
    "https://stage.example",
    "--rundschau",
    "https://r.example/368",
    "--tests",
    "https://t.example/368",
]


def _lauf(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKRIPTE / "abschluss_paket.py"), *args],
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


def _deck_schreiben(repo: Path, ziel: Path) -> Path:
    bild = repo.parent / "shot_368.png"
    bild.write_bytes(_png())
    texte: dict[str, Any] = {
        "titel": f"Spec {SPEC} — Rundschau",
        "teil1": [
            {
                "titel": "Filter und Export",
                "kern": "Neue Filter- und Export-Funktionen auf Staging.",
                "bild": str(bild),
            }
        ],
        "teil2": [
            {
                "titel": "Ticket #10 — Filter",
                "kern": "Filter zeigt offene Punkte.",
                "bild": str(bild),
                "ticket": "10",
                "klickschritte": [
                    "Staging-Startseite öffnen",
                    "Filter auf „Offen“ umstellen und Treffer prüfen",
                ],
            },
            {
                "titel": "Ticket #11 — CSV-Export",
                "kern": "Export lädt CSV herunter.",
                "bild": str(bild),
                "ticket": "11",
                "klickschritte": [
                    "Export-Ansicht öffnen",
                    "CSV-Download klicken und Spalten prüfen",
                ],
            },
            {"titel": "Offenes", "kern": "Abnahme #12 offen.", "art": "offenes"},
        ],
    }
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(rd.erzeuge(texte, []), encoding="utf-8")
    return ziel


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repo mit Manifest, staging.url, Rundschau-Auftrag und Rundschau-Deck."""
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
            "11": {"title": "CSV-Export"},
            "12": {"title": "Abnahme durch den Menschen"},
        },
    }
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    auftrag = _lauf(
        "rundschau-auftrag",
        str(SPEC),
        "--repo",
        str(r),
        "--direkt",
        "Filter #10=https://stage.example/filter",
        env=_env(tmp_path),
    )
    assert auftrag.returncode == 0, auftrag.stdout + auftrag.stderr
    _deck_schreiben(r, r / "docs" / "agents" / f"rundschau_{SPEC}.html")
    return r


def _ergebnis_daten(*, mit_fehler: bool = False, bereinigt: bool = True) -> dict[str, Any]:
    schritt2: dict[str, Any] = {
        "schritt": "Filter auf „Offen“ umstellen und Treffer prüfen",
        "ergebnis": "passt nicht" if mit_fehler else "passt",
        "beleg": "docs/verify-hard/selbstpruefung_368_10_2.png",
        "hinweis": "Filter-Knopf liefert HTTP 500" if mit_fehler else "",
    }
    return {
        "spec": SPEC,
        "stage": "https://stage.example",
        "browser": "kopflos",
        "testdaten_bereinigt": bereinigt,
        "folgen": [
            {
                "ticket": "10",
                "titel": "Ticket #10 — Filter",
                "schritte": [
                    {
                        "schritt": "Staging-Startseite öffnen",
                        "ergebnis": "passt",
                        "beleg": "HTTP 200, Titel „App“ sichtbar",
                        "hinweis": "",
                    },
                    schritt2,
                ],
            },
            {
                "ticket": "11",
                "titel": "Ticket #11 — CSV-Export",
                "schritte": [
                    {
                        "schritt": "Export-Ansicht öffnen",
                        "ergebnis": "passt",
                        "beleg": "docs/verify-hard/selbstpruefung_368_11_1.png",
                        "hinweis": "",
                    },
                    {
                        "schritt": "CSV-Download klicken und Spalten prüfen",
                        "ergebnis": "passt",
                        "beleg": "CSV enthält Kopfzeile id;titel;status",
                        "hinweis": "",
                    },
                ],
            },
        ],
    }


def _ergebnis_pfad(repo: Path) -> Path:
    return repo / ".to-spawn" / "selbstpruefung" / str(SPEC) / "ergebnis.json"


def test_rundschau_deck_extrahiert_klickschritte(repo: Path) -> None:
    html_text = (repo / "docs" / "agents" / f"rundschau_{SPEC}.html").read_text(encoding="utf-8")
    folgen = rd.klickschritte(html_text)
    assert len(folgen) == 2
    assert folgen[0]["ticket"] == "10"
    assert folgen[0]["schritte"] == [
        "Staging-Startseite öffnen",
        "Filter auf „Offen“ umstellen und Treffer prüfen",
    ]
    assert folgen[1]["ticket"] == "11"
    assert folgen[1]["schritte"] == [
        "Export-Ansicht öffnen",
        "CSV-Download klicken und Spalten prüfen",
    ]


def test_selbstpruefung_planen_schreibt_auftrag_mit_klickschritten_und_testdaten_regel(
    repo: Path, tmp_path: Path
) -> None:
    erg = _lauf("selbstpruefung", "planen", str(SPEC), "--repo", str(repo), env=_env(tmp_path))
    assert erg.returncode == 0, erg.stdout + erg.stderr
    auftrag_datei = repo / ".to-spawn" / "selbstpruefung" / str(SPEC) / "auftrag.md"
    lauf_datei = repo / ".to-spawn" / "selbstpruefung" / str(SPEC) / "lauf.json"
    assert auftrag_datei.is_file()
    assert lauf_datei.is_file()
    text = auftrag_datei.read_text(encoding="utf-8")
    assert "https://stage.example" in text
    assert "https://stage.example/filter" in text
    assert "Staging-Startseite öffnen" in text
    assert "CSV-Download klicken und Spalten prüfen" in text
    assert "passt nicht" in text and "ergebnis.json" in text
    # AC 4: keine Testdaten auf Staging zurücklassen.
    assert "nur lesen und klicken" in text.lower()
    assert "dummy-daten" in text.lower() and "löschen" in text.lower()
    # Browser-Entscheidung (kopflos).
    assert "kopflos" in text.lower()
    # Zweiter Aufruf bei aktiver Sperre → Exit 4.
    doppelt = _lauf("selbstpruefung", "planen", str(SPEC), "--repo", str(repo), env=_env(tmp_path))
    assert doppelt.returncode == 4, doppelt.stdout + doppelt.stderr


def test_selbstpruefung_planen_passwort_schutz_und_staging_sanitierung(
    repo: Path, tmp_path: Path
) -> None:
    rot = _lauf(
        "selbstpruefung",
        "planen",
        str(SPEC),
        "--repo",
        str(repo),
        "--stage",
        "https://nutzer:geheim@stage.example",
        env=_env(tmp_path),
    )
    assert rot.returncode == 2
    assert "Passwort" in rot.stderr
    assert not (repo / ".to-spawn" / "selbstpruefung" / str(SPEC) / "auftrag.md").exists()

    # Auch Zugangsdaten in config.json staging.url landen nie im Auftrag.
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps({"staging": {"url": "https://user:geheim123@stage.example/app"}}),
        encoding="utf-8",
    )
    ok = _lauf("selbstpruefung", "planen", str(SPEC), "--repo", str(repo), env=_env(tmp_path))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    auftrag = (repo / ".to-spawn" / "selbstpruefung" / str(SPEC) / "auftrag.md").read_text(
        encoding="utf-8"
    )
    assert "https://stage.example/app" in auftrag
    assert "geheim123" not in auftrag


def test_selbstpruefung_sammeln_fehlt_kaputt_gruen_und_passt_nicht(
    repo: Path, tmp_path: Path
) -> None:
    env = _env(tmp_path)
    assert _lauf("selbstpruefung", "planen", str(SPEC), "--repo", str(repo), env=env).returncode == 0
    # 1. Ergebnis fehlt → Exit 3, Sperre wird freigegeben.
    fehlt = _lauf("selbstpruefung", "sammeln", str(SPEC), "--repo", str(repo), env=env)
    assert fehlt.returncode == 3, fehlt.stdout + fehlt.stderr
    assert not (repo / ".to-spawn" / "selbstpruefung" / str(SPEC) / "lauf.json").exists()

    # 2. Ungültiges Ergebnis (Beleg fehlt) → Exit 1.
    kaputt = _ergebnis_daten()
    kaputt["folgen"][0]["schritte"][0]["beleg"] = ""
    _ergebnis_pfad(repo).write_text(json.dumps(kaputt), encoding="utf-8")
    rot = _lauf("selbstpruefung", "sammeln", str(SPEC), "--repo", str(repo), env=env)
    assert rot.returncode == 1, rot.stdout + rot.stderr
    assert not (repo / "docs" / "agents" / f"selbstpruefung_{SPEC}.md").exists()

    # 3. Ergebnis mit „passt nicht“ → Exit 0, Marker nennt den Fehler-Schritt.
    _ergebnis_pfad(repo).write_text(json.dumps(_ergebnis_daten(mit_fehler=True)), encoding="utf-8")
    sammel = _lauf("selbstpruefung", "sammeln", str(SPEC), "--repo", str(repo), env=env)
    assert sammel.returncode == 0, sammel.stdout + sammel.stderr
    marker = repo / "docs" / "agents" / f"selbstpruefung_{SPEC}.md"
    assert marker.is_file()
    text = marker.read_text(encoding="utf-8")
    assert "3 von 4" in text and "1 passt nicht" in text
    assert "Filter auf „Offen“ umstellen und Treffer prüfen" in text
    assert "Filter-Knopf liefert HTTP 500" in text

    # 4. Nach „passt nicht“ darf planen erneut starten (Reparatur-Schleife), löscht altes ergebnis.json.
    neu = _lauf("selbstpruefung", "planen", str(SPEC), "--repo", str(repo), env=env)
    assert neu.returncode == 0, neu.stdout + neu.stderr
    assert not _ergebnis_pfad(repo).exists()

    # 5. Alles grün → Exit 0, danach zweites planen/sammeln → Exit 4.
    _ergebnis_pfad(repo).write_text(json.dumps(_ergebnis_daten(mit_fehler=False)), encoding="utf-8")
    gruen = _lauf("selbstpruefung", "sammeln", str(SPEC), "--repo", str(repo), env=env)
    assert gruen.returncode == 0, gruen.stdout + gruen.stderr
    assert "4 von 4" in marker.read_text(encoding="utf-8")
    assert _lauf("selbstpruefung", "sammeln", str(SPEC), "--repo", str(repo), env=env).returncode == 4
    assert _lauf("selbstpruefung", "planen", str(SPEC), "--repo", str(repo), env=env).returncode == 4


def test_ablegen_verweigert_ohne_ergebnis_und_zeigt_passt_nicht_im_paket(
    repo: Path, tmp_path: Path
) -> None:
    env = _env(tmp_path)
    deck = f"docs/agents/rundschau_{SPEC}.html"
    # 1. Ohne Ergebnis-Datei bei gesetzter staging.url → Exit 4, nichts abgelegt.
    verweigert = _lauf(
        "ablegen",
        str(SPEC),
        "--repo",
        str(repo),
        *_LINKS,
        "--rundschau-datei",
        deck,
        "--spec-fertig",
        "2026-11-25T09:00:00+01:00",
        env=env,
    )
    assert verweigert.returncode == 4, verweigert.stdout + verweigert.stderr
    assert "Selbstprüfung" in verweigert.stdout and "ergebnis.json" in verweigert.stdout
    assert not (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").exists()

    # 2. Mit „passt nicht“-Ergebnis → ablegen erlaubt (Exit 0), Schritt steht sichtbar im Paket.
    assert _lauf("selbstpruefung", "planen", str(SPEC), "--repo", str(repo), env=env).returncode == 0
    _ergebnis_pfad(repo).write_text(json.dumps(_ergebnis_daten(mit_fehler=True)), encoding="utf-8")
    assert _lauf("selbstpruefung", "sammeln", str(SPEC), "--repo", str(repo), env=env).returncode == 0

    abgelegt = _lauf(
        "ablegen",
        str(SPEC),
        "--repo",
        str(repo),
        *_LINKS,
        "--belege",
        "https://b.example/368",
        "--rundschau-datei",
        deck,
        "--spec-fertig",
        "2026-11-25T09:00:00+01:00",
        env=env,
    )
    assert abgelegt.returncode == 0, abgelegt.stdout + abgelegt.stderr
    probe = _lauf(
        "nachsehen",
        str(SPEC),
        "--repo",
        str(repo),
        "--jetzt",
        "2026-11-25T12:00:00+01:00",
        "--dry-run",
        env=env,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "KI-Selbstprüfung" in probe.stdout
    assert "passt nicht" in probe.stdout
    assert "Filter auf „Offen“ umstellen und Treffer prüfen" in probe.stdout
    assert "Filter-Knopf liefert HTTP 500" in probe.stdout
    assert f"docs/agents/selbstpruefung_{SPEC}.md" in probe.stdout

    # 3. --stand live verlangt keine Selbstprüfung.
    _ergebnis_pfad(repo).unlink()
    live = _lauf(
        "ablegen",
        str(SPEC),
        "--repo",
        str(repo),
        *_LINKS,
        "--stand",
        "live",
        "--spec-fertig",
        "2026-11-25T09:00:00+01:00",
        env=env,
    )
    assert live.returncode == 0, live.stdout + live.stderr


def test_ohne_staging_url_meldet_klar_und_paket_nennt_grund(
    repo: Path, tmp_path: Path
) -> None:
    env = _env(tmp_path)
    (repo / ".to-spawn" / "config.json").write_text(json.dumps({}), encoding="utf-8")

    # planen meldet klar mit Exit 2 und schreibt keinen Auftrag.
    plan = _lauf("selbstpruefung", "planen", str(SPEC), "--repo", str(repo), env=env)
    assert plan.returncode == 2, plan.stdout + plan.stderr
    assert "Selbstprüfung nicht möglich, weil" in plan.stdout
    assert "staging.url" in plan.stdout
    assert not (repo / ".to-spawn" / "selbstpruefung" / str(SPEC) / "auftrag.md").exists()

    # ablegen klappt ohne Ergebnis-Datei und nennt „Selbstprüfung nicht möglich, weil …“ im Paket.
    abgelegt = _lauf(
        "ablegen",
        str(SPEC),
        "--repo",
        str(repo),
        *_LINKS,
        "--rundschau-datei",
        f"docs/agents/rundschau_{SPEC}.html",
        "--spec-fertig",
        "2026-11-25T09:00:00+01:00",
        env=env,
    )
    assert abgelegt.returncode == 0, abgelegt.stdout + abgelegt.stderr
    probe = _lauf(
        "nachsehen",
        str(SPEC),
        "--repo",
        str(repo),
        "--jetzt",
        "2026-11-25T12:00:00+01:00",
        "--dry-run",
        env=env,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "Selbstprüfung nicht möglich, weil" in probe.stdout
    assert "staging.url" in probe.stdout

    # Auch staging.ohne: true meldet klar und blockiert ablegen nicht.
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps({"staging": {"ohne": True, "url": "https://stage.example"}}),
        encoding="utf-8",
    )
    ohne = _lauf("selbstpruefung", "planen", str(SPEC), "--repo", str(repo), env=env)
    assert ohne.returncode == 2, ohne.stdout + ohne.stderr
    assert "Selbstprüfung nicht möglich, weil" in ohne.stdout
    assert "staging.ohne" in ohne.stdout


def test_selbstpruefung_fallback_auf_rundschau_auftrag_und_testdaten_warnung(
    repo: Path, tmp_path: Path
) -> None:
    env = _env(tmp_path)
    # Ohne Rundschau-HTML nutzt planen den Rundschau-Auftrag als Quelle.
    (repo / "docs" / "agents" / f"rundschau_{SPEC}.html").unlink()
    plan = _lauf("selbstpruefung", "planen", str(SPEC), "--repo", str(repo), env=env)
    assert plan.returncode == 0, plan.stdout + plan.stderr
    auftrag = (repo / ".to-spawn" / "selbstpruefung" / str(SPEC) / "auftrag.md").read_text(
        encoding="utf-8"
    )
    assert "#10" in auftrag and "https://stage.example/filter" in auftrag

    # Kaputtes ergebnis.json blockiert ablegen mit Exit 4.
    _deck_schreiben(repo, repo / "docs" / "agents" / f"rundschau_{SPEC}.html")
    _ergebnis_pfad(repo).write_text("{kaputt", encoding="utf-8")
    kaputt_ablage = _lauf(
        "ablegen",
        str(SPEC),
        "--repo",
        str(repo),
        *_LINKS,
        "--rundschau-datei",
        f"docs/agents/rundschau_{SPEC}.html",
        env=env,
    )
    assert kaputt_ablage.returncode == 4, kaputt_ablage.stdout + kaputt_ablage.stderr
    assert "Selbstprüfung ungültig" in kaputt_ablage.stdout

    # testdaten_bereinigt: false erscheint als Warnung im Marker und im Paket.
    _ergebnis_pfad(repo).write_text(
        json.dumps(_ergebnis_daten(mit_fehler=False, bereinigt=False)), encoding="utf-8"
    )
    assert _lauf("selbstpruefung", "sammeln", str(SPEC), "--repo", str(repo), env=env).returncode == 0
    marker_inhalt = (repo / "docs" / "agents" / f"selbstpruefung_{SPEC}.md").read_text(
        encoding="utf-8"
    )
    assert "nicht vollständig bereinigt" in marker_inhalt
    assert (
        _lauf(
            "ablegen",
            str(SPEC),
            "--repo",
            str(repo),
            *_LINKS,
            "--rundschau-datei",
            f"docs/agents/rundschau_{SPEC}.html",
            "--spec-fertig",
            "2026-11-25T09:00:00+01:00",
            env=env,
        ).returncode
        == 0
    )
    probe = _lauf(
        "nachsehen",
        str(SPEC),
        "--repo",
        str(repo),
        "--jetzt",
        "2026-11-25T12:00:00+01:00",
        "--dry-run",
        env=env,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "nicht vollständig bereinigt" in probe.stdout


def test_pfade_agenten_gilt_auch_fuer_selbstpruefung_marker(
    repo: Path, tmp_path: Path
) -> None:
    env = _env(tmp_path)
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps(
            {
                "staging": {"url": "https://stage.example"},
                "pfade": {"agenten": "eigen/agenten"},
            }
        ),
        encoding="utf-8",
    )
    _deck_schreiben(repo, repo / "eigen" / "agenten" / f"rundschau_{SPEC}.html")
    assert _lauf("selbstpruefung", "planen", str(SPEC), "--repo", str(repo), env=env).returncode == 0
    _ergebnis_pfad(repo).write_text(json.dumps(_ergebnis_daten()), encoding="utf-8")
    assert _lauf("selbstpruefung", "sammeln", str(SPEC), "--repo", str(repo), env=env).returncode == 0
    assert (repo / "eigen" / "agenten" / f"selbstpruefung_{SPEC}.md").is_file()


def test_aufseher_prompts_enthalten_selbstpruefung_nach_rundschau_vor_ablegen() -> None:
    modul_spec = importlib.util.spec_from_file_location("wache_368", SKRIPTE / "wache.py")
    assert modul_spec and modul_spec.loader
    wache = importlib.util.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(wache)

    for prompt in (anleitung.ABSCHLUSS_ANWEISUNG, waechter_takt.PROMPT, wache.PROMPT):
        stellen = [
            prompt.index(k)
            for k in (
                "abschluss_paket.py rundschau-auftrag {S}",
                "abschluss_paket.py selbstpruefung planen {S}",
                "abschluss_paket.py selbstpruefung sammeln {S}",
                "abschluss_paket.py ablegen {S} --stand abnahme",
            )
        ]
        assert stellen == sorted(stellen)
        assert "genau einen frischen Helfer" in prompt
    assert "docs/agents/selbstpruefung_{S}.md" in wache.PROMPT
