"""Stufe 1 Sofort-Prüfung + Stufe 2 „nur Doku“ der Prüf-Tür (L3 3b-1b, #100).

Echt laufen: CLI, git, ruff und pytest im frischen Probe-Repo. Gestellt ist nur ein fehlendes
ruff (externes Werkzeug, im Prozess ausgeblendet). Ein Test des Probe-Repos schreibt eine
Marker-Datei — fehlt sie, lief kein Test.
"""

from __future__ import annotations

import json
import os
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


def _erklaere(ordner: Path, muster: object, roh: str | None = None) -> None:
    """Repo erklärt seine Doku-Pfade (``pruefen.doku_pfade``) bzw. ``roh`` als Konfig-Text; neue Basis."""
    (ordner / ".to-spawn").mkdir(exist_ok=True)
    text = roh if roh is not None else json.dumps({"pruefen": {"doku_pfade": muster}})
    (ordner / ".to-spawn" / "config.json").write_text(text, encoding="utf-8")
    with (ordner / ".gitignore").open("a", encoding="utf-8") as datei:
        datei.write(".to-spawn/*\n!.to-spawn/config.json\n")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "konfig")
    _git(ordner, "tag", "-f", "base")


def test_m11_fall_1_nur_readme_laeuft_kein_test(tmp_path: Path) -> None:
    probe = _probe(tmp_path / "py")
    _erklaere(probe, ["README.md"])
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
# In CI (CI gesetzt) ist ruff Pflicht: dort scheitert der Test, statt still übersprungen zu werden.
@pytest.mark.skipif(shutil.which("ruff") is None and not os.environ.get("CI"), reason="ruff nicht im PATH")
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
    assert _stufe(urteil.zeile, "doku")["grund"] == "Doku-Pfade nicht erklärt (pruefen.doku_pfade)"
    assert _stufe(urteil.zeile, "voll")["lief"] is True


def test_ungespeicherte_code_aenderung_hebt_nur_doku_auf(tmp_path: Path) -> None:
    # Seit Fix-Runde 1 lehnt die Tür einen ungespeicherten Baum ab (Exit 2); die Stufe selbst
    # sieht ungespeicherte Dateien weiter (zweite Sicherung, falls sie anders aufgerufen wird).
    from to_spawn.pruef import sofort

    probe = _probe(tmp_path / "py")
    _commit(probe, "README.md", "# Probe\n\nGeändert.\n")
    (probe / "neu.py").write_text("X = 1\n", encoding="utf-8")  # neu, nicht committet
    liste, halt = sofort.stufen(probe, "base")
    assert [s.grund for s in liste if s.name == "doku"] == ["Doku-Pfade nicht erklärt (pruefen.doku_pfade)"]
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
    assert _stufe(zeile, "doku")["grund"] == "Doku-Pfade nicht erklärt (pruefen.doku_pfade)"
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
    assert _stufe(zeile, "doku")["grund"] == "Doku-Pfade nicht erklärt (pruefen.doku_pfade)"


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
    assert _stufe(zeile, "doku")["grund"] == "Doku-Pfade nicht erklärt (pruefen.doku_pfade)"


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
    assert _stufe(zeile, "doku")["grund"] == "Doku-Pfade nicht erklärt (pruefen.doku_pfade)"
    assert _stufe(zeile, "voll")["lief"] is True


def test_geloeschte_code_datei_ist_keine_doku(tmp_path: Path) -> None:
    probe = _probe(tmp_path / "py")
    (probe / "rechnen.py").unlink()
    _commit(probe, "README.md", "# Probe\n\nOhne rechnen.\n")
    code, zeile = _tuer(probe)
    assert code == 1, zeile  # Test findet rechnen nicht mehr → voll lief und ist rot
    assert _stufe(zeile, "sofort")["grund"] == "keine Code-Datei geändert"
    assert _stufe(zeile, "doku")["grund"] == "Doku-Pfade nicht erklärt (pruefen.doku_pfade)"


def test_datei_ohne_endung_ist_keine_doku(tmp_path: Path) -> None:
    probe = _probe(tmp_path / "py")
    _commit(probe, "LIZENZ", "frei\n")
    code, zeile = _tuer(probe)
    assert code == 0, zeile
    assert _stufe(zeile, "doku")["grund"] == "Doku-Pfade nicht erklärt (pruefen.doku_pfade)"
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


