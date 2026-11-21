"""L1 Etappe 2 E2-3 (#87): Prüf-Test für den Ketten-Text unter ``kette/``.

Bricht den Build bei jeder Text-Änderung an ``kette/**/*.md``, die
1. einen ``to_spawn.py <befehl>`` (samt ``--schalter``) nennt, den der Parser nicht kennt,
2. feste Namen des ersten App-Repos enthält (``Shavy72/``, ``docs/agents/manifests``),
3. einen Pflicht-Baustein von ``kette/skills/to-tickets/SKILL.md`` verliert.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
KETTE = SKILL / "kette"
TO_TICKETS = KETTE / "skills" / "to-tickets" / "SKILL.md"

#: ``to_spawn.py <befehl> <rest der Zeile>`` — Befehl = erstes Wort nach dem Skript, auch mit
#: Anführungszeichen um Pfad oder Befehl und in jeder Groß-/Kleinschreibung (#144).
BEFEHL = re.compile(r"to_spawn\.py[\"']?\s+[\"']?([A-Za-z][A-Za-z0-9-]*)([^\n`]*)")
SCHALTER = re.compile(r"(?<![\w-])(--[A-Za-z][A-Za-z0-9-]*)")
VERBOTEN = ("Shavy72/", "docs/agents/manifests")
PFLICHT_TO_TICKETS = (
    "<!-- pflicht:beschluss-protokoll -->",
    "(E<n>)",
    "## Beschlüsse",
    "to_spawn.py lernstoff",
    "staffel.grenze_k",
    "## Kontext-Paket",
    "## Werkzeuge",
    "checkpoint:human",
    "to_spawn.py anhaengen",
    "to_spawn.py pruefen <S>",
    "pfade.manifeste",
    ".to-spawn/kette.md",
)


def _texte() -> dict[Path, str]:
    return {p: p.read_text(encoding="utf-8") for p in sorted(KETTE.rglob("*.md"))}


@cache
def _hilfe(befehl: str) -> tuple[int, str]:
    lauf = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), befehl, "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    return lauf.returncode, lauf.stdout


def _aufrufe() -> list[tuple[str, str, str]]:
    return [
        (p.relative_to(SKILL).as_posix(), m.group(1), m.group(2))
        for p, text in _texte().items()
        for m in BEFEHL.finditer(text)
    ]


def test_to_tickets_skill_liegt_in_der_kette() -> None:
    assert TO_TICKETS.is_file(), "kette/skills/to-tickets/SKILL.md fehlt"


def test_jeder_to_spawn_befehl_existiert_im_parser() -> None:
    aufrufe = _aufrufe()
    assert any(b == "anhaengen" for _, b, _ in aufrufe), "kein to_spawn.py-Aufruf in kette/"
    fehler = []
    for datei, befehl, rest in aufrufe:
        code, hilfe = _hilfe(befehl)
        if code != 0:
            fehler.append(f"{datei}: to_spawn.py {befehl} — Befehl unbekannt")
            continue
        fehler += [
            f"{datei}: to_spawn.py {befehl} {s} — Schalter unbekannt" for s in SCHALTER.findall(rest) if s not in hilfe
        ]
    assert not fehler, "\n".join(fehler)


def test_kein_fester_repo_name_im_ketten_text() -> None:
    fehler = [
        f"{p.relative_to(SKILL).as_posix()}:{nr}: {wort!r}"
        for p, text in _texte().items()
        for nr, zeile in enumerate(text.splitlines(), 1)
        for wort in VERBOTEN
        if wort in zeile
    ]
    assert not fehler, "\n".join(fehler)


@pytest.mark.parametrize("baustein", PFLICHT_TO_TICKETS)
def test_to_tickets_pflicht_baustein(baustein: str) -> None:
    text = TO_TICKETS.read_text(encoding="utf-8") if TO_TICKETS.is_file() else ""
    assert baustein in text, f"to-tickets: Pflicht-Baustein {baustein!r} fehlt"


def test_to_tickets_kanten_nur_ueber_anhaengen() -> None:
    """Keine handgesetzten Kanten mehr: ``anhaengen`` ist die eine Tür (E2-2)."""
    text = TO_TICKETS.read_text(encoding="utf-8") if TO_TICKETS.is_file() else ""
    assert "dependencies/blocked_by" not in text
    assert "statt des restlichen Texts" not in text, "alter Projekt-Vorrang (ersetzt) noch drin"
    assert text.index("<!-- pflicht:beschluss-protokoll -->") < text.index("## Process")


def test_pruefung_wird_rot_bei_unbekanntem_befehl() -> None:
    """Selbstprobe: ein ausgedachter Befehl und Schalter fallen auf."""
    m = BEFEHL.search("`python to_spawn.py anhaengen 1 2 --gibtsnicht`")
    assert m and m.group(1) == "anhaengen"
    assert "--gibtsnicht" not in _hilfe("anhaengen")[1]
    assert _hilfe("gibtsnicht")[0] != 0


@pytest.mark.parametrize(
    ("zeile", "befehl"),
    [
        ('`python "$SKILL/to_spawn.py" erfunden`', "erfunden"),
        ("`python '$SKILL/to_spawn.py' erfunden`", "erfunden"),
        ("`python to_spawn.py Erfunden`", "Erfunden"),
        ('`python "C:\\skills\\to_spawn.py" "Erfunden"`', "Erfunden"),
    ],
)
def test_pruefung_erkennt_jede_schreibweise(zeile: str, befehl: str) -> None:
    """#144: Anführungszeichen um Pfad oder Befehl und Großschreibung entgehen dem Muster nicht."""
    m = BEFEHL.search(zeile)
    assert m and m.group(1) == befehl, f"Muster übersieht {zeile!r}"
    assert _hilfe(befehl)[0] != 0


def test_jeder_schalter_im_to_tickets_text_existiert() -> None:
    """#144: auch Schalter außerhalb einer Aufruf-Zeile (``Erst mit `--trocken` …``) gegen den Parser prüfen."""
    text = TO_TICKETS.read_text(encoding="utf-8") if TO_TICKETS.is_file() else ""
    hilfen = "".join(_hilfe(b)[1] for b in {m.group(1) for m in BEFEHL.finditer(text)})
    unbekannt = sorted({s for s in SCHALTER.findall(text) if s not in hilfen})
    assert "--trocken" in SCHALTER.findall(text), "Selbstprobe: --trocken nicht erfasst"
    assert not unbekannt, f"to-tickets nennt unbekannte Schalter: {unbekannt}"


def test_parent_satz_widerspricht_anhaengen_nicht() -> None:
    """#144: ``anhaengen`` macht das Ticket zum Unter-Issue — ein pauschales „parent nie ändern“ widerspricht dem."""
    text = TO_TICKETS.read_text(encoding="utf-8") if TO_TICKETS.is_file() else ""
    assert "modify any parent issue" not in text
    assert "Unter-Issue" in text
