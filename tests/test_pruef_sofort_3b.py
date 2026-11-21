"""Stufe 1 Sofort-Prüfung + Stufe 2 „nur Doku“ der Prüf-Tür (L3 3b-1b, #100).

Echt laufen: CLI, git, ruff und pytest im frischen Probe-Repo. Gestellt ist nur ein fehlendes
ruff (externes Werkzeug, im Prozess ausgeblendet). Ein Test des Probe-Repos schreibt eine
Marker-Datei — fehlt sie, lief kein Test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
sys.path.insert(0, str(SKILL))


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
    )


def _probe(ordner: Path) -> Path:
    """Python-Repo mit Code, README und einem Test, der ``lief.txt`` schreibt; Tag ``base``."""
    ordner.mkdir()
    (ordner / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (ordner / "README.md").write_text("# Probe\n", encoding="utf-8")
    (ordner / "rechnen.py").write_text("def addiere(a, b):\n    return a + b\n", encoding="utf-8")
    (ordner / "tests").mkdir()
    (ordner / "tests" / "test_rechnen.py").write_text(
        textwrap.dedent("""\
            import pathlib, sys
            WURZEL = pathlib.Path(__file__).parent.parent
            sys.path.insert(0, str(WURZEL))
            from rechnen import addiere

            def test_addiere():
                (WURZEL / "lief.txt").write_text("ja")
                assert addiere(1, 2) == 3
            """),
        encoding="utf-8",
    )
    (ordner / ".gitignore").write_text("lief.txt\n", encoding="utf-8")
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "base")
    _git(ordner, "tag", "base")
    return ordner


def _commit(ordner: Path, datei: str, text: str) -> None:
    (ordner / datei).write_text(text, encoding="utf-8")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", f"ändert {datei}")


def _tuer(ordner: Path) -> tuple[int, dict]:
    erg = subprocess.run(
        [sys.executable, str(CLI), "pruefen", "base", "HEAD"],
        cwd=ordner,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    zeilen = erg.stdout.strip().splitlines()
    assert len(zeilen) == 1, erg.stdout + erg.stderr
    return erg.returncode, json.loads(zeilen[0])


def _stufe(zeile: dict, name: str) -> dict:
    treffer = [stufe for stufe in zeile["stufen"] if stufe["name"] == name]
    assert len(treffer) == 1, zeile["stufen"]
    return treffer[0]


def test_m11_fall_1_nur_readme_laeuft_kein_test(tmp_path: Path) -> None:
    probe = _probe(tmp_path / "py")
    _commit(probe, "README.md", "# Probe\n\nNeuer Absatz.\n")
    start = time.monotonic()
    code, zeile = _tuer(probe)
    dauer = time.monotonic() - start
    assert code == 0, zeile
    assert not (probe / "lief.txt").exists(), "kein Test darf laufen"
    assert _stufe(zeile, "doku")["grund"] == "nur Doku"
    voll = _stufe(zeile, "voll")
    assert (voll["lief"], voll["grund"]) == (False, "nur Doku")
    assert zeile["gesamt_s"] < 10 and dauer < 10, (zeile["gesamt_s"], dauer)
    assert {"dauer_s"} <= _stufe(zeile, "sofort").keys()


# Echte Werkzeug-Grenze: ohne ruff gibt es keine Stufe 1 für Python (Fall „fehlt“ prüft der Test unten).
@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff nicht im PATH")
def test_syntax_fehler_ist_rot_in_stufe_1_und_voll_laeuft_nicht(tmp_path: Path) -> None:
    probe = _probe(tmp_path / "py")
    _commit(probe, "rechnen.py", "def addiere(a, b)\n    return a + b\n")
    code, zeile = _tuer(probe)
    assert code == 1, zeile
    sofort = _stufe(zeile, "sofort")
    assert (sofort["lief"], sofort["adapter"]) == (True, "py")
    assert "rechnen.py" in zeile["fehler_kurz"]
    assert _stufe(zeile, "voll")["lief"] is False
    assert not (probe / "lief.txt").exists(), "Stufe voll darf nach Rot nicht laufen"
    assert any(w.startswith("ruff@") for w in zeile["werkzeug"])


def test_fehlendes_ruff_ist_ausgelassen_nicht_gruen_und_voll_prueft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from to_spawn import pruef
    from to_spawn.pruef import sofort

    probe = _probe(tmp_path / "py")
    _commit(probe, "rechnen.py", "def addiere(a, b):\n    return b + a\n")
    echt = shutil.which
    monkeypatch.setattr(
        sofort.shutil,
        "which",
        lambda name, **kw: None if name == "ruff" else echt(name, **kw),
    )
    urteil = pruef.pruefen(probe, "base", "HEAD")
    assert urteil.exit == 0, urteil.zeile
    stufe = _stufe(urteil.zeile, "sofort")
    assert (stufe["lief"], stufe["grund"]) == (False, "Werkzeug fehlt (ruff)")
    assert _stufe(urteil.zeile, "voll")["lief"] is True
    assert (probe / "lief.txt").exists()


def test_doku_die_ein_test_liest_ist_nicht_nur_doku(tmp_path: Path) -> None:
    from to_spawn import pruef

    probe = _probe(tmp_path / "py")
    _commit(probe, "tests/test_readme.py", "def test_readme():\n    assert 'README.md'\n")
    _git(probe, "tag", "-f", "base")
    _commit(probe, "README.md", "# Probe\n\nGeändert.\n")
    urteil = pruef.pruefen(probe, "base", "HEAD")
    assert _stufe(urteil.zeile, "doku")["grund"] == "nicht nur Doku geändert"
    assert _stufe(urteil.zeile, "voll")["lief"] is True


def test_ungespeicherte_code_aenderung_hebt_nur_doku_auf(tmp_path: Path) -> None:
    # Seit Fix-Runde 1 lehnt die Tür einen ungespeicherten Baum ab (Exit 2); die Stufe selbst
    # sieht ungespeicherte Dateien weiter (zweite Sicherung, falls sie anders aufgerufen wird).
    from to_spawn.pruef import sofort

    probe = _probe(tmp_path / "py")
    _commit(probe, "README.md", "# Probe\n\nGeändert.\n")
    (probe / "neu.py").write_text("X = 1\n", encoding="utf-8")  # neu, nicht committet
    liste, halt = sofort.stufen(probe, "base")
    assert [s.grund for s in liste if s.name == "doku"] == ["nicht nur Doku geändert"]
    assert halt == ""


# --- Fix-Runde 1 (#100): Laufzeit-Doku, ESLint-Zweig, umbenannt/gelöscht/ohne Endung ---


def _probe_laufzeit(ordner: Path, code: str, doku: dict[str, str]) -> Path:
    """Repo, dessen ``app.py`` Doku zur Laufzeit liest; der Test prüft, dass ``ja`` drinsteht."""
    probe = _probe(ordner)
    for pfad, text in doku.items():
        (probe / pfad).parent.mkdir(parents=True, exist_ok=True)
        (probe / pfad).write_text(text, encoding="utf-8")
    (probe / "app.py").write_text(textwrap.dedent(code), encoding="utf-8")
    (probe / "tests" / "test_app.py").write_text(
        textwrap.dedent("""\
            import pathlib, sys
            sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
            from app import lies

            def test_lies():
                assert "ja" in lies()
            """),
        encoding="utf-8",
    )
    _git(probe, "add", "-A")
    _git(probe, "commit", "-qm", "laufzeit")
    _git(probe, "tag", "-f", "base")
    return probe


def test_prompt_md_die_code_liest_ist_keine_doku_und_rot(tmp_path: Path) -> None:
    probe = _probe_laufzeit(
        tmp_path / "py",
        """\
        import pathlib
        def lies():
            return (pathlib.Path(__file__).parent / "prompts" / "system.md").read_text()
        """,
        {"prompts/system.md": "ja\n"},
    )
    _commit(probe, "prompts/system.md", "nein\n")
    code, zeile = _tuer(probe)
    assert code == 1, zeile
    assert _stufe(zeile, "doku")["grund"] == "nicht nur Doku geändert"
    assert "test_lies" in zeile["fehler_kurz"]


def test_md_in_ordner_den_code_liest_ist_keine_doku(tmp_path: Path) -> None:
    probe = _probe_laufzeit(
        tmp_path / "py",
        """\
        import pathlib
        def lies():
            ordner = pathlib.Path(__file__).parent / "vorlagen"
            return "".join(f.read_text() for f in ordner.iterdir())
        """,
        {"vorlagen/eins.md": "ja\n"},
    )
    _commit(probe, "vorlagen/eins.md", "nein\n")
    code, zeile = _tuer(probe)
    assert code == 1, zeile
    assert _stufe(zeile, "doku")["grund"] == "nicht nur Doku geändert"


def test_md_die_code_per_endungs_glob_liest_ist_keine_doku(tmp_path: Path) -> None:
    probe = _probe_laufzeit(
        tmp_path / "py",
        """\
        import pathlib
        def lies():
            return "".join(f.read_text() for f in pathlib.Path(__file__).parent.glob("*.md"))
        """,
        {"HILFE.md": "ja\n"},
    )
    (probe / "README.md").unlink()
    _git(probe, "add", "-A")
    _git(probe, "commit", "-qm", "ohne readme")
    _git(probe, "tag", "-f", "base")
    _commit(probe, "HILFE.md", "nein\n")
    code, zeile = _tuer(probe)
    assert code == 1, zeile
    assert _stufe(zeile, "doku")["grund"] == "nicht nur Doku geändert"


def test_umbenannte_code_datei_meldet_alt_und_neu_und_ist_keine_doku(
    tmp_path: Path,
) -> None:
    from to_spawn.pruef import sofort

    probe = _probe(tmp_path / "py")
    _git(probe, "mv", "rechnen.py", "rechner.py")
    _commit(
        probe,
        "tests/test_rechnen.py",
        (probe / "tests" / "test_rechnen.py").read_text().replace("from rechnen", "from rechner"),
    )
    assert {"rechnen.py", "rechner.py"} <= set(sofort.geaendert(probe, "base") or [])
    code, zeile = _tuer(probe)
    assert code == 0, zeile
    assert _stufe(zeile, "doku")["grund"] == "nicht nur Doku geändert"
    assert _stufe(zeile, "voll")["lief"] is True


def test_geloeschte_code_datei_ist_keine_doku(tmp_path: Path) -> None:
    probe = _probe(tmp_path / "py")
    (probe / "rechnen.py").unlink()
    _commit(probe, "README.md", "# Probe\n\nOhne rechnen.\n")
    code, zeile = _tuer(probe)
    assert code == 1, zeile  # Test findet rechnen nicht mehr → voll lief und ist rot
    assert _stufe(zeile, "sofort")["grund"] == "keine Code-Datei geändert"
    assert _stufe(zeile, "doku")["grund"] == "nicht nur Doku geändert"


def test_datei_ohne_endung_ist_keine_doku(tmp_path: Path) -> None:
    probe = _probe(tmp_path / "py")
    _commit(probe, "LIZENZ", "frei\n")
    code, zeile = _tuer(probe)
    assert code == 0, zeile
    assert _stufe(zeile, "doku")["grund"] == "nicht nur Doku geändert"
    assert _stufe(zeile, "voll")["lief"] is True
    assert (probe / "lief.txt").exists()


def _probe_node(ordner: Path, mit_eslint: bool) -> Path:
    """Node-Repo mit ``app.js``; ESLint ist die Attrappe ``eslint_attrappe_3b.py`` (plattformneutral)."""
    ordner.mkdir()
    (ordner / "package.json").write_text('{"scripts": {"test": "node -e 0"}}', encoding="utf-8")
    (ordner / "app.js").write_text("module.exports = 1;\n", encoding="utf-8")
    (ordner / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    if mit_eslint:
        attrappe = Path(__file__).resolve().parent / "eslint_attrappe_3b.py"
        bin_ordner = ordner / "node_modules" / ".bin"
        bin_ordner.mkdir(parents=True)
        if sys.platform == "win32":
            (bin_ordner / "eslint.cmd").write_text(f'@"{sys.executable}" "{attrappe}" %*\r\n', encoding="utf-8")
        else:
            ziel = bin_ordner / "eslint"
            ziel.write_text(
                f"#!{sys.executable}\n" + attrappe.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            ziel.chmod(0o755)
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "base")
    _git(ordner, "tag", "base")
    _commit(ordner, "app.js", "module.exports = 2;\n")
    return ordner


def test_eslint_fehlt_ist_ausgelassen(tmp_path: Path) -> None:
    from to_spawn.pruef import leiter, sofort

    liste, halt = sofort.stufen(_probe_node(tmp_path / "node", mit_eslint=False), "base")
    stufe = liste[0]
    assert (stufe.name, stufe.adapter, stufe.lief, stufe.grund) == (
        "sofort",
        "node",
        False,
        "Werkzeug fehlt (eslint)",
    )
    assert stufe.ergebnis == leiter.AUSGELASSEN and halt == ""


@pytest.mark.parametrize(
    ("exit_code", "erwartet_lief", "erwartet_halt"),
    [(0, True, ""), (1, True, "Stufe sofort rot (schnell rot)"), (2, False, "")],
)
def test_eslint_gruen_rot_und_kaputt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    erwartet_lief: bool,
    erwartet_halt: str,
) -> None:
    from to_spawn.pruef import leiter, sofort

    monkeypatch.setenv("ESLINT_ATTRAPPE_EXIT", str(exit_code))
    liste, halt = sofort.stufen(_probe_node(tmp_path / "node", mit_eslint=True), "base")
    stufe = liste[0]
    assert (stufe.name, stufe.adapter, stufe.lief) == (
        "sofort",
        "node",
        erwartet_lief,
    ), stufe
    assert halt == erwartet_halt
    assert stufe.werkzeug == "eslint@v9.0.0"
    erwartet = {0: leiter.GRUEN, 1: leiter.ROT, 2: leiter.AUSGELASSEN}[exit_code]
    assert stufe.ergebnis == erwartet
    if exit_code == 1:
        assert "app.js" in stufe.ausgabe
    if exit_code == 2:
        assert stufe.grund.startswith("eslint lief nicht (Exit 2")
