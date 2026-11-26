"""Bereitschafts-Prüfung („doctor“) auf der Inventur (#583, E31).

Prüft die fünf neuen Bereitschafts-Prüfpunkte direkt in ``to_spawn/inventur.py``
(Muster ``Unterbau`` / ``UNTERBAUTEN``, keine zweite Prüf-Datei daneben) und liest
die Repo-Art aus dem Repo-Profil (#425):

1. kein testmon
2. Test-Schutz in der CI aktiv
3. Anschluss an die Pipeline-Vorlage Stufe 0–2
4. Testauswahl per Import-Graph
5. Markierung „braucht Server“ vorhanden

Abweichungen melden Grund + Abhilfe; risikoarme Punkte regelt ``--regeln`` selbst
(z. B. Vorlage anschließen, Marker eintragen), riskante Punkte gehen über den
bestehenden T0-Weg (``to_spawn.einrichtungs_ticket``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from conftest import attrappe

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"

sys.path.insert(0, str(SKILL))

from to_spawn import einrichtungs_ticket, inventur


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _uebungs_repo(
    tmp_path: Path,
    *,
    name: str = "uebung-repo",
    mit_staging: bool = False,
    sauber: bool = True,
) -> Path:
    """Legt ein kleines echtes Git-Übungs-Repo mit Repo-Profil (#425) an."""
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")

    konfig: dict[str, Any] = {
        "pruefung": {
            "schnell": "python -m to_spawn.pruef.import_graph --repo . --basis main",
            "komplett": "pytest -q tests",
        },
        "live": {"ohne": True},
        "staging": {"url": "http://127.0.0.1:18502/", "ohne": False} if mit_staging else {"url": "", "ohne": True},
        "staging_start": "python skripte/ausrollen.py staging" if mit_staging else "",
    }
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps(konfig, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (repo / "app.py").write_text("def wert() -> int:\n    return 1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("def test_ok() -> None:\n    assert True\n", encoding="utf-8")

    if sauber:
        (repo / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
            'markers = [\n    "braucht_server: Test benötigt den Bau-Server",\n]\n',
            encoding="utf-8",
        )
        wf = repo / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "pipeline-stufe-0-1.yml").write_text(
            "name: Pipeline Stufe 0+1\n"
            "on:\n  pull_request:\n"
            "permissions:\n  contents: read\n"
            "jobs:\n"
            "  stufe-0-1:\n"
            "    uses: Shavy72/claude-skill-to-spawn/.github/workflows/pipeline-stufe-0-1.yml@main\n",
            encoding="utf-8",
        )
        staging_bool = "true" if mit_staging else "false"
        (wf / "pipeline-stufe-2.yml").write_text(
            "name: Pipeline Stufe 2\n"
            "on:\n  workflow_dispatch:\n"
            "permissions:\n  contents: read\n"
            "jobs:\n"
            "  stufe-2:\n"
            "    uses: Shavy72/claude-skill-to-spawn/.github/workflows/pipeline-stufe-2.yml@main\n"
            "    with:\n"
            f"      mit-staging-app: {staging_bool}\n",
            encoding="utf-8",
        )
    else:
        (repo / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            encoding="utf-8",
        )

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: Startstand")
    return repo


def _heim(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    return home


def _cli(
    repo: Path, home: Path, *args: str, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["TO_SPAWN_REPO"] = str(repo)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CLI), "inventur", *args],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def _gh_attrappe(tmp_path: Path, *, offene_tickets: list[dict[str, Any]] | None = None) -> tuple[Path, Path]:
    """Erstellt eine echte ``gh``-Attrappe, die Aufrufe in eine Welt-JSON protokolliert."""
    bin_dir = tmp_path / "gh-bin"
    bin_dir.mkdir(exist_ok=True)
    welt = tmp_path / "gh-welt.json"
    welt.write_text(
        json.dumps({"offen": offene_tickets or [], "angelegt": [], "labels": []}),
        encoding="utf-8",
    )
    code = f"""
import json, sys
from pathlib import Path

welt_pfad = Path({str(welt)!r})
welt = json.loads(welt_pfad.read_text(encoding="utf-8"))
args = sys.argv[1:]
if args[:2] == ["issue", "list"]:
    print(json.dumps(welt["offen"]))
    raise SystemExit(0)
if args[:1] == ["api"] and "/labels/" in " ".join(args):
    raise SystemExit(1)
if args[:2] == ["label", "create"]:
    welt["labels"].append(args[2] if len(args) > 2 else "")
    welt_pfad.write_text(json.dumps(welt), encoding="utf-8")
    raise SystemExit(0)
if args[:2] == ["issue", "create"]:
    titel = args[args.index("--title") + 1] if "--title" in args else ""
    body = args[args.index("--body") + 1] if "--body" in args else ""
    label = args[args.index("--label") + 1] if "--label" in args else ""
    nr = 700 + len(welt["angelegt"]) + 1
    welt["angelegt"].append({{"number": nr, "title": titel, "body": body, "label": label}})
    welt_pfad.write_text(json.dumps(welt), encoding="utf-8")
    print(f"https://github.com/beispiel/repo/issues/{{nr}}")
    raise SystemExit(0)
raise SystemExit(0)
"""
    attrappe(bin_dir, "gh", code)
    return bin_dir, welt


# --- 1. Struktur: 5 Prüfpunkte nach Muster Unterbau/UNTERBAUTEN ----------------------------


def test_fuenf_bereitschafts_pruefpunkte_in_inventur() -> None:
    punkte = inventur.BEREITSCHAFTS_PUNKTE
    assert len(punkte) == 5
    namen = [p.name for p in punkte]
    assert namen == [
        "kein_testmon",
        "test_schutz_ci",
        "pipeline_vorlage",
        "import_graph",
        "braucht_server_marker",
    ]
    # Risikoarme Punkte haben einen Anleger/Regler, riskante nicht.
    risikoarm = {p.name for p in punkte if p.risikoarm}
    assert "pipeline_vorlage" in risikoarm
    assert "braucht_server_marker" in risikoarm
    assert "kein_testmon" not in risikoarm
    assert "test_schutz_ci" not in risikoarm


def test_repo_art_kommt_aus_425_profil(tmp_path: Path) -> None:
    home = _heim(tmp_path)
    repo_mit = _uebungs_repo(tmp_path, name="mit-app", mit_staging=True, sauber=True)
    repo_ohne = _uebungs_repo(tmp_path, name="ohne-app", mit_staging=False, sauber=True)

    inv_mit = inventur.erstelle(
        repo_mit, claude_home=home / ".claude", home_json=home / ".claude.json", home=home, environ={"PATH": ""}
    )
    inv_ohne = inventur.erstelle(
        repo_ohne, claude_home=home / ".claude", home_json=home / ".claude.json", home=home, environ={"PATH": ""}
    )

    assert inv_mit.projekt["repo_profil"]["repo_art"] == "mit-staging"
    assert inv_mit.projekt["repo_profil"]["mit_staging"] is True
    assert inv_ohne.projekt["repo_profil"]["repo_art"] == "ohne-staging"
    assert inv_ohne.projekt["repo_profil"]["mit_staging"] is False


# --- 2. Weg-Test: sauberes Übungs-Repo -> grün --------------------------------------------


@pytest.mark.parametrize("mit_staging", [False, True])
def test_weg_sauberes_uebungs_repo_gruen(tmp_path: Path, mit_staging: bool) -> None:
    home = _heim(tmp_path)
    repo = _uebungs_repo(tmp_path, name=f"sauber-{mit_staging}", mit_staging=mit_staging, sauber=True)

    lauf = _cli(repo, home, "--doctor", "--json")
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    daten = json.loads(lauf.stdout)
    bereitschaft = daten["bereitschaft"]
    assert len(bereitschaft) == 5
    assert all(b["ok"] is True for b in bereitschaft), bereitschaft


# --- 3. Weg-Test: Übungs-Repo mit Altlast (testmon an, kein Test-Schutz) ------------------


def test_weg_uebungs_repo_mit_altlast_meldet_beide_abweichungen(tmp_path: Path) -> None:
    home = _heim(tmp_path)
    repo = _uebungs_repo(tmp_path, name="altlast-repo", mit_staging=False, sauber=False)
    # Altlast 1: testmon in pyproject.toml + Workflow aktiv
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "--testmon"\ntestpaths = ["tests"]\n',
        encoding="utf-8",
    )
    wf = repo / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "name: Alt-CI\non:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: pytest --testmon\n",
        encoding="utf-8",
    )
    # Altlast 2: Test-Schutz ausdrücklich abgeschaltet
    konfig_datei = repo / ".to-spawn" / "config.json"
    konfig = json.loads(konfig_datei.read_text(encoding="utf-8"))
    konfig["pruefung"]["test_schutz"] = False
    konfig_datei.write_text(json.dumps(konfig, indent=2) + "\n", encoding="utf-8")

    lauf = _cli(repo, home, "--doctor", "--json")
    assert lauf.returncode == inventur.EXIT_ABWEICHUNG, lauf.stdout + lauf.stderr
    daten = json.loads(lauf.stdout)
    je_name = {b["name"]: b for b in daten["bereitschaft"]}

    assert je_name["kein_testmon"]["ok"] is False
    assert "pyproject.toml" in je_name["kein_testmon"]["fehlgrund"]
    assert je_name["kein_testmon"]["abhilfe"]

    assert je_name["test_schutz_ci"]["ok"] is False
    assert je_name["test_schutz_ci"]["fehlgrund"]
    assert je_name["test_schutz_ci"]["abhilfe"]


