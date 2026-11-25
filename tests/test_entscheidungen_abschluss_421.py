"""Entscheidungs-Log entsteht mit dem Abschluss-Paket, nicht erst bei „SPEC FERTIG“ (#421).

Der Aufseher legt das Abschluss-Paket ``--stand abnahme`` ab, solange noch die
``checkpoint:human``-Abnahme offen ist — ``capo.uebersicht()`` lief bisher nur bei
„SPEC FERTIG“, ``docs/agents/entscheidungen_<S>.md`` fehlte darum im Lauf. Echt laufen
das CLI ``abschluss_paket.py`` gegen ein Repo aus echten Dateien (Manifest, Bau-Log,
Briefkasten); gestellt ist nichts (kein gh, kein Mail-Befehl nötig).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rundschau_beispiel import rundschau_args
from to_spawn import bau_log, briefkasten

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
SPEC = 421
_LINKS = [
    "--stage",
    "https://stage.example",
    "--rundschau",
    "https://r.example",
    "--tests",
    "https://t.example",
]
_FERTIG = "2026-11-24T04:25:00+01:00"


def _unter(befehl: str, repo: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SKRIPTE / "abschluss_paket.py"),
            befehl,
            str(SPEC),
            "--repo",
            str(repo),
            *args,
            *(
                rundschau_args(repo, SPEC)  # #529: Abnahme verlangt ein gültiges Deck
                if befehl == "ablegen" and "--dry-run" not in args
                else []
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "TO_SPAWN_ABSCHLUSS_ORDNER": str(tmp_path / "abschluss"),
            "TO_SPAWN_WAECHTER_ORDNER": str(tmp_path / "w"),
        },
        timeout=120,
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Spec mit Tickets 2, 3 und 8 (Abnahme offen): Entscheidung im Bau-Log zu 2, Aufseher-Antwort zu 3."""
    r = tmp_path / "projekt"
    manifest = {
        "spec": SPEC,
        "tickets": {
            "2": {"title": "Knopf zeigt den Status"},
            "3": {"title": "Handy schaltet sich aus"},
            "8": {"title": "Abnahme durch den Menschen"},
        },
    }
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(json.dumps(manifest), encoding="utf-8")
    bau_log.schreibe(r, 2, "entscheidung", frage="Welche Tabelle", wahl="SQLite", grund="schon da")
    fid = briefkasten.einwerfen("3", "Blocker erledigt?", "weiterbauen", 3, [], hauptbaum=r)
    briefkasten.antworten("3", fid, "weiterbauen", ["Lastenheft A46"], "Blocker sind zu", hauptbaum=r)
    return r


def _datei(repo: Path) -> Path:
    return repo / "docs" / "agents" / f"entscheidungen_{SPEC}.md"