# --- Fix-Runde 2 (#100): nackte Endung, git-Ursache, Werkzeug-Ordner nur als Ordner-Eintrag ---


def test_md_per_endswith_aus_berechnetem_ordner_ist_keine_doku_und_rot(
    tmp_path: Path,
) -> None:
    # Ordner berechnet, Dateiname und ``*.md`` stehen nirgends im Code — nur ``.md`` als Endung.
    probe = _probe_laufzeit(
        tmp_path / "py",
        """\
        import os, pathlib
        def lies():
            ordner = pathlib.Path(__file__).parent / "_".join(["haupt", "texte"])
            return "".join(
                (ordner / f).read_text() for f in os.listdir(ordner) if f.endswith(".md")
            )
        """,
        {"haupt_texte/eins.md": "ja\n"},
    )
    _commit(probe, "haupt_texte/eins.md", "nein\n")
    code, zeile = _tuer(probe)
    assert code == 1, zeile
    assert _stufe(zeile, "doku")["grund"] == "Doku-Pfade nicht erklärt (pruefen.doku_pfade)"
    assert "test_lies" in zeile["fehler_kurz"]


def test_readme_commit_mit_ungetracktem_code_ist_nie_gruen(tmp_path: Path) -> None:
    # Ursprüngliche Aussage des in Runde 1 umgeschriebenen Tests, jetzt wieder über die Tür.
    probe = _probe(tmp_path / "py")
    _commit(probe, "README.md", "# Probe\n\nGeändert.\n")
    (probe / "neu.py").write_text("X = 1\n", encoding="utf-8")
    code, zeile = _tuer(probe)
    assert code == 2, zeile
    assert "neu.py" in zeile["fehler_kurz"]


