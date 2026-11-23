"""#329 P1: ``review/`` ist die versionierte Quelle des Review-Werkzeugs.

Geprüft: (a) jede Datei, die Hook (``review-hook/_hookbasis.py``) und Skill
``review-dirigent`` aufrufen, liegt unter ``review/``; (b) keine Laufzeit-Daten
(``sitzungen/``, ``*.jsonl``, ``*.log``, ``__pycache__``) im Repo; (c) das Repo ist
öffentlich — kein Projektname, kein privater Pfad, kein Personenname, kein Geheimnis.
Die Muster für (c) stammen aus den bestehenden Namens-Prüfungen (#323, L1).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from test_feste_namen_l1 import MUSTER
from test_rolle_statt_name_323 import NAME

SKILL = Path(__file__).resolve().parents[1]
REVIEW = SKILL / "review"

SKRIPTE = (
    "_gemeinsam.py",
    "review_route.py",
    "review_beleg.py",
    "review_bericht.py",
    "rot_probe.py",
)
DOKU = ("VERTRAG.md", "config.json", ".gitignore")
AUFTRAEGE = (
    "basis.md",
    "datenbank.md",
    "fehlerpfad.md",
    "infra.md",
    "kosten.md",
    "n8n.md",
    "oberflaeche.md",
    "python.md",
    "react.md",
    "richter.md",
    "shell.md",
    "sicherheit.md",
    "spec.md",
    "tests.md",
    "typescript.md",
)
TEST_HILFEN = ("tests/conftest.py", "tests/review_hilfen.py")

LAUFZEIT = re.compile(r"(^|/)(sitzungen/|__pycache__/)|\.(jsonl|log|pyc)$")
#: Geheimnis-Formen (echte Längen; Attrappen wie ``sk-abc123`` bleiben erlaubt).
GEHEIMNIS = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY"
)
OEFFENTLICH = {**MUSTER, "personenname": NAME, "geheimnis": GEHEIMNIS}


def _review_dateien() -> list[str]:
    """Dateien unter ``review/``, die ins Repo gehören (versioniert oder nicht ignoriert)."""
    raus = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "review"],
        cwd=SKILL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return [z for z in raus.stdout.splitlines() if (SKILL / z).is_file()]


def test_quelle_vollstaendig() -> None:
    soll = [
        *SKRIPTE,
        *DOKU,
        *(f"auftraege/{a}" for a in AUFTRAEGE),
        *TEST_HILFEN,
    ]
    fehlt = [d for d in soll if not (REVIEW / d).is_file()]
    assert fehlt == [], f"fehlt unter review/: {fehlt}"
    assert len(list((REVIEW / "tests").glob("test_*.py"))) >= 17


def test_keine_laufzeit_daten_im_repo() -> None:
    funde = [d for d in _review_dateien() if LAUFZEIT.search(d)]
    assert funde == [], f"Laufzeit-Daten gehören nicht ins Repo: {funde}"


def test_oeffentlich_tauglich() -> None:
    funde = [
        f"{datei}:{nr} ({art})"
        for datei in _review_dateien()
        for nr, zeile in enumerate(
            (SKILL / datei).read_text(encoding="utf-8").splitlines(), 1
        )
        for art, muster in OEFFENTLICH.items()
        if muster.search(zeile)
    ]
    assert funde == [], f"nicht öffentlich-tauglich: {funde}"


def test_review_tests_ohne_fremdes_conftest() -> None:
    """Werkzeug-Tests holen Helfer aus ``review_hilfen``, nie per ``from conftest``."""
    funde = [
        f"{p.name}:{nr}"
        for p in sorted((REVIEW / "tests").glob("*.py"))
        for nr, zeile in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.match(r"\s*(from conftest import|import conftest)", zeile)
    ]
    assert funde == [], f"from conftest im Werkzeug-Test: {funde}"