# --- 4. Selbst-Regeln nur für risikoarme Punkte, sonst Einrichtungs-Ticket ----------------


def test_selbst_regeln_nur_risikoarme_punkte_sonst_einrichtungs_ticket(tmp_path: Path) -> None:
    home = _heim(tmp_path)
    repo = _uebungs_repo(tmp_path, name="regeln-repo", mit_staging=True, sauber=False)
    # GitHub-Origin setzen, damit der T0-Weg greifen kann
    _git(repo, "remote", "add", "origin", "https://github.com/beispiel/repo.git")
    # Altlast: testmon in requirements-dev.txt (riskant -> darf NICHT selbst gelöscht werden)
    (repo / "requirements-dev.txt").write_text("pytest\npytest-testmon==2.1.0\n", encoding="utf-8")
    _git(repo, "add", "requirements-dev.txt")
    _git(repo, "commit", "-m", "chore: Altlast")

    gh_bin, welt_pfad = _gh_attrappe(tmp_path)
    pfad_env = f"{gh_bin}{os.pathsep}{os.environ.get('PATH', '')}"

    lauf = _cli(repo, home, "--doctor", "--regeln", "--mit-ticket", "--json", env_extra={"PATH": pfad_env})
    # Nach Selbst-Regeln bleibt kein_testmon rot -> Exit 3
    assert lauf.returncode == inventur.EXIT_ABWEICHUNG, lauf.stdout + lauf.stderr
    daten = json.loads(lauf.stdout)
    je_name = {b["name"]: b for b in daten["bereitschaft"]}

    # Risikoarme Punkte wurden selbst geregelt:
    # 1) Pipeline-Vorlage Stufe 0-2 angeschlossen (mit Staging-App aus #425-Profil!)
    assert je_name["pipeline_vorlage"]["ok"] is True
    assert je_name["pipeline_vorlage"]["status"] == "geregelt"
    wf_01 = repo / ".github" / "workflows" / "pipeline-stufe-0-1.yml"
    wf_2 = repo / ".github" / "workflows" / "pipeline-stufe-2.yml"
    assert wf_01.is_file()
    assert wf_2.is_file()
    assert "mit-staging-app: true" in wf_2.read_text(encoding="utf-8")

    # 2) braucht_server-Markierung eingetragen
    assert je_name["braucht_server_marker"]["ok"] is True
    assert je_name["braucht_server_marker"]["status"] == "geregelt"
    assert "braucht_server" in (repo / "pyproject.toml").read_text(encoding="utf-8")

    # 3) Durch den Vorlagen-Anschluss sind Test-Schutz und Import-Graph jetzt ebenfalls grün!
    assert je_name["test_schutz_ci"]["ok"] is True
    assert je_name["import_graph"]["ok"] is True

    # 4) Riskanter Punkt (kein_testmon) wurde NICHT still umgeschrieben, sondern bekam ein T0-Ticket
    assert "pytest-testmon" in (repo / "requirements-dev.txt").read_text(encoding="utf-8")
    assert je_name["kein_testmon"]["ok"] is False
    assert je_name["kein_testmon"]["status"] == "ticket"
    assert je_name["kein_testmon"]["aktion"] == "#701"

    welt = json.loads(welt_pfad.read_text(encoding="utf-8"))
    assert len(welt["angelegt"]) == 1
    assert "kein_testmon" in welt["angelegt"][0]["title"]
    assert einrichtungs_ticket.LABEL in welt["labels"]