def test_git_status_scheitert_meldet_ursache_und_loggt(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from to_spawn import pruef

    monkeypatch.setenv("LC_ALL", "C")  # geprüft wird git-Text auf Englisch

    kein_repo = tmp_path / "leer"
    kein_repo.mkdir()
    with caplog.at_level("WARNING", logger="to_spawn.pruef"):
        offen = pruef._ungespeichert(kein_repo)
    assert len(offen) == 1 and offen[0].startswith("?"), offen
    assert "not a git repository" in offen[0].lower(), offen
    assert any("git status" in r.getMessage() for r in caplog.records), caplog.records


def test_ungetrackter_code_in_unterordner_venv_ist_ungespeichert(
    tmp_path: Path,
) -> None:
    from to_spawn import pruef

    probe = _probe(tmp_path / "py")
    (probe / "src" / "venv").mkdir(parents=True)
    (probe / "src" / "venv" / "x.py").write_text("X = 1\n", encoding="utf-8")
    assert pruef._ungespeichert(probe) == ["src/"]  # normal: neuer Ordner zusammengefasst
    _git(probe, "add", "src/venv/x.py")
    _git(probe, "commit", "-qm", "src")
    (probe / "src" / "venv" / "y.py").write_text("Y = 1\n", encoding="utf-8")
    assert pruef._ungespeichert(probe) == ["src/venv/y.py"]
    (probe / "src" / "__pycache__").mkdir()
    (probe / "src" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (probe / ".venv").mkdir()
    (probe / ".venv" / "a").write_text("", encoding="utf-8")
    (probe / "node_modules").write_text("", encoding="utf-8")  # Datei, kein Werkzeug-Ordner
    assert pruef._ungespeichert(probe) == ["node_modules", "src/venv/y.py"]


# --- Fix-Runde 3 (#100): „nur Doku“ nur für erklärte Doku-Pfade, sonst voll (Angriffe F1b–F8) ---

OHNE = "Doku-Pfade nicht erklärt (pruefen.doku_pfade)"
_TEST_APP = 'from app import f\n\ndef test_f():\n    assert f() == "ok"\n'


def _angriff(ordner: Path, dateien: dict[str, str], links: dict[str, str] | None = None) -> Path:
    """Grünes Repo aus ``dateien`` + ``tests/test_app.py`` (``f() == "ok"``), Symlinks ``links``; Tag ``base``."""
    ordner.mkdir()
    alle = {
        ".gitignore": "__pycache__/\n.pytest_cache/\n",
        "conftest.py": "",
        "tests/test_app.py": _TEST_APP,
        **dateien,
    }
    for pfad, text in alle.items():
        (ordner / pfad).parent.mkdir(parents=True, exist_ok=True)
        (ordner / pfad).write_text(text, encoding="utf-8")
    for link, ziel in (links or {}).items():
        try:
            os.symlink(ziel, ordner / link)
        except OSError as fehler:  # Windows ohne Symlink-Recht
            pytest.skip(f"Symlink nicht anlegbar: {fehler}")
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "base")
    _git(ordner, "tag", "base")
    return ordner


def _voll_rot(ordner: Path, grund: str) -> None:
    code, zeile = _tuer(ordner)
    assert code == 1, zeile
    assert _stufe(zeile, "doku")["grund"] == grund
    assert _stufe(zeile, "voll")["lief"] is True
    assert "test_" in zeile["fehler_kurz"], zeile


_F1C = {
    "README.md": "# Titel\n",
    "app.py": 'def f():\n    return "ok"\n',
    "tests/test_hygiene.py": textwrap.dedent("""\
        import subprocess
        from pathlib import Path
        WURZEL = Path(__file__).resolve().parents[1]
        def test_keine_tabs():
            dateien = subprocess.run(["git", "ls-files"], cwd=WURZEL, capture_output=True,
                                     text=True).stdout.split()
            for d in dateien:
                assert "\\t" not in (WURZEL / d).read_text(errors="ignore"), d
        """),
}
_F6 = {
    "doku/x.md": "ok\n",
    "app.py": 'from pathlib import Path\ndef f():\n    return (Path(__file__).parent / "vorlage.txt").read_text().strip()\n',
}
_ITERDIR = 'from pathlib import Path\ndef f():\n    return sorted((Path(__file__).parent / "zz").iterdir())[0].read_text().strip()\n'


def test_f1c_hygiene_test_ueber_git_ls_files_ohne_konfig_voll_und_rot(tmp_path: Path) -> None:
    probe = _angriff(tmp_path / "f1c", _F1C)
    _commit(probe, "README.md", "# Titel\n\tEingerückt\n")
    _voll_rot(probe, OHNE)


def test_f1b_iterdir_ueber_ordner_aus_env_ohne_konfig_voll_und_rot(tmp_path: Path) -> None:
    probe = _angriff(
        tmp_path / "f1b",
        {
            "texte/gruss.md": "ok\n",
            "app.py": 'import os\nfrom pathlib import Path\ndef f():\n    d = Path(os.environ["APP_DATEN"])\n'
            "    return next(p for p in sorted(d.iterdir())).read_text().strip()\n",
            "tests/conftest.py": textwrap.dedent("""\
                import os, sys
                from pathlib import Path
                w = Path(__file__).resolve().parents[1]
                sys.path.insert(0, str(w))
                os.environ.setdefault("APP_DATEN", str(next(
                    p for p in sorted(w.iterdir())
                    if p.is_dir() and not p.name.startswith((".", "_")) and p.name != "tests")))
                """),
        },
    )
    _commit(probe, "texte/gruss.md", "bad\n")
    _voll_rot(probe, OHNE)


def test_f6_symlink_auf_doku_ohne_konfig_voll_und_rot(tmp_path: Path) -> None:
    probe = _angriff(tmp_path / "f6", _F6, {"vorlage.txt": "doku/x.md"})
    _commit(probe, "doku/x.md", "bad\n")
    _voll_rot(probe, OHNE)


def test_f7b_git_mv_aendert_erste_datei_im_ordner_ohne_konfig_voll_und_rot(tmp_path: Path) -> None:
    probe = _angriff(tmp_path / "f7b", {"zz/a.md": "ok\n", "zz/m.md": "bad\n", "app.py": _ITERDIR})
    _git(probe, "mv", "zz/a.md", "zz/z.md")
    _git(probe, "commit", "-qm", "mv")
    _voll_rot(probe, OHNE)


def test_f8_erkennung_per_mimetypes_ohne_konfig_voll_und_rot(tmp_path: Path) -> None:
    probe = _angriff(
        tmp_path / "f8",
        {
            "zz/a.md": "ok\n",
            "zz/b.py": "x\n",
            "app.py": "import mimetypes\nfrom pathlib import Path\ndef f():\n"
            '    d = Path(__file__).parent / "zz"\n'
            '    return next(p for p in d.iterdir() if mimetypes.guess_type(p.name)[0] == "text/markdown")'
            ".read_text().strip()\n",
        },
    )
    _commit(probe, "zz/a.md", "bad\n")
    _voll_rot(probe, OHNE)


def test_erklaerter_pfad_mit_unterordner_ist_nur_doku(tmp_path: Path) -> None:
    probe = _probe(tmp_path / "py")
    (probe / "docs" / "teil").mkdir(parents=True)
    (probe / "docs" / "teil" / "a.md").write_text("alt\n", encoding="utf-8")
    _erklaere(probe, ["docs/**", "README.md"])
    _commit(probe, "docs/teil/a.md", "neu\n")
    (probe / "README.md").unlink()  # gelöscht, aber erklärt
    _commit(probe, "docs/neu.md", "neu\n")
    code, zeile = _tuer(probe)
    assert code == 0, zeile
    assert (_stufe(zeile, "voll")["lief"], _stufe(zeile, "voll")["grund"]) == (False, "nur Doku")
    assert not (probe / "lief.txt").exists()


def test_datei_ausserhalb_der_erklaerten_pfade_prueft_voll(tmp_path: Path) -> None:
    probe = _angriff(tmp_path / "aussen", _F1C)
    _erklaere(probe, ["docs/**"])
    _commit(probe, "README.md", "# Titel\n\tEingerückt\n")
    _voll_rot(probe, "nicht nur Doku geändert")


def test_symlink_auf_erklaerte_doku_prueft_voll(tmp_path: Path) -> None:
    probe = _angriff(tmp_path / "f6k", _F6, {"vorlage.txt": "doku/x.md"})
    _erklaere(probe, ["doku/**"])
    _commit(probe, "doku/x.md", "bad\n")
    _voll_rot(probe, "Symlink geändert oder zeigt auf geänderte Datei")


def test_neuer_symlink_im_erklaerten_pfad_prueft_voll(tmp_path: Path) -> None:
    probe = _probe(tmp_path / "py")
    _erklaere(probe, ["docs/**"])
    (probe / "docs").mkdir()
    try:
        # Windows löst „/“ im Link-Ziel nicht auf (Errno 22) — Trenner des Systems nehmen.
        os.symlink(os.path.join("..", "rechnen.py"), probe / "docs" / "code.md")
    except OSError as fehler:
        pytest.skip(f"Symlink nicht anlegbar: {fehler}")
    _git(probe, "add", "-A")
    _git(probe, "commit", "-qm", "link")
    code, zeile = _tuer(probe)
    assert code == 0, zeile
    assert _stufe(zeile, "doku")["grund"] == "Symlink geändert oder zeigt auf geänderte Datei"
    assert _stufe(zeile, "voll")["lief"] is True


def test_umbenennung_aus_erklaertem_pfad_heraus_prueft_voll(tmp_path: Path) -> None:
    probe = _angriff(tmp_path / "mv", {"docs/a.md": "bad\n", "zz/m.md": "ok\n", "app.py": _ITERDIR})
    _erklaere(probe, ["docs/**"])
    _git(probe, "mv", "docs/a.md", "zz/a.md")
    _git(probe, "commit", "-qm", "mv")
    _voll_rot(probe, "nicht nur Doku geändert")


@pytest.mark.parametrize(
    ("roh", "grund"),
    [
        ("{kaputt", OHNE),
        ('{"pruefen": {"doku_pfade": "docs/**"}}', "pruefen.doku_pfade ungültig (Liste von Pfad-Mustern erwartet)"),
        ('{"pruefen": {"doku_pfade": []}}', "pruefen.doku_pfade ungültig (Liste von Pfad-Mustern erwartet)"),
    ],
)
def test_kaputte_konfig_prueft_voll_und_meldet(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, roh: str, grund: str
) -> None:
    from to_spawn import pruef

    probe = _angriff(tmp_path / "kaputt", _F1C)
    _erklaere(probe, None, roh=roh)
    _commit(probe, "README.md", "# Titel\n\tEingerückt\n")
    with caplog.at_level("WARNING"):
        urteil = pruef.pruefen(probe, "base", "HEAD")
    assert urteil.exit == 1, urteil.zeile
    assert _stufe(urteil.zeile, "doku")["grund"] == grund
    assert _stufe(urteil.zeile, "voll")["lief"] is True
    assert any(r.levelname == "WARNING" for r in caplog.records), caplog.records


# --- Fix-Runde 4 (#100): der geprüfte Commit bestimmt nie selbst, wie streng er geprüft wird ---

KONFIG_GEAENDERT = "Konfig geändert (.to-spawn/config.json)"


def _konfig(ordner: Path, muster: object) -> None:
    (ordner / ".to-spawn").mkdir(exist_ok=True)
    (ordner / ".to-spawn" / "config.json").write_text(json.dumps({"pruefen": {"doku_pfade": muster}}), encoding="utf-8")


@pytest.mark.parametrize(
    ("basis_muster", "neu_muster"),
    [(["docs/**"], ["*"]), (["docs/**"], ["**"]), (None, [".to-spawn/*", "README.md"]), (["*"], ["*"])],
)
def test_konfig_im_selben_commit_geaendert_prueft_voll(
    tmp_path: Path, basis_muster: object, neu_muster: object
) -> None:
    probe = _angriff(tmp_path / "b9", _F1C)
    if basis_muster is not None:
        _erklaere(probe, basis_muster)
    _konfig(probe, neu_muster)
    (probe / ".to-spawn" / "config.json").write_text(
        json.dumps({"pruefen": {"doku_pfade": neu_muster}, "x": 1}), encoding="utf-8"
    )
    _commit(probe, "README.md", "# Titel\n\tEingerückt\n")
    _voll_rot(probe, KONFIG_GEAENDERT)


def test_konfig_erst_im_commit_angelegt_prueft_voll(tmp_path: Path) -> None:
    probe = _angriff(tmp_path / "b9neu", _F1C)
    with (probe / ".gitignore").open("a", encoding="utf-8") as datei:
        datei.write(".to-spawn/*\n!.to-spawn/config.json\n")
    _konfig(probe, ["**"])
    _commit(probe, "README.md", "# Titel\n\tEingerückt\n")
    _voll_rot(probe, KONFIG_GEAENDERT)


def test_doku_muster_kommen_aus_der_basis(tmp_path: Path) -> None:
    probe = _probe(tmp_path / "py")
    _erklaere(probe, ["README.md"])
    _commit(probe, "README.md", "# Probe\n\nNeu.\n")
    _konfig(probe, ["docs/**"])  # Arbeitsbaum erklärt README nicht mehr — gilt nicht, Basis zählt
    from to_spawn.pruef import sofort

    assert sofort._doku_muster(probe, "base", ["README.md"]) == (["README.md"], "")


@pytest.mark.parametrize("art", ["ungespeichert", "untracked", "ignoriert"])
def test_konfig_nicht_committet_ist_gate_fehler(tmp_path: Path, art: str) -> None:
    probe = _probe(tmp_path / "py")
    if art == "ungespeichert":
        _erklaere(probe, ["docs/**"])
    elif art == "ignoriert":
        with (probe / ".gitignore").open("a", encoding="utf-8") as datei:
            datei.write(".to-spawn/\n")
        _git(probe, "add", "-A")
        _git(probe, "commit", "-qm", "ignoriert")
        _git(probe, "tag", "-f", "base")
    _commit(probe, "README.md", "# Probe\n\nNeu.\n")
    _konfig(probe, ["*"])
    code, zeile = _tuer(probe)
    assert code == 2, zeile
    assert ".to-spawn/config.json" in zeile["fehler_kurz"], zeile
    assert not (probe / "lief.txt").exists()


def test_laufdateien_unter_to_spawn_sind_nicht_ungespeichert(tmp_path: Path) -> None:
    from to_spawn import pruef

    probe = _probe(tmp_path / "py")
    for pfad in ("pruef/protokoll.jsonl", "sessions/a.json", "stand.json"):
        (probe / ".to-spawn" / pfad).parent.mkdir(parents=True, exist_ok=True)
        (probe / ".to-spawn" / pfad).write_text("{}", encoding="utf-8")
    assert pruef._ungespeichert(probe) == []
