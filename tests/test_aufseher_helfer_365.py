"""Aufseher-Helfer: Obsidian + alte Session-Logs als Suchorte, Pflicht-Beleg (#365, L9 A55/A56, N21).

Prüft ohne gh und ohne echte Claude-Session (tmp_path):
- gemeinsamer Tresor-Ort (:func:`config.notiz_tresor`) für ``uebergabe_sichern`` und ``befugnis``,
- Aufseher-Text nennt Notiz-Tresor (``$OBSIDIAN_VAULT`` / ``~/obsidian``), alte Session-Logs,
  Überspringen fehlender Orte im Beleg, frischen Helfer je offener Frage und Pflicht-``--beleg``,
- ``briefkasten.antworten`` und CLI ``antworten`` verweigern fehlende/leere Fundstellen klar,
- Aufseher-Takt: 0 offene Fragen → kein Helfer-Auftrag im Text / kein Claude-Start;
  je offener Klasse-3-Frage ein eigener Helfer-Auftrag; Klasse 2 bleibt beim Nutzer.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus test_waechter_takt_316.
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tests"))

from test_waechter_takt_316 import CLAUDE_AUFZ, _aufrufe, _takt, welt  # noqa: F401

from to_spawn import befugnis, briefkasten, config, uebergabe_sichern, waechter_takt


def _cli(repo: Path, *argv: str, **zusatz: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env["TO_SPAWN_REPO"] = str(repo)
    env.update(zusatz)
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), *argv],
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


# --- 1: Gemeinsamer Tresor-Ort & Aufseher-Text ----------------------------------------


def test_notiz_tresor_eine_quelle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``config.notiz_tresor`` ist die eine Quelle für ``uebergabe_sichern`` und ``befugnis``."""
    tresor = tmp_path / "mein-vault"
    tresor.mkdir()
    monkeypatch.setenv("OBSIDIAN_VAULT", str(tresor))
    monkeypatch.delenv(uebergabe_sichern.UMGEBUNG_ZIEL, raising=False)
    assert config.notiz_tresor() == tresor
    assert uebergabe_sichern.ziel_ordner().is_relative_to(tresor)
    assert tresor.as_posix() in befugnis.aufseher_text("python X/to_spawn.py")

    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert config.notiz_tresor() == Path("~/obsidian").expanduser()
    # Kein zweites ``OBSIDIAN_VAULT``-Lookup in uebergabe_sichern.py.
    assert 'os.environ.get("OBSIDIAN_VAULT")' not in (SKILL / "to_spawn" / "uebergabe_sichern.py").read_text(
        encoding="utf-8"
    )


def test_aufseher_text_nennt_obsidian_session_logs_und_pflicht_beleg() -> None:
    text = befugnis.aufseher_text("python X/to_spawn.py")
    assert "Notiz-Tresor" in text and "Obsidian" in text
    assert "$OBSIDIAN_VAULT" in text and "~/obsidian" in text
    assert "Session-Logs" in text and "~/.claude/projects/" in text
    assert "überspringen" in text and "im Beleg" in text
    assert "je offener Frage" in text and "frischer Helfer" in text
    assert "--beleg" in text and "Pflicht" in text
    assert "{" not in text and "}" not in text
    assert text.count(befugnis.KLASSE_2_WEITERGABE) == 3


# --- 2: Pflicht-Fundstelle (--beleg) in briefkasten antworten -------------------------