def test_trocken_schreibt_nichts_und_legt_kein_ticket_an(tmp_path: Path) -> None:
    home = _heim(tmp_path)
    repo = _uebungs_repo(tmp_path, name="trocken-repo", mit_staging=False, sauber=False)
    _git(repo, "remote", "add", "origin", "https://github.com/beispiel/repo.git")
    vorher_pyproject = (repo / "pyproject.toml").read_text(encoding="utf-8")

    gh_bin, welt_pfad = _gh_attrappe(tmp_path)
    pfad_env = f"{gh_bin}{os.pathsep}{os.environ.get('PATH', '')}"

    lauf = _cli(repo, home, "--doctor", "--regeln", "--mit-ticket", "--trocken", env_extra={"PATH": pfad_env})
    assert lauf.returncode == inventur.EXIT_ABWEICHUNG
    assert not (repo / ".github" / "workflows").exists()
    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == vorher_pyproject
    welt = json.loads(welt_pfad.read_text(encoding="utf-8"))
    assert welt["angelegt"] == []


def test_einrichtungs_ticket_wiederverwendet_offenes_ticket(tmp_path: Path) -> None:
    home = _heim(tmp_path)
    repo = _uebungs_repo(tmp_path, name="reuse-repo", mit_staging=False, sauber=True)
    _git(repo, "remote", "add", "origin", "https://github.com/beispiel/repo.git")
    (repo / "requirements-dev.txt").write_text("pytest-testmon\n", encoding="utf-8")

    gh_bin, welt_pfad = _gh_attrappe(
        tmp_path,
        offene_tickets=[{"number": 42, "title": "Einrichtung: bereits offen"}],
    )
    pfad_env = f"{gh_bin}{os.pathsep}{os.environ.get('PATH', '')}"

    lauf = _cli(repo, home, "--doctor", "--regeln", "--mit-ticket", "--json", env_extra={"PATH": pfad_env})
    assert lauf.returncode == inventur.EXIT_ABWEICHUNG
    daten = json.loads(lauf.stdout)
    je_name = {b["name"]: b for b in daten["bereitschaft"]}
    assert je_name["kein_testmon"]["status"] == "ticket"
    assert je_name["kein_testmon"]["aktion"] == "#42"
    welt = json.loads(welt_pfad.read_text(encoding="utf-8"))
    assert welt["angelegt"] == []
