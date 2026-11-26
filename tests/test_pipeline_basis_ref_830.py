"""#830: Basis-Ref-Wahl bei PR-Merge-Commits (Stufe 0/1/3, Linux + Windows).

Stellt den Befund aus Issue #830 (Folge #800) in einem echten Git-Repo nach:
- Basis-Commit A auf ``main``.
- ``main`` geht weiter auf Commit B und ändert dort eine Testgrenze in einer fremden Datei.
- PR-Zweig von A ändert nur eine eigene Datei.
- GitHub-Merge-Commit ``M = merge(B, PR)`` hat 2 Eltern (``M^1 == B``, ``M^2 == PR``).
- Mit veraltetem ``PR_BASIS = A`` meldet Stufe 0 fälschlich ``[grenzwert_geaendert]`` (Rot-Beweis).
- Mit ``bestimme_basis_ref`` (``HEAD^1`` bei ``pull_request`` + Merge-Commit) meldet Stufe 0
  0 Verstöße für die fremde Datei und Stufe 1 wählt nur die tatsächlich betroffenen Tests.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import prozessbaum
from to_spawn.pruef import aufraeumen, pipeline_vorlage

_NULL_SHA = "0" * 40


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
    ziel.write_text(inhalt, encoding="utf-8", newline="\n")
    return ziel


def _cli(
    *args: str, cwd: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    umg = {**os.environ, "PYTHONPATH": str(SKILL)}
    if extra_env:
        umg.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "to_spawn.pruef.pipeline_vorlage", *args],
        cwd=cwd,
        env=umg,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **prozessbaum.fensterlos(),
    )


def _erstelle_pr_merge_szenario(ordner: Path) -> dict[str, str]:
    """Baut Commit A -> main: B (ändert Testgrenze), PR von A -> P, Merge M = merge(B, P)."""
    ordner.mkdir(parents=True, exist_ok=True)
    _git(ordner, "init", "-q", "-b", "main")
    _schreibe(ordner, ".gitignore", "__pycache__/\n.pytest_cache/\n")
    _schreibe(ordner, "pytest.ini", "[pytest]\ntestpaths = tests\npythonpath = .\n")
    _schreibe(ordner, "app/__init__.py", "")
    _schreibe(ordner, "app/fremd.py", "def grenze() -> int:\n    return 20\n")
    _schreibe(ordner, "app/eigen.py", "def wert() -> int:\n    return 1\n")
    _schreibe(
        ordner,
        "tests/test_fremd.py",
        textwrap.dedent("""\
            from app.fremd import grenze


            def test_fremde_grenze() -> None:
                assert grenze() >= 10
            """),
    )
    _schreibe(
        ordner,
        "tests/test_eigen.py",
        textwrap.dedent("""\
            from app.eigen import wert


            def test_eigener_wert() -> None:
                assert wert() == 1
            """),
    )
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "A: Basis-Stand")
    sha_a = _git(ordner, "rev-parse", "HEAD")

    # PR-Zweig von Commit A abzweigen und nur eine eigene Datei ändern.
    _git(ordner, "checkout", "-q", "-b", "pr-zweig", sha_a)
    _schreibe(ordner, "app/eigen.py", "def wert() -> int:\n    return 1  # PR-Änderung\n")
    _git(ordner, "commit", "-qam", "P: PR ändert nur app/eigen.py")
    sha_p = _git(ordner, "rev-parse", "HEAD")

    # Auf main landet zwischenzeitlich Commit B, der in tests/test_fremd.py eine Grenze ändert.
    _git(ordner, "checkout", "-q", "main")
    _schreibe(
        ordner,
        "tests/test_fremd.py",
        textwrap.dedent("""\
            from app.fremd import grenze


            def test_fremde_grenze() -> None:
                assert grenze() >= 5
            """),
    )
    _git(ordner, "commit", "-qam", "B: main ändert fremde Testgrenze")
    sha_b = _git(ordner, "rev-parse", "HEAD")

    # GitHub-Actions-Merge-Commit M = merge(B, P): erster Elternteil B, zweiter Elternteil P.
    _git(ordner, "merge", "--no-ff", "-q", "pr-zweig", "-m", "Merge PR auf aktuelles main")
    sha_m = _git(ordner, "rev-parse", "HEAD")

    assert _git(ordner, "rev-parse", "HEAD^1") == sha_b
    assert _git(ordner, "rev-parse", "HEAD^2") == sha_p
    return {"A": sha_a, "B": sha_b, "P": sha_p, "M": sha_m}


# --- 1. Rot-Beweis und Korrektur für PR-Merge-Commit (#830) ------------------------------------


def test_pr_merge_commit_vermeidet_falschen_test_schutz_verstoss_auf_fremder_datei(tmp_path: Path) -> None:
    repo = tmp_path / "pr_merge_repo"
    shas = _erstelle_pr_merge_szenario(repo)

    # Gegenprobe (Rot-Beweis): Mit veraltetem PR_BASIS = A meldet Stufe 0 einen falschen Verstoß
    # für die fremde Datei tests/test_fremd.py, die gar nicht Teil des PR ist.
    erg_veraltet = pipeline_vorlage.stufe0_lauf(repo, basis=shas["A"], ziel="HEAD")
    assert erg_veraltet.ok is False
    assert erg_veraltet.exit_code == 1
    assert erg_veraltet.test_schutz is not None
    assert any(
        v.datei == "tests/test_fremd.py" and v.art == "grenzwert_geaendert"
        for v in erg_veraltet.test_schutz.verstoesse
    )

    # Neue Basis-Wahl für pull_request + Merge-Commit liefert HEAD^1 (= Commit B).
    basis = pipeline_vorlage.bestimme_basis_ref(
        repo,
        ereignis="pull_request",
        pr_basis=shas["A"],
    )
    assert basis == "HEAD^1"

    # Stufe 0 mit ermittelter Basis HEAD^1 meldet 0 Verstöße für tests/test_fremd.py.
    erg_neu = pipeline_vorlage.stufe0_lauf(repo, basis=basis, ziel="HEAD")
    assert erg_neu.ok is True, erg_neu.bericht()
    assert erg_neu.exit_code == 0
    assert erg_neu.test_schutz is not None
    assert erg_neu.test_schutz.verstoesse == ()

    # Stufe 1 wählt nur die vom PR betroffene Testdatei (nicht tests/test_fremd.py).
    erg_stufe1 = pipeline_vorlage.stufe1_lauf(repo, basis=basis, ziel="HEAD")
    assert erg_stufe1.ok is True, erg_stufe1.ausgabe
    assert erg_stufe1.geaendert == ("app/eigen.py",)
    assert erg_stufe1.ausgewaehlt == ("tests/test_eigen.py",)


def test_basis_ref_cli_schreibt_github_env_auch_unter_cp1252(tmp_path: Path) -> None:
    repo = tmp_path / "cli_repo"
    shas = _erstelle_pr_merge_szenario(repo)
    env_datei = tmp_path / "github_env.txt"

    lauf = _cli(
        "basis-ref",
        "--repo",
        str(repo),
        "--ereignis",
        "pull_request",
        "--pr-basis",
        shas["A"],
        "--github-env",
        str(env_datei),
        cwd=repo,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert lauf.stdout.strip() == "HEAD^1"
    assert env_datei.read_text(encoding="utf-8") == "BASIS_REF=HEAD^1\n"


# --- 2. Bisherige Reihenfolge für alle anderen Fälle -------------------------------------------


def test_basis_ref_reihenfolge_fuer_eingabe_non_merge_push_und_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "reihenfolge_repo"
    shas = _erstelle_pr_merge_szenario(repo)

    # 1. Explizite Eingabe (inputs.basis-ref) hat immer Vorrang — auch bei PR-Merge-Commit.
    assert (
        pipeline_vorlage.bestimme_basis_ref(
            repo,
            eingabe_basis="origin/main",
            ereignis="pull_request",
            pr_basis=shas["A"],
            push_vorher=shas["B"],
        )
        == "origin/main"
    )

    # 2. Bei push mit Merge-Commit als HEAD bleibt PUSH_VORHER (nicht HEAD^1) vorrangig.
    assert (
        pipeline_vorlage.bestimme_basis_ref(
            repo,
            ereignis="push",
            push_vorher=shas["A"],
        )
        == shas["A"]
    )

    # 3. Bei push mit Null-SHA fällt die Wahl auf HEAD~1 zurück.
    assert (
        pipeline_vorlage.bestimme_basis_ref(
            repo,
            ereignis="push",
            push_vorher=_NULL_SHA,
        )
        == "HEAD~1"
    )

    # 4. Bei workflow_dispatch / schedule ohne PR/Push-SHAs fällt die Wahl auf HEAD~1 zurück.
    assert pipeline_vorlage.bestimme_basis_ref(repo, ereignis="schedule") == "HEAD~1"
    assert pipeline_vorlage.bestimme_basis_ref(repo, ereignis="workflow_dispatch") == "HEAD~1"

    # 5. Bei pull_request OHNE Merge-Commit (nur 1 Elternteil) bleibt PR_BASIS erhalten.
    _git(repo, "checkout", "-q", shas["P"])
    assert (
        pipeline_vorlage.bestimme_basis_ref(
            repo,
            ereignis="pull_request",
            pr_basis=shas["A"],
        )
        == shas["A"]
    )

    # 6. Wurzel-Commit ohne Vorgänger fällt auf HEAD zurück.
    wurzel_repo = tmp_path / "wurzel_repo"
    wurzel_repo.mkdir(parents=True, exist_ok=True)
    _git(wurzel_repo, "init", "-q", "-b", "main")
    _schreibe(wurzel_repo, "README.md", "# Neu\n")
    _git(wurzel_repo, "add", "README.md")
    _git(wurzel_repo, "commit", "-qm", "initial")
    assert pipeline_vorlage.bestimme_basis_ref(wurzel_repo, ereignis="workflow_dispatch") == "HEAD"


# --- 3. Workflows nutzen zentralen Basis-Ref-Helfer & erfüllen Sicherheitsregeln ----------------


def test_betroffene_workflows_nutzen_basis_ref_helfer_und_haben_nur_leserechte() -> None:
    wf_ordner = SKILL / ".github" / "workflows"

    # Stufe 0+1: beide Jobs (stufe-0 und stufe-1) rufen den zentralen Helfer auf.
    stufe01_pfad = wf_ordner / "pipeline-stufe-0-1.yml"
    stufe01_text = stufe01_pfad.read_text(encoding="utf-8")
    assert stufe01_text == pipeline_vorlage.vorlage_yaml()
    assert stufe01_text.count("to_spawn.pruef.pipeline_vorlage basis-ref") == 2
    assert "github.event_name" in stufe01_text
    assert "pull_request_target" not in stufe01_text
    assert "secrets." not in stufe01_text
    assert re.search(r"(?m)^permissions:\s*\n  contents: read\s*$", stufe01_text)

    # Stufe 3: diff-mutation ruft denselben Helfer auf, ebenfalls nur contents: read.
    stufe3_pfad = wf_ordner / "pipeline-stufe-3.yml"
    stufe3_text = stufe3_pfad.read_text(encoding="utf-8")
    assert stufe3_text == aufraeumen.vorlage_stufe3_yaml()
    assert stufe3_text.count("to_spawn.pruef.pipeline_vorlage basis-ref") == 1
    assert "github.event_name" in stufe3_text
    assert "pull_request_target" not in stufe3_text
    assert "secrets." not in stufe3_text
    assert re.search(r"(?m)^permissions:\s*\n  contents: read\s*$", stufe3_text)

    # Übrige Pipeline-Workflows und Eigenanschlüsse enthalten weder pull_request_target
    # noch den alten veralteten PR_BASIS-Inline-Block.
    for name in (
        "pipeline-stufe-0-1-eigen.yml",
        "pipeline-stufe-2.yml",
        "pipeline-stufe-2-eigen.yml",
        "pipeline-stufe-3-eigen.yml",
        "pipeline-nachtrunde.yml",
        "pipeline-nachtrunde-eigen.yml",
    ):
        inhalt = (wf_ordner / name).read_text(encoding="utf-8")
        assert "pull_request_target" not in inhalt, f"pull_request_target in {name}"
        assert "github.event.pull_request.base.sha" not in inhalt, f"unerwarteter PR_BASIS-Block in {name}"
