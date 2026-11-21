"""#164: Die Prüf-Tür lehnt zu weite Doku-Muster selbst ab — auch wenn ein Vorgänger-Commit sie setzt.

Angriff (Zwei-Schritt): Commit A setzt ``pruefen.doku_pfade`` auf ``**``, Commit B ändert Code.
``pruefen A B`` liest die Konfig aus A (Basis) — ohne eigene Prüfung bekäme B die Abkürzung
„nur Doku“ und wäre grün, obwohl seine Tests rot sind. Soll: kein „nur Doku“, voller Lauf, rot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_pruef_sofort_3b as sofort_test

ZU_WEIT = ["**", "*", "**/*", "*.*", "*.py", "?*"]


@pytest.mark.parametrize("muster", ZU_WEIT)
def test_vorgaenger_commit_mit_zu_weitem_muster_gibt_code_commit_keine_abkuerzung(tmp_path: Path, muster: str) -> None:
    probe = sofort_test._angriff(tmp_path / "zwei", {"README.md": "# Titel\n", "app.py": 'def f():\n    return "ok"\n'})
    sofort_test._erklaere(probe, ["docs/**", muster])  # Commit A, Tag base = A
    sofort_test._commit(probe, "app.py", 'def f():\n    return "kaputt"\n')  # Commit B: Code, Test rot
    code, zeile = sofort_test._tuer(probe)
    doku = sofort_test._stufe(zeile, "doku")
    assert doku["grund"] != "nur Doku", zeile
    assert doku["grund"].startswith("pruefen.doku_pfade zu weit"), doku
    assert f"„{muster}“" in doku["grund"], doku
    assert sofort_test._stufe(zeile, "voll")["lief"] is True
    assert code == 1, zeile


@pytest.mark.parametrize("muster", ZU_WEIT)
def test_zu_weites_muster_sperrt_auch_reine_doku_aenderung(tmp_path: Path, muster: str) -> None:
    from to_spawn.pruef import sofort

    probe = sofort_test._probe(tmp_path / "py")
    sofort_test._erklaere(probe, [muster])
    assert sofort._doku_muster(probe, "base", ["README.md"]) == (
        [],
        f"pruefen.doku_pfade zu weit: „{muster}“ trifft Code-Dateien",
    )


@pytest.mark.parametrize("muster", [["docs/**", "README.md"], ["*.md"], ["docs/*.md", "CHANGELOG"]])
def test_enge_muster_bleiben_gueltig(tmp_path: Path, muster: list[str]) -> None:
    from to_spawn.pruef import sofort

    probe = sofort_test._probe(tmp_path / "py")
    sofort_test._erklaere(probe, muster)
    assert sofort._doku_muster(probe, "base", ["README.md"]) == (muster, "")