@pytest.mark.parametrize("belege", [[], [""], ["   "], ["\t\n"]])
def test_antworten_ohne_beleg_verweigert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, belege: list[str]
) -> None:
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid = briefkasten.einwerfen("7", "Ordner umbenennen?", "neu", 3, [], hauptbaum=tmp_path)
    with pytest.raises(ValueError, match="--beleg|Beleg|Fundstelle"):
        briefkasten.antworten("7", fid, "neu", belege, "passt so", hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.WARTET


@pytest.mark.parametrize("extra", [[], ["--beleg", ""], ["--beleg", "   "]])
def test_cli_antworten_ohne_beleg_abgelehnt(tmp_path: Path, extra: list[str]) -> None:
    eingeworfen = _cli(tmp_path, "frage", "7", "--frage", "Ordner?", "--vorschlag", "neu")
    assert eingeworfen.returncode == 0, eingeworfen.stderr
    fid = eingeworfen.stdout.strip().rsplit(" ", 1)[-1]
    lauf = _cli(tmp_path, "antworten", "7", "--id", fid, "--wahl", "neu", "--grund", "passt", *extra)
    assert lauf.returncode != 0
    assert "beleg" in (lauf.stderr + lauf.stdout).lower()
    assert _cli(tmp_path, "antwort", "7", "--id", fid).returncode == 3


def test_cli_antworten_mit_beleg_schreibt_fundstelle(tmp_path: Path) -> None:
    eingeworfen = _cli(tmp_path, "frage", "7", "--frage", "Ordner?", "--vorschlag", "neu")
    fid = eingeworfen.stdout.strip().rsplit(" ", 1)[-1]
    lauf = _cli(
        tmp_path,
        "antworten",
        "7",
        "--id",
        fid,
        "--wahl",
        "neu",
        "--grund",
        "Haltung aus Notiz-Tresor",
        "--beleg",
        "~/obsidian/projects/bau-system/regel.md:12",
    )
    assert lauf.returncode == 0, lauf.stderr
    antwort = briefkasten.lesen("7", fid, hauptbaum=tmp_path)
    assert isinstance(antwort, briefkasten.Antwort)
    assert antwort.belege == ["~/obsidian/projects/bau-system/regel.md:12"]


def test_nutzer_antwort_braucht_weiterhin_keinen_beleg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    briefkasten.nutzer_antworten("7", fid, "nein", [], "später", hauptbaum=tmp_path)
    antwort = briefkasten.lesen("7", fid, hauptbaum=tmp_path)
    assert isinstance(antwort, briefkasten.Antwort) and antwort.von == briefkasten.NUTZER_VON


# --- 3: Aufseher-Takt: 0 offene Fragen → kein Helfer-Auftrag --------------------------


def test_takt_ohne_offene_fragen_kein_helfer_auftrag(
    welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    # 0 offene Fragen im Briefkasten → kein Briefkasten-Eintrag, kein Helfer-Auftrag in der Liste.
    assert waechter_takt.briefkasten_eintraege(welt["repo"], [297, 298], set()) == []
    prompt = waechter_takt.baue_prompt(293, welt["repo"], ["#297 neu geschlossen — Belegseite prüfen"], None)
    aufgaben = prompt.split("Zu entscheiden:\n", 1)[1].split("\n\nNotizzettel", 1)[0]
    assert "Briefkasten-Frage" not in aufgaben
    assert "Helfer" not in aufgaben
    # Echter Takt-Lauf ohne offene Fragen und ohne andere Ereignisse startet gar kein Claude.
    erg = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert erg.returncode == 0, erg.stdout + erg.stderr
    assert _aufrufe(welt) == []


def test_takt_ein_frischer_helfer_je_offener_klasse_3_frage_nie_fuer_klasse_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    q1 = briefkasten.einwerfen("297", "Ordner A umbenennen?", "ja", 3, [], hauptbaum=tmp_path)
    q2 = briefkasten.einwerfen("298", "Datei B verschieben?", "ja", 3, [], hauptbaum=tmp_path)
    k2 = briefkasten.einwerfen("299", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    eintraege = waechter_takt.briefkasten_eintraege(tmp_path, [297, 298, 299], set())
    texte = {k: t for t, _feld, k in eintraege}
    assert "frischer Helfer" in texte[f"297:{q1}"] and "--beleg" in texte[f"297:{q1}"]
    assert "frischer Helfer" in texte[f"298:{q2}"] and "--beleg" in texte[f"298:{q2}"]
    assert "frischer Helfer" not in texte[f"299:{k2}"]
    assert befugnis.KLASSE_2_WEITERGABE in texte[f"299:{k2}"]