def test_ablegen_schreibt_entscheidungen_mit_beiden_quellen(repo: Path, tmp_path: Path) -> None:
    lauf = _unter(
        "ablegen",
        repo,
        tmp_path,
        *_LINKS,
        "--stand",
        "abnahme",
        "--spec-fertig",
        _FERTIG,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    datei = _datei(repo)
    assert datei.is_file(), "entscheidungen_<S>.md fehlt nach dem Abschluss-Paket"
    zeilen = [z for z in datei.read_text(encoding="utf-8").splitlines() if z.startswith("| #")]
    assert any(z.startswith("| #2 | Welche Tabelle → SQLite | schon da | Bau-Log #2") for z in zeilen), zeilen
    assert "| #3 | Blocker erledigt? → weiterbauen | Blocker sind zu | Lastenheft A46 |" in zeilen, zeilen


def test_ablegen_probe_schreibt_keine_entscheidungen(repo: Path, tmp_path: Path) -> None:
    lauf = _unter("ablegen", repo, tmp_path, *_LINKS, "--spec-fertig", _FERTIG, "--dry-run")
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert not _datei(repo).exists()


def test_abschluss_text_verlinkt_entscheidungen(repo: Path, tmp_path: Path) -> None:
    assert _unter("ablegen", repo, tmp_path, *_LINKS, "--spec-fertig", _FERTIG).returncode == 0
    probe = _unter("nachsehen", repo, tmp_path, "--jetzt", "2026-11-24T23:00:00+01:00", "--dry-run")
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert f"docs/agents/entscheidungen_{SPEC}.md" in probe.stdout


def test_wache_ende_satz_passt_zum_ablauf() -> None:
    modul_spec = importlib.util.spec_from_file_location("wache_421", SKRIPTE / "wache.py")
    assert modul_spec and modul_spec.loader
    wache = importlib.util.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(wache)
    prompt: str = wache.PROMPT
    assert "capo hat docs/agents/entscheidungen_{S}.md geschrieben" not in prompt
    ablegen = prompt.index("abschluss_paket.py ablegen {S} --stand abnahme")
    assert "entscheidungen_{S}.md" in prompt[ablegen : prompt.index("Danach Aufbau-Prüfung")]


def test_abschluss_text_enthaelt_alle_entscheidungen_mit_zahl_475(repo: Path, tmp_path: Path) -> None:
    """Die Mail zeigt jede Zeile der Liste samt Zahl — nicht nur den Link (#475)."""
    echt = (
        "Push nach Rebase abgelehnt",
        "force-with-lease auf eigenen ticket/14-unbekannte-adressen",
    )
    for i in range(4):
        bau_log.schreibe(repo, 2, "entscheidung", frage=f"Frage {i}", wahl=f"Wahl {i}", grund="g")
    bau_log.schreibe(repo, 3, "entscheidung", frage=echt[0], wahl=echt[1], grund="Klasse 1 · Rebase")
    assert _unter("ablegen", repo, tmp_path, *_LINKS, "--spec-fertig", _FERTIG).returncode == 0
    zeilen = [z for z in _datei(repo).read_text(encoding="utf-8").splitlines() if z.startswith("| #")]
    assert len(zeilen) == 7, zeilen
    probe = _unter("nachsehen", repo, tmp_path, "--jetzt", "2026-11-24T23:00:00+01:00", "--dry-run")
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "Entscheidungen (7)" in probe.stdout
    assert all(z in probe.stdout for z in zeilen)
    assert f"{echt[0]} → {echt[1]}" in probe.stdout


def test_ohne_manifest_keine_fremden_bau_logs_und_unvollstaendig_markiert_445(
    tmp_path: Path,
) -> None:
    """Ohne Manifest landen keine fremden Bau-Logs in entscheidungen_<S>.md; Liste als unvollständig markiert (#445)."""
    r = tmp_path / "projekt_ohne_manifest"
    r.mkdir()
    # Fremde Tickets ohne Spec-Bezug bzw. mit fremder Spec dürfen nicht auftauchen.
    bau_log.schreibe(r, 999, "entscheidung", frage="Fremdes Thema", wahl="Fremde Wahl", grund="andere Spec")
    bau_log.schreibe(
        r,
        888,
        "entscheidung",
        spec="888",
        frage="Anderes Spec-Thema",
        wahl="Andere Wahl",
        grund="Spec 888",
    )
    fid = briefkasten.einwerfen("999", "Fremde Briefkasten-Frage?", "nein", 3, [], hauptbaum=r)
    briefkasten.antworten("999", fid, "nein", ["Quelle"], "fremd", hauptbaum=r)

    # Ticket mit ausdrücklichem Spec-Bezug im Bau-Log darf enthalten sein.
    bau_log.schreibe(
        r,
        422,
        "entscheidung",
        spec=str(SPEC),
        frage="Eigenes Spec-Thema",
        wahl="Eigene Wahl",
        grund="gehört zu 421",
    )

    lauf = _unter(
        "ablegen",
        r,
        tmp_path,
        *_LINKS,
        "--stand",
        "abnahme",
        "--ohne-rundschau",
        "--spec-fertig",
        _FERTIG,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    inhalt = _datei(r).read_text(encoding="utf-8")
    zeilen = [z for z in inhalt.splitlines() if z.startswith("| #")]
    assert not any("#999" in z for z in zeilen), f"Fremdes Ticket #999 in Liste: {zeilen}"
    assert not any("#888" in z for z in zeilen), f"Fremdes Ticket #888 in Liste: {zeilen}"
    assert any(z.startswith("| #422 | Eigenes Spec-Thema → Eigene Wahl | gehört zu 421 |") for z in zeilen), zeilen
    assert "unvollständig" in inhalt.lower()
    assert f"spec-{SPEC}.json" in inhalt


def test_ohne_manifest_und_ohne_spec_tag_leere_unvollstaendige_liste_445(
    tmp_path: Path,
) -> None:
    """Ohne Manifest und ohne Spec-Zuordnung im Bau-Log bleibt die Liste frei von Fremd-Tickets (#445)."""
    r = tmp_path / "projekt_nur_fremd"
    r.mkdir()
    bau_log.schreibe(r, 777, "entscheidung", frage="Nur fremd", wahl="X", grund="gehört nicht zu 421")
    lauf = _unter(
        "ablegen",
        r,
        tmp_path,
        *_LINKS,
        "--stand",
        "abnahme",
        "--ohne-rundschau",
        "--spec-fertig",
        _FERTIG,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    inhalt = _datei(r).read_text(encoding="utf-8")
    assert [z for z in inhalt.splitlines() if z.startswith("| #")] == []
    assert "#777" not in inhalt
    assert "unvollständig" in inhalt.lower()
