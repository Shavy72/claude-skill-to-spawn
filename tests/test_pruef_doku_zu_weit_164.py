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


#: Fix-Runde 1 (PR 187): Muster, die echte geänderte Code-/Test-Dateien treffen, aber keine erfundenen.
ROT_TEST = "def test_rot():\n    assert False\n"
ECHTE_CODE_TREFFER = [
    ("rechnen.py", "rechnen.py", "def addiere(a, b):\n    return a - b\n"),
    ("re*", "rechnen.py", "def addiere(a, b):\n    return a - b\n"),
    ("tests/**", "tests/test_rechnen.py", ROT_TEST),
    ("*test*", "tests/test_rechnen.py", ROT_TEST),
]


@pytest.mark.parametrize(("muster", "datei", "text"), ECHTE_CODE_TREFFER)
def test_muster_trifft_echte_geaenderte_code_datei_keine_abkuerzung(
    tmp_path: Path, muster: str, datei: str, text: str
) -> None:
    probe = sofort_test._probe(tmp_path / "py")
    sofort_test._erklaere(probe, ["docs/**", muster])  # Commit A, Tag base = A
    sofort_test._commit(probe, datei, text)  # Commit B: Code/Test, Lauf rot
    code, zeile = sofort_test._tuer(probe)
    doku = sofort_test._stufe(zeile, "doku")
    assert doku["grund"] != "nur Doku", zeile
    assert doku["grund"].startswith("pruefen.doku_pfade zu weit"), doku
    assert f"„{muster}“" in doku["grund"], doku
    assert sofort_test._stufe(zeile, "voll")["lief"] is True
    assert code == 1, zeile


@pytest.mark.parametrize("muster", [["docs/**", "README.md"], ["*.md"], ["docs/*.md", "CHANGELOG"]])
def test_enge_muster_bleiben_gueltig(tmp_path: Path, muster: list[str]) -> None:
    from to_spawn.pruef import sofort

    probe = sofort_test._probe(tmp_path / "py")
    sofort_test._erklaere(probe, muster)
    assert sofort._doku_muster(probe, "base", ["README.md"]) == (muster, "")


#: Fix-Runde 2 (PR 187): Erlaubnisliste statt Verbotsliste. ``app.py`` liest die Datei ``ziel``; Commit A
#: erklärt ``muster``, Commit B ändert (oder löscht) ``ziel`` — der Test ist rot, also nie „nur Doku“.
def _liest(ziel: str) -> str:
    return f'from pathlib import Path\ndef f():\n    return (Path(__file__).parent / "{ziel}").read_text().strip()\n'


NICHT_DOKU_ENDUNG = [
    ("tool", "tool", "#!/bin/sh\necho kaputt\n", "geaendert"),
    ("*.sh", "run.sh", "kaputt\n", "geaendert"),
    ("*.sh", "run.sh", "", "geloescht"),
    ("*.ps1", "start.ps1", "kaputt\n", "geaendert"),
    ("*.json", "einstellung.json", "kaputt\n", "geaendert"),
    ("Makefile", "Makefile", "kaputt\n", "geaendert"),
    ("*.toml", "pyproject.toml", "kaputt\n", "geaendert"),
    ("docs/**", "docs/start.sh", "", "geloescht"),
]


def _voll_und_rot(probe: Path, gate_fehler: bool = False) -> None:
    """Voller Lauf statt „nur Doku“; rot (1) bzw. Gate-Fehler (2), wenn die Datei pytest selbst bricht."""
    code, zeile = sofort_test._tuer(probe)
    assert sofort_test._stufe(zeile, "doku")["grund"] != "nur Doku", zeile
    assert sofort_test._stufe(zeile, "voll")["lief"] is True, zeile
    if gate_fehler:
        assert code == 2, zeile
        return
    assert code == 1, zeile
    assert "test_" in zeile["fehler_kurz"], zeile


@pytest.mark.parametrize(("muster", "datei", "text", "art"), NICHT_DOKU_ENDUNG)
def test_datei_ohne_doku_endung_ist_nie_doku(tmp_path: Path, muster: str, datei: str, text: str, art: str) -> None:
    probe = sofort_test._angriff(tmp_path / "e", {"README.md": "# Titel\n", datei: "ok\n", "app.py": _liest(datei)})
    sofort_test._erklaere(probe, ["docs/**", "README.md", muster])  # Commit A, Tag base = A
    if art == "geloescht":
        (probe / datei).unlink()
        sofort_test._git(probe, "add", "-A")
        sofort_test._git(probe, "commit", "-qm", f"löscht {datei}")
    else:
        sofort_test._commit(probe, datei, text)
    _voll_und_rot(probe, gate_fehler=datei == "pyproject.toml")  # kaputtes TOML bricht pytest-Konfig


def test_umbenennung_doku_zu_code_ist_nie_doku(tmp_path: Path) -> None:
    probe = sofort_test._angriff(tmp_path / "u", {"docs/hilfe.md": "ok\n", "app.py": _liest("docs/hilfe.md")})
    sofort_test._erklaere(probe, ["docs/**"])
    sofort_test._git(probe, "mv", "docs/hilfe.md", "docs/hilfe.sh")
    sofort_test._git(probe, "commit", "-qm", "benennt um")
    _voll_und_rot(probe)


@pytest.mark.parametrize(("datei", "text"), [("docs/x.md", "# Neu\n"), ("docs/bild.png", "png"), ("CHANGELOG", "neu\n")])
def test_echte_doku_bleibt_nur_doku(tmp_path: Path, datei: str, text: str) -> None:
    probe = sofort_test._probe(tmp_path / "g")
    sofort_test._erklaere(probe, ["docs/**", "README.md", "CHANGELOG"])
    (probe / "docs").mkdir(exist_ok=True)
    sofort_test._commit(probe, datei, text)
    code, zeile = sofort_test._tuer(probe)
    assert sofort_test._stufe(zeile, "doku")["grund"] == "nur Doku", zeile
    assert sofort_test._stufe(zeile, "voll")["lief"] is False, zeile
    assert code == 0, zeile
    assert not (probe / "lief.txt").exists(), "kein Test darf laufen"


_LETZTE_ZEILE = 'from pathlib import Path\ndef f():\n    return (Path(__file__).parent / "docs/start.txt").read_text().splitlines()[-1]\n'


@pytest.mark.parametrize("art", ["geaendert", "geloescht"])
def test_datei_mit_shebang_ist_nie_doku(tmp_path: Path, art: str) -> None:
    """Shebang in Zeile 1 heißt Skript, auch mit Doku-Endung und passendem Muster (gelöscht: Stand der Basis)."""
    probe = sofort_test._angriff(tmp_path / "s", {"docs/start.txt": "#!/bin/sh\nok\n", "app.py": _LETZTE_ZEILE})
    sofort_test._erklaere(probe, ["docs/**"])
    if art == "geloescht":
        (probe / "docs/start.txt").unlink()
        sofort_test._git(probe, "add", "-A")
        sofort_test._git(probe, "commit", "-qm", "löscht docs/start.txt")
    else:
        sofort_test._commit(probe, "docs/start.txt", "#!/bin/sh\nkaputt\n")
    _voll_und_rot(probe)
