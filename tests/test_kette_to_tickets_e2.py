"""L1 Etappe 2 E2-3 (#87): Prüf-Test für den Ketten-Text unter ``kette/``.

Bricht den Build bei jeder Text-Änderung an ``kette/**/*.md`` und ``kette/skills/*/agents/openai.yaml``, die
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
#: Anführungszeichen um Pfad oder Befehl und in jeder Groß-/Kleinschreibung (#144); Variablen wie
#: ``"$X"`` vor dem Befehl werden übersprungen (#156). Zeilenumbrüche mit ``\`` löst ``_befehle`` auf.
BEFEHL = re.compile(r"to_spawn\.py[\"']?(?:\s+[\"']?\$[^\s\"'`]*[\"']?)*\s+[\"']?([A-Za-z][A-Za-z0-9-]*)([^\n`]*)")
UMBRUCH = re.compile(r"\\\r?\n[ \t]*")
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
    "to_spawn.py regularien <S>",
    "pfade.manifeste",
    ".to-spawn/kette.md",
    "Projekt-Vorrang",
    "Repo-Zuordnung",
)

#: #579 (E15, E29): Pflicht-Bausteine für Schritt „Repo-Zuordnung“ in to-spec und to-tickets.
PFLICHT_REPO_ZUORDNUNG = (
    "Repo-Zuordnung",
    "Skill-Repo",
    "App-Repo",
    "beide",
    "Querverweis",
    "eigenständig",
    "begleitet",
)


def pruefe_repo_zuordnung_und_projekt_vorrang(text: str, name: str) -> None:
    """#579 (E15, E29): Prüft Pflicht-Zeile „Projekt-Vorrang“ und Schritt „Repo-Zuordnung“."""
    assert "Projekt-Vorrang" in text, f"{name}: Pflicht-Zeile 'Projekt-Vorrang' fehlt"
    assert "statt des restlichen Texts" not in text, f"{name}: alter Projekt-Vorrang (ersetzt) noch drin"
    assert text.index("Projekt-Vorrang") < text.index("## Process"), (
        f"{name}: 'Projekt-Vorrang' muss vor '## Process' stehen"
    )
    prozess = text[text.index("## Process") :]
    for baustein in PFLICHT_REPO_ZUORDNUNG:
        assert baustein in prozess, f"{name}: Baustein {baustein!r} fehlt im Schritt 'Repo-Zuordnung'"


def _texte() -> dict[Path, str]:
    dateien = [*KETTE.rglob("*.md"), *KETTE.glob("skills/*/agents/openai.yaml")]
    return {p: p.read_text(encoding="utf-8") for p in sorted(dateien)}


def _befehle(text: str) -> list[re.Match[str]]:
    """Alle ``to_spawn.py``-Aufrufe in ``text``; ein mit Backslash umbrochener Aufruf zählt als eine Zeile."""
    return list(BEFEHL.finditer(UMBRUCH.sub(" ", text)))


def _verbotene(text: str) -> list[tuple[int, str]]:
    """(Zeilennummer, Wort) je fester App-Repo-Name in ``text`` — ohne Rücksicht auf Groß-/Kleinschreibung und Backslash."""
    return [
        (nr, wort)
        for nr, zeile in enumerate(text.splitlines(), 1)
        for wort in VERBOTEN
        if wort.lower() in zeile.lower().replace("\\", "/")
    ]


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
        for m in _befehle(text)
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
        for nr, wort in _verbotene(text)
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
        # #156: Umbruch mit ``\``, Variable vor dem Befehl, ``python3 …``, ``${SKILL}``.
        ("python to_spawn.py \\\nerfunden --x", "erfunden"),
        ('python3 "${SKILL}/to_spawn.py" \\\n  "$X" \\\n  erfunden', "erfunden"),
        ('`to_spawn.py "$X" erfunden`', "erfunden"),
        ("`python3 ${SKILL}/to_spawn.py $SPEC erfunden`", "erfunden"),
        ('`python3 "${SKILL}/to_spawn.py" erfunden`', "erfunden"),
    ],
)
def test_pruefung_erkennt_jede_schreibweise(zeile: str, befehl: str) -> None:
    """#144/#156: Anführungszeichen, Großschreibung, Umbruch und Variablen entgehen dem Muster nicht."""
    treffer = _befehle(zeile)
    assert treffer and treffer[0].group(1) == befehl, f"Muster übersieht {zeile!r}"
    assert _hilfe(befehl)[0] != 0


@pytest.mark.parametrize(
    "zeile",
    [
        "siehe Shavy72/duoplus",
        "siehe shavy72/duoplus",
        "SHAVY72/x",
        "docs\\agents\\manifests\\spec-1.json",
        "Docs/Agents/Manifests",
    ],
)
def test_pruefung_erkennt_festen_namen_jeder_schreibweise(zeile: str) -> None:
    """#156: Groß-/Kleinschreibung und Backslash entgehen der Namens-Sperre nicht."""
    assert _verbotene(zeile), f"Namens-Sperre übersieht {zeile!r}"


def test_agents_yaml_wird_geprueft() -> None:
    """#156: ``kette/skills/*/agents/openai.yaml`` gehört zum geprüften Ketten-Text."""
    yamls = sorted(KETTE.glob("skills/*/agents/openai.yaml"))
    assert yamls, "Selbstprobe: keine agents/openai.yaml unter kette/skills/"
    assert set(yamls) <= set(_texte()), "agents/openai.yaml fehlt in der Prüfung"


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


@pytest.mark.parametrize("skill", ("to-spec", "to-tickets"))
def test_kette_skill_hat_repo_zuordnung_und_projekt_vorrang_579(skill: str) -> None:
    """#579 (E15, E29): Beide Skill-Texte führen „Projekt-Vorrang“ und „Repo-Zuordnung“ mit drei Werten."""
    pfad = KETTE / "skills" / skill / "SKILL.md"
    assert pfad.is_file(), f"{pfad} fehlt"
    pruefe_repo_zuordnung_und_projekt_vorrang(pfad.read_text(encoding="utf-8"), skill)
