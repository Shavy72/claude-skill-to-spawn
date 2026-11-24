"""Runde 4: Profil schlank bündelt Fachprüfer, voll bleibt beim alten Verhalten."""

from __future__ import annotations

import sys
from pathlib import Path

REVIEW = Path(__file__).resolve().parent.parent
if str(REVIEW) not in sys.path:
    sys.path.insert(0, str(REVIEW))

from _gemeinsam import _pruefer_liste  # noqa: E402


def _signale(*ids_dateien: tuple[str, list[str]]) -> list[dict]:
    return [{"id": sid, "klasse": "normal", "dateien": dateien} for sid, dateien in ids_dateien]


def test_schlank_gross_mit_python_sicherheit_tests_datenbank_genau_drei_pruefer() -> None:
    dateien = ["app/a.py", "app/auth.py", "app/models.py", "app/tests/test_a.py"]
    signale = _signale(
        ("python", ["app/a.py"]),
        ("sicherheit", ["app/auth.py"]),
        ("tests", ["app/tests/test_a.py"]),
        ("datenbank", ["app/models.py"]),
    )
    cfg = {"profil": "schlank", "modelle": {"pruefer": "sonnet"}}
    ergebnis = _pruefer_liste("gross", signale, dateien, cfg)
    namen = {p["name"] for p in ergebnis}
    assert len(ergebnis) == 3
    assert namen == {"general-purpose", "security-reviewer", "pr-test-analyzer"}
    spec = next(p for p in ergebnis if p["name"] == "general-purpose")
    assert "spec" in spec["auftraege"]
    assert "python" in spec["auftraege"]
    assert "datenbank" in spec["auftraege"]


def test_schlank_ohne_sicherheit_signal_kein_security_reviewer() -> None:
    dateien = ["app/a.py", "app/tests/test_a.py"]
    signale = _signale(("python", ["app/a.py"]), ("tests", ["app/tests/test_a.py"]))
    cfg = {"profil": "schlank", "modelle": {"pruefer": "sonnet"}}
    ergebnis = _pruefer_liste("mittel", signale, dateien, cfg)
    namen = {p["name"] for p in ergebnis}
    assert "security-reviewer" not in namen


def test_schlank_code_ohne_test_erzwingt_pr_test_analyzer() -> None:
    dateien = ["app/a.py"]
    signale = _signale(("python", ["app/a.py"]))
    cfg = {"profil": "schlank", "modelle": {"pruefer": "sonnet"}}
    ergebnis = _pruefer_liste("mittel", signale, dateien, cfg)
    namen = {p["name"] for p in ergebnis}
    assert "pr-test-analyzer" in namen


def test_schlank_klein_ist_ein_pruefer_mit_fachauftraegen() -> None:
    dateien = ["app/a.py"]
    signale = _signale(("python", ["app/a.py"]))
    cfg = {"profil": "schlank", "modelle": {"pruefer": "sonnet"}}
    ergebnis = _pruefer_liste("klein", signale, dateien, cfg)
    assert len(ergebnis) == 1
    assert ergebnis[0]["name"] == "general-purpose"
    assert "python" in ergebnis[0]["auftraege"]


def test_profil_voll_behaelt_alte_liste() -> None:
    dateien = ["app/a.py", "app/auth.py"]
    signale = _signale(("python", ["app/a.py"]), ("sicherheit", ["app/auth.py"]))
    cfg = {"profil": "voll", "modelle": {"pruefer": "sonnet"}}
    ergebnis = _pruefer_liste("mittel", signale, dateien, cfg)
    namen = {p["name"] for p in ergebnis}
    assert namen == {"code-reviewer", "general-purpose", "python-reviewer", "security-reviewer"}
