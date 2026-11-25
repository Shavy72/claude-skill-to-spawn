"""#796 (#580 Teil 3): workflow_call-Vorlage Stufe 0+1, CI-Läufer und Eigenanschluss (Linux + Windows).

Prüft plattformübergreifend ohne externe YAML-Abhängigkeit und ohne ``testmon``:
- ``to_spawn.pruef.pipeline_vorlage`` erzeugt die wiederverwendbare ``workflow_call``-Vorlage
  (Stufe 0: ruff, Typ-Prüfung, Test-Schutz; Stufe 1: Import-Graph-Testauswahl auf GitHub Actions)
  mit vollständig dokumentierten Eingaben sowie die Eigenanschluss-Vorlage für das Skill-Repo.
- Strukturprüfung erkennt gültige Vorlagen als fehlerfrei und meldet fehlende Eingaben,
  fehlende ``workflow_call``-Sektion oder verbotene ``testmon``-Verweise.
- ``import_graph.auswaehlen(..., basis=..., ziel=...)`` und CLI ``--ziel`` arbeiten auch mit
  explizitem Ziel-Commit und unter cp1252-Pipes (Windows) mit sauberem UTF-8.
- Echte Weg-Tests auf frischen Git-Repos für Stufe 0 (grün + rot bei Test-Schwächung) und
  Stufe 1 (nur abhängige Tests laufen, 0 betroffene Tests grün ohne Exit 5, Auslassungen,
  Pfad-Filter, Markdown-Zusammenfassung, cp1252-Pipe).
- Kein ``testmon`` in Vorlagen, Doku oder Produktivcode unter ``to_spawn/``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import prozessbaum
from to_spawn.pruef import import_graph, pipeline_vorlage


def _git(ordner: Path, *args: str) -> str:
    erg = subprocess.run(
        ["git", "-c", "user.name=probe", "-c", "user.email=probe@example.invalid", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **prozessbaum.fensterlos(),
    )
    return erg.stdout.strip()


def _schreibe(ordner: Path, rel: str, inhalt: str) -> Path:
    ziel = ordner / rel
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(inhalt, encoding="utf-8")
    return ziel


def _cli(
    modul: str, *args: str, cwd: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    umg = {**os.environ, "PYTHONPATH": str(SKILL)}
    if extra_env:
        umg.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", modul, *args],
        cwd=cwd,
        env=umg,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **prozessbaum.fensterlos(),
    )


def _probe_repo(ordner: Path, spur_datei: Path | None = None) -> Path:
    """Echtes Git-Repo mit Paket-Abhängigkeitskette, Tests und Tag ``base``."""
    spur_code = (
        f"import pathlib\n"
        f"def spur(name: str) -> None:\n"
        f"    with pathlib.Path({str(spur_datei)!r}).open('a', encoding='utf-8') as f:\n"
        f"        f.write(name + '\\n')\n"
        if spur_datei is not None
        else "def spur(name: str) -> None:\n    pass\n"
    )
    dateien = {
        ".gitignore": "__pycache__/\n.pytest_cache/\n",
        "pytest.ini": "[pytest]\ntestpaths = tests\npythonpath = .\n",
        "kern/__init__.py": "",
        "kern/mod_a.py": "def wert_a() -> int:\n    return 10\n",
        "dienst/__init__.py": "",
        "dienst/mod_b.py": "from kern.mod_a import wert_a\n\n\ndef wert_b() -> int:\n    return wert_a() + 5\n",
        "dienst/mod_c.py": "def wert_c() -> int:\n    return 99\n",
        "tests/spur_hilfe.py": spur_code,
        "tests/test_a.py": textwrap.dedent("""\
            from kern.mod_a import wert_a
            from spur_hilfe import spur


            def test_a_ok() -> None:
                spur("test_a_ok")
                assert wert_a() == 10


            def test_a_ausgelassen() -> None:
                spur("test_a_ausgelassen")
                assert False, "sollte per auslassungen abgewählt werden"
            """),
        "tests/test_b.py": textwrap.dedent("""\
            from dienst.mod_b import wert_b
            from spur_hilfe import spur


            def test_b_ok() -> None:
                spur("test_b_ok")
                assert wert_b() == 15
            """),
        "tests/test_c.py": textwrap.dedent("""\
            from dienst.mod_c import wert_c
            from spur_hilfe import spur


            def test_c_ok() -> None:
                spur("test_c_ok")
                assert wert_c() == 99
            """),
    }
    ordner.mkdir(parents=True, exist_ok=True)
    for rel, inhalt in dateien.items():
        _schreibe(ordner, rel, inhalt)
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "basis")
    _git(ordner, "tag", "base")
    return ordner


# --- 1. workflow_call-Vorlage & Eigenanschluss ---------------------------------------------------


def test_vorlage_yaml_enthaelt_workflow_call_und_alle_dokumentierten_eingaben() -> None:
    text = pipeline_vorlage.vorlage_yaml()
    assert pipeline_vorlage.pruefe_vorlage_text(text) == []
    assert "workflow_call:" in text
    assert "permissions:" in text
    assert "contents: read" in text
    for eingabe in pipeline_vorlage.EINGABEN:
        assert f"      {eingabe.name}:" in text
        assert eingabe.beschreibung in text
    assert "to_spawn.pruef.pipeline_vorlage stufe0" in text
    assert "to_spawn.pruef.pipeline_vorlage stufe1" in text
    assert "testmon" not in text.lower()
    # Optional mit PyYAML gegenchecken, wenn auf der Maschine vorhanden.
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return
    daten = yaml.safe_load(text)
    assert isinstance(daten, dict)
    on_block = daten.get("on") or daten.get(True)
    assert isinstance(on_block, dict) and "workflow_call" in on_block
    eingaben = on_block["workflow_call"]["inputs"]
    for eingabe in pipeline_vorlage.EINGABEN:
        assert eingabe.name in eingaben
        assert eingaben[eingabe.name]["description"]
        assert eingaben[eingabe.name]["type"] == eingabe.typ
    assert set(daten["jobs"]) == {"stufe-0", "stufe-1"}


def test_eigenanschluss_yaml_ruft_vorlage_auf_und_ist_gueltiges_yaml() -> None:
    text = pipeline_vorlage.eigenanschluss_yaml()
    assert "uses: ./.github/workflows/pipeline-stufe-0-1.yml" in text
    assert "python-version:" in text
    assert "auslassungen:" in text
    assert "testmon" not in text.lower()
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return
    daten = yaml.safe_load(text)
    assert isinstance(daten, dict)
    assert "jobs" in daten and "pipeline-stufe-0-1" in daten["jobs"]
    job = daten["jobs"]["pipeline-stufe-0-1"]
    assert job["uses"] == "./.github/workflows/pipeline-stufe-0-1.yml"


def test_pruefe_vorlage_text_meldet_luecken_und_verbotene_plugins() -> None:
    kaputt = "name: Kaputt\non:\n  push:\njobs:\n  x:\n    steps:\n      - run: pytest --testmon\n"
    befunde = pipeline_vorlage.pruefe_vorlage_text(kaputt)
    assert any("workflow_call" in b for b in befunde)
    assert any("python-version" in b for b in befunde)
    assert any("testmon" in b.lower() for b in befunde)


# --- 2. import_graph mit explizitem --ziel und cp1252-Pipe --------------------------------------


def test_import_graph_unterstuetzt_ziel_commit_und_cp1252_pipe(tmp_path: Path) -> None:
    repo = _probe_repo(tmp_path / "ziel_repo")
    _schreibe(repo, "kern/mod_a.py", "def wert_a() -> int:\n    return 10  # geändert\n")
    _git(repo, "commit", "-qam", "aendert mod_a")
    mitte = _git(repo, "rev-parse", "HEAD")

    _schreibe(repo, "dienst/mod_c.py", "def wert_c() -> int:\n    return 99  # geändert\n")
    _git(repo, "commit", "-qam", "aendert mod_c")

    wahl = import_graph.auswaehlen(repo, basis="base", ziel=mitte)
    assert wahl.tests == ("tests/test_a.py", "tests/test_b.py")

    cli = _cli(
        "to_spawn.pruef.import_graph",
        "--repo",
        str(repo),
        "--basis",
        "base",
        "--ziel",
        mitte,
        "--json",
        cwd=repo,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert cli.returncode == 0, cli.stdout + cli.stderr
    daten = json.loads(cli.stdout)
    assert daten["ziele"] == ["tests/test_a.py", "tests/test_b.py"]


# --- 3. Weg-Tests: Stufe 0 + Stufe 1 CI-Läufer --------------------------------------------------


def test_stufe1_waehlt_nur_abhaengige_tests_und_beachtet_auslassungen(tmp_path: Path) -> None:
    spur = tmp_path / "spur.txt"
    repo = _probe_repo(tmp_path / "weg_stufe1", spur_datei=spur)
    aus_datei = _schreibe(
        repo,
        "auslassungen.txt",
        "# Ausgelassene Tests\n"
        "tests/test_a.py::test_a_ausgelassen  # Absichtlich rot im Testaufbau\n"
        "tests/test_c.py::test_c_ok  # Nicht betroffene Datei stört nicht\n",
    )
    _git(repo, "add", "auslassungen.txt")
    _git(repo, "commit", "-qam", "auslassungen")
    _git(repo, "tag", "vor_aenderung")

    _schreibe(repo, "kern/mod_a.py", "def wert_a() -> int:\n    return 10  # kommentar\n")
    _git(repo, "commit", "-qam", "aendert mod_a")

    summary = tmp_path / "summary.md"
    erg = pipeline_vorlage.stufe1_lauf(
        repo,
        basis="vor_aenderung",
        ziel="HEAD",
        auslassungen=aus_datei,
        summary_pfad=summary,
    )
    assert erg.ok is True, erg.ausgabe
    assert erg.exit_code == 0
    assert erg.ausgewaehlt == ("tests/test_a.py", "tests/test_b.py")
    assert erg.ausgelassen == (("tests/test_a.py::test_a_ausgelassen", "Absichtlich rot im Testaufbau"),)
    gelaufen = spur.read_text(encoding="utf-8").splitlines()
    assert set(gelaufen) == {"test_a_ok", "test_b_ok"}
    assert "test_c_ok" not in gelaufen
    assert "test_a_ausgelassen" not in gelaufen
    assert summary.is_file()
    summary_text = summary.read_text(encoding="utf-8")
    assert "Stufe 1" in summary_text and "tests/test_a.py" in summary_text


def test_stufe1_null_betroffene_tests_ist_gruen_ohne_pytest_exit_5(tmp_path: Path) -> None:
    spur = tmp_path / "spur_leer.txt"
    repo = _probe_repo(tmp_path / "weg_leer", spur_datei=spur)
    _schreibe(repo, "README.md", "# Doku\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qam", "nur doku")

    erg = pipeline_vorlage.stufe1_lauf(repo, basis="base", ziel="HEAD")
    assert erg.ok is True
    assert erg.exit_code == 0
    assert erg.ausgewaehlt == ()
    assert not spur.exists()


def test_stufe1_pfad_filter_beschraenkt_geaenderte_dateien(tmp_path: Path) -> None:
    repo = _probe_repo(tmp_path / "weg_pfade")
    _schreibe(repo, "kern/mod_a.py", "def wert_a() -> int:\n    return 10  # a\n")
    _schreibe(repo, "dienst/mod_c.py", "def wert_c() -> int:\n    return 99  # c\n")
    _git(repo, "commit", "-qam", "aendert kern und dienst")

    erg = pipeline_vorlage.stufe1_lauf(repo, basis="base", ziel="HEAD", pfade=("dienst",))
    assert erg.ok is True, erg.ausgabe
    assert erg.ausgewaehlt == ("tests/test_c.py",)


def test_stufe0_cli_gruen_und_rot_bei_test_schwaechung_unter_cp1252(tmp_path: Path) -> None:
    repo = _probe_repo(tmp_path / "weg_stufe0")
    _schreibe(repo, "kern/mod_a.py", "def wert_a() -> int:\n    return 10  # normal\n")
    _git(repo, "commit", "-qam", "normaler diff")

    gruen = _cli(
        "to_spawn.pruef.pipeline_vorlage",
        "stufe0",
        "--repo",
        str(repo),
        "--basis",
        "base",
        "--ziel",
        "HEAD",
        "--ruff-befehl",
        "",
        "--typ-befehl",
        "",
        "--json",
        cwd=repo,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert gruen.returncode == 0, gruen.stdout + gruen.stderr
    assert json.loads(gruen.stdout)["ok"] is True

    # Test-Assert entfernen -> Stufe 0 muss rot melden.
    _schreibe(
        repo,
        "tests/test_c.py",
        "from dienst.mod_c import wert_c\n\n\ndef test_c_ok() -> None:\n    wert_c()\n",
    )
    _git(repo, "commit", "-qam", "assert entfernt")
    rot = _cli(
        "to_spawn.pruef.pipeline_vorlage",
        "stufe0",
        "--repo",
        str(repo),
        "--basis",
        "base",
        "--ziel",
        "HEAD",
        "--json",
        cwd=repo,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert rot.returncode == 1, rot.stdout + rot.stderr
    assert json.loads(rot.stdout)["ok"] is False


def test_cli_vorlage_und_pruefen_weg(tmp_path: Path) -> None:
    ziel = tmp_path / "pipeline-stufe-0-1.yml"
    schreib = _cli(
        "to_spawn.pruef.pipeline_vorlage",
        "vorlage",
        "--ausgabe",
        str(ziel),
        cwd=SKILL,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert schreib.returncode == 0, schreib.stdout + schreib.stderr
    assert ziel.is_file()

    pruef = _cli(
        "to_spawn.pruef.pipeline_vorlage",
        "vorlage",
        "--pruefen",
        str(ziel),
        cwd=SKILL,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert pruef.returncode == 0, pruef.stdout + pruef.stderr


# --- 4. Abnahme-Kriterium: kein testmon und Doku vorhanden --------------------------------------


def test_kein_testmon_in_vorlage_doku_und_produktivcode() -> None:
    for text in (pipeline_vorlage.vorlage_yaml(), pipeline_vorlage.eigenanschluss_yaml()):
        assert "testmon" not in text.lower()
    doku = SKILL / "docs" / "PIPELINE_STUFE_0_1.md"
    assert doku.is_file()
    assert "workflow_call" in doku.read_text(encoding="utf-8")
    assert "testmon" not in doku.read_text(encoding="utf-8").lower()
    treffer: list[str] = []
    for datei in sorted((SKILL / "to_spawn").rglob("*.py")):
        if "__pycache__" in datei.parts:
            continue
        for nr, zeile in enumerate(datei.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "testmon" in zeile.lower():
                treffer.append(f"{datei.relative_to(SKILL).as_posix()}:{nr}")
    assert treffer == [], f"testmon im Produktivcode gefunden: {treffer}"


@pytest.mark.parametrize("modus", ["vorlage", "eigenanschluss"])
def test_cli_vorlage_gibt_gueltigen_text_aus(modus: str) -> None:
    args = ["vorlage"] if modus == "vorlage" else ["vorlage", "--eigenanschluss"]
    lauf = _cli("to_spawn.pruef.pipeline_vorlage", *args, cwd=SKILL)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "name:" in lauf.stdout
    assert "testmon" not in lauf.stdout.lower()
