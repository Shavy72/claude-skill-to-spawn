"""Tests #563: „SPEC FERTIG“ schaltet den Staging-Schalter der Spec AN, nie Live.

Echt laufen: Git-Repo mit Bare-Origin, die CLI ``skripte/capo.py``, die echte
Zustandsdatei und der echte Lesepfad des Spec-Textes. Gestellt sind nur die
externen Dienste: ``gh`` (Ersatz aus #213) und das Schalter-Skript des App-Repos
(``scripts/schalter.py`` im Wegwerf-Repo schreibt seine Argumente in eine Datei,
statt den Staging-Container anzusprechen).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus test_waechter_213 und wird als Parameter genannt.
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _beleg,
    _capo,
    _commit,
    _gh_setzen,
    _iso,
    _text,
    welt,
)

SKILL = Path(__file__).resolve().parents[1]

SCHALTER_FAKE = (
    "import json, os, sys\n"
    "open(os.environ['SCHALTER_FAKE_PROTOKOLL'], 'a', encoding='utf-8')"
    ".write(json.dumps(sys.argv[1:]) + '\\n')\n"
    "print('Schalter umgeschaltet (Fake)')\n"
    "sys.exit(int(os.environ.get('SCHALTER_FAKE_EXIT', '0')))\n"
)

SPEC_MIT_SCHALTER = (
    "## Problem\n\nirgendwas\n\n"
    "## Hauptschalter\n\n"
    "- **Name des Hauptschalters:** `demo_funktion.an` — schaltet die Demo-Funktion.\n"
    "- **Unter-Schalter (optional):** keine\n\n"
    "## User Stories\n\n1. `andere.an` gehört nicht dazu\n"
)

SPEC_OHNE_SCHALTER = (
    "## Hauptschalter\n\n"
    "- **Kein Schalter nötig, weil:** nur Fehlerbehebungen an freigegebenen Funktionen.\n"
)


@pytest.fixture()
def protokoll(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> Path:
    pfad = welt["tmp"] / "schalter_aufrufe.jsonl"
    monkeypatch.setenv("SCHALTER_FAKE_PROTOKOLL", str(pfad))
    monkeypatch.delenv("SCHALTER_FAKE_EXIT", raising=False)
    return pfad


def _aufrufe(pfad: Path) -> list[list[str]]:
    if not pfad.is_file():
        return []
    return [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines() if z]


def _spec_fertig(
    welt: dict[str, Path], spec_text: str | None, mit_skript: bool = True
) -> None:
    dateien = {**_beleg("901"), **_beleg("902")}
    if mit_skript:
        dateien["scripts/schalter.py"] = SCHALTER_FAKE
    _commit(welt["repo"], "feat: A (#901)", dateien)
    _commit(welt["repo"], "feat: B (#902)", {})
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())
    if spec_text is not None:
        _gh_setzen(welt, SPEC, state="open", body=spec_text)


def test_spec_fertig_schaltet_staging_an_und_nur_einmal(
    welt: dict[str, Path], protokoll: Path
) -> None:
    _spec_fertig(welt, SPEC_MIT_SCHALTER)
    erster = _capo(welt)
    zweiter = _capo(welt)
    assert "SPEC FERTIG" in erster.stdout, _text(erster)
    assert "Staging-Schalter demo_funktion.an AN" in erster.stdout, _text(erster)
    aufrufe = _aufrufe(protokoll)
    assert len(aufrufe) == 1, (aufrufe, _text(zweiter))
    argv = aufrufe[0]
    assert argv[:2] == ["an", "demo_funktion.an"]
    assert argv[argv.index("--ziel") + 1] == "staging"
    assert f"#{SPEC}" in argv[argv.index("--grund") + 1]
    assert "Staging-Schalter" not in zweiter.stdout


def test_probe_zeigt_befehl_und_schaltet_nichts(
    welt: dict[str, Path], protokoll: Path
) -> None:
    _spec_fertig(welt, SPEC_MIT_SCHALTER)
    ergebnis = _capo(welt, "--dry-run")
    assert "[Probe] Staging-Schalter demo_funktion.an AN" in ergebnis.stdout, _text(
        ergebnis
    )
    assert "--ziel staging" in ergebnis.stdout
    assert _aufrufe(protokoll) == []


def test_spec_ohne_schalter_tut_nichts_und_meldet_es(
    welt: dict[str, Path], protokoll: Path
) -> None:
    _spec_fertig(welt, SPEC_OHNE_SCHALTER)
    ergebnis = _capo(welt)
    _capo(welt)
    assert "kein Hauptschalter" in _text(ergebnis), _text(ergebnis)
    assert _aufrufe(protokoll) == []
    assert ergebnis.returncode == 0, _text(ergebnis)


def test_ohne_schalter_skript_ist_fehler_und_schaltet_nicht(
    welt: dict[str, Path], protokoll: Path
) -> None:
    """Spec nennt einen Schalter, Skript fehlt (Stand veraltet): FEHLER, nicht still abhaken."""
    _spec_fertig(welt, SPEC_MIT_SCHALTER, mit_skript=False)
    ergebnis = _capo(welt)
    assert "FEHLER: Staging-Schalter demo_funktion.an" in ergebnis.stdout, _text(
        ergebnis
    )
    assert "scripts/schalter.py" in ergebnis.stdout
    assert _aufrufe(protokoll) == []
    assert ergebnis.returncode == 1, _text(ergebnis)


@pytest.mark.parametrize(
    "zeile",
    [
        "- **Name des Hauptschalters:** z. B. `<spec-kurzname>.an` nach Vorbild `story_macher.an`",
        "- **Name des Hauptschalters:** demo_funktion.an (ohne Backticks)",
    ],
)
def test_unlesbarer_name_ist_fehler_und_schaltet_keinen_fremden_schalter(
    welt: dict[str, Path], protokoll: Path, zeile: str
) -> None:
    """Vorlagen-Platzhalter oder Name ohne Backticks: nie raten, nie ``story_macher.an`` schalten."""
    _spec_fertig(welt, f"## Hauptschalter\n\n{zeile}\n")
    ergebnis = _capo(welt)
    zweiter = _capo(welt)
    assert "FEHLER: Staging-Schalter — Name des Hauptschalters" in ergebnis.stdout, (
        _text(ergebnis)
    )
    assert (
        "FEHLER: Staging-Schalter" in zweiter.stdout
    )  # nicht abgehakt, nächster Tick prüft neu
    assert _aufrufe(protokoll) == []


def test_fehlschlag_ist_fehler_und_naechster_tick_versucht_es_wieder(
    welt: dict[str, Path], protokoll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spec_fertig(welt, SPEC_MIT_SCHALTER)
    monkeypatch.setenv("SCHALTER_FAKE_EXIT", "1")
    erster = _capo(welt)
    assert "FEHLER: Staging-Schalter demo_funktion.an" in erster.stdout, _text(erster)
    monkeypatch.setenv("SCHALTER_FAKE_EXIT", "0")
    zweiter = _capo(welt)
    _capo(welt)
    assert "Staging-Schalter demo_funktion.an AN" in zweiter.stdout, _text(zweiter)
    assert len(_aufrufe(protokoll)) == 2


def test_kein_pfad_im_skill_schaltet_live() -> None:
    """Live schaltet nur David: kein Skill-Code ruft das Schalter-Skript mit Ziel live auf."""
    from to_spawn import staging_schalter

    assert staging_schalter.ZIEL == "staging"
    verdaechtig = re.compile(
        r"schalter.{0,200}?--ziel\W{0,5}live|ZIEL\s*=\s*['\"]live",
        re.DOTALL | re.IGNORECASE,
    )
    funde = []
    for pfad in SKILL.rglob("*"):
        if "tests" in pfad.parts or pfad.suffix not in {
            ".py",
            ".sh",
            ".ps1",
            ".md",
            ".json",
        }:
            continue
        if verdaechtig.search(pfad.read_text(encoding="utf-8", errors="replace")):
            funde.append(str(pfad.relative_to(SKILL)))
    assert funde == []
