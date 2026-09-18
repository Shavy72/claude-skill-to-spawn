"""Umzug #205: Repo-Skripte leben im Skill (``skripte/``), das Repo trägt nur
Weiterleitungen und ``.to-spawn/config.json``.

Echt laufen: Konfig-Anlage, CLI, Skill-Skripte, Git. Gestellt ist nur das
``claude``-Programm (schreibt seine Umgebung in eine Datei).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
SKRIPTE = SKILL / "skripte"

sys.path.insert(0, str(SKILL))

from to_spawn import config  # noqa: E402

FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ["FAKE_UMGEBUNG"]).write_text(
    json.dumps({"TO_SPAWN_REPO": os.environ.get("TO_SPAWN_REPO"), "argv": sys.argv[1:3]}),
    encoding="utf-8",
)
sys.exit(0)
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    _git(arbeit, "init")
    manifeste = arbeit / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    (manifeste / "spec-900.json").write_text(
        json.dumps(
            {
                "spec": 900,
                "tickets": {
                    "901": {"title": "Wegwerf", "schaetzung_k": 120, "umfang": "Kern bauen."}
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    shutil.copy2(SKILL / "repo-scripts" / "_default.json", manifeste / "_default.json")
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.chdir(arbeit)
    return arbeit


def _lade_skript(name: str, modulname: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(modulname, SKRIPTE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[modulname] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(modulname, None)
    return modul


# --- 1. config.sicherstellen ------------------------------------------------


def test_sicherstellen_legt_konfig_mit_allen_feldern_an(repo: Path) -> None:
    datei = config.sicherstellen(repo)
    assert datei == repo / ".to-spawn" / "config.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    for feld in (
        "terminal",
        "ziel_default",
        "ssh_ziel",
        "runner",
        "staging_start",
        "deploy_befehl",
    ):
        assert feld in daten, feld
    for rolle in ("ticket", "ticket_leicht", "waechter"):
        assert rolle in daten["modelle"], rolle
        assert rolle in daten["effort"], rolle
    assert "ziel" in daten["mail"]
    assert config.lade(repo) == config._mische(config.DEFAULTS, daten)
    assert config.lade(repo)["staging_start"] == ""
    assert config.lade(repo)["deploy_befehl"] == ""


def test_sicherstellen_ueberschreibt_nie(repo: Path) -> None:
    datei = repo / ".to-spawn" / "config.json"
    datei.parent.mkdir()
    datei.write_text('{"deploy_befehl": "make deploy"}', encoding="utf-8")
    assert config.sicherstellen(repo) == datei
    assert datei.read_text(encoding="utf-8") == '{"deploy_befehl": "make deploy"}'
    assert config.lade(repo)["deploy_befehl"] == "make deploy"


def test_sicherstellen_ohne_argument_nimmt_git_wurzel(repo: Path) -> None:
    unter = repo / "docs"
    os.chdir(unter)
    assert config.sicherstellen() == repo / ".to-spawn" / "config.json"


# --- 2. CLI legt die Konfig beim ersten Aufruf an ---------------------------


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def test_pruefen_legt_konfig_an(repo: Path) -> None:
    ergebnis = _cli(repo, "pruefen", "900", "--ohne-github")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert (repo / ".to-spawn" / "config.json").is_file()


def test_pruefen_probelauf_legt_nichts_an(repo: Path) -> None:
    _cli(repo, "pruefen", "900", "--ohne-github", "--dry-run")
    assert not (repo / ".to-spawn" / "config.json").exists()


# --- 3. Alias-Skills ----------------------------------------------------------


def _frontmatter(datei: Path) -> dict[str, str]:
    text = datei.read_text(encoding="utf-8")
    assert text.startswith("---\n"), datei
    kopf = text.split("---\n", 2)[1]
    felder: dict[str, str] = {}
    for zeile in kopf.splitlines():
        if ":" in zeile:
            schluessel, wert = zeile.split(":", 1)
            felder[schluessel.strip()] = wert.strip()
    return felder


@pytest.mark.parametrize(
    ("name", "verweis"),
    [
        ("meta-exec", "/to-spawn"),
        ("to-spawn-local", "to_spawn.py spawn <S> --ziel local"),
        ("to-spawn-remote", "to_spawn.py spawn <S> --ziel srv"),
    ],
)
def test_alias_skill(name: str, verweis: str) -> None:
    datei = SKILL / "aliase" / name / "SKILL.md"
    assert datei.is_file(), datei
    felder = _frontmatter(datei)
    assert felder["name"] == name
    assert felder.get("description")
    text = datei.read_text(encoding="utf-8")
    assert "to-spawn" in text.split("---\n", 2)[2]
    assert verweis in text


# --- 4. Skill-Skripte: Repo aus Umgebung oder Git-Wurzel, nie aus Dateiort ----


@pytest.mark.parametrize("name", ["bau", "sessions_stand"])
def test_repo_aus_umgebung(name: str, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    anderes = repo.parent / "anderes"
    anderes.mkdir()
    monkeypatch.setenv("TO_SPAWN_REPO", str(anderes))
    modul = _lade_skript(name, f"_umzug_test_{name}_env")
    assert Path(modul.REPO).resolve() == anderes.resolve()


@pytest.mark.parametrize("name", ["bau", "sessions_stand"])
def test_repo_aus_git_wurzel(name: str, repo: Path) -> None:
    os.chdir(repo / "docs" / "agents")
    modul = _lade_skript(name, f"_umzug_test_{name}_git")
    assert Path(modul.REPO).resolve() == repo.resolve()
    assert SKILL.resolve() not in Path(modul.REPO).resolve().parents


def test_spec_stand_repo_dir_vorgabe(repo: Path) -> None:
    modul = _lade_skript("spec_stand", "_umzug_test_spec_stand")
    assert Path(modul.REPO_ORDNER).resolve() == repo.resolve()


def test_bau_legt_konfig_beim_start_an(repo: Path) -> None:
    """Direkt startbar: ``python skripte/bau.py <N>`` im fremden Repo."""
    ergebnis = subprocess.run(
        [sys.executable, str(SKRIPTE / "bau.py"), "901", "--print-prompt"],
        cwd=str(repo),
        env={k: v for k, v in os.environ.items() if k != "TO_SPAWN_REPO"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert "901" in ergebnis.stdout
    assert (repo / ".to-spawn" / "config.json").is_file()


@pytest.mark.parametrize(("name", "nummer"), [("bau", "901"), ("wache", "900")])
def test_kind_session_erbt_kein_to_spawn_repo(
    name: str, nummer: str, repo: Path, tmp_path: Path
) -> None:
    binaer = tmp_path / "bin"
    binaer.mkdir()
    claude = binaer / "claude"
    claude.write_text(FAKE_CLAUDE, encoding="utf-8")
    claude.chmod(0o755)
    beweis = tmp_path / "umgebung.json"
    temp = tmp_path / "tmp"
    temp.mkdir()
    umgebung = {
        **os.environ,
        "PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}",
        "TO_SPAWN_REPO": str(repo),
        "FAKE_UMGEBUNG": str(beweis),
        "TMPDIR": str(temp),
    }
    umgebung.pop("LOCALAPPDATA", None)
    befehl = [sys.executable, str(SKRIPTE / f"{name}.py"), nummer]
    if name == "bau":
        befehl.append("--sofort")
    ergebnis = subprocess.run(
        befehl,
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    daten = json.loads(beweis.read_text(encoding="utf-8"))
    assert daten["TO_SPAWN_REPO"] is None
    assert (repo / ".to-spawn" / "config.json").is_file()


def test_spawn_srv_hilfe_und_repo_aus_umgebung(repo: Path) -> None:
    ergebnis = subprocess.run(
        ["bash", str(SKRIPTE / "spawn_srv.sh"), "--help"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    text = (SKRIPTE / "spawn_srv.sh").read_text(encoding="utf-8")
    assert 'REPO="${TO_SPAWN_REPO:-' in text
    assert "$HOME/.claude/skills/to-spawn" not in text


def test_repo_scripts_sind_nur_weiterleitungen() -> None:
    for name in ("bau", "wache", "sessions_stand", "spec_stand"):
        text = (SKILL / "repo-scripts" / f"{name}.py").read_text(encoding="utf-8")
        assert len(text.splitlines()) < 45, name
        assert "argparse" not in text, name
    text = (SKILL / "repo-scripts" / "spawn_srv.sh").read_text(encoding="utf-8")
    assert 'exec bash "$SKILL/skripte/spawn_srv.sh"' in text


def test_install_sh_vorhanden_und_syntaktisch_sauber() -> None:
    skript = SKILL / "install.sh"
    assert skript.is_file()
    ergebnis = subprocess.run(
        ["bash", "-n", str(skript)], capture_output=True, text=True, check=False
    )
    assert ergebnis.returncode == 0, ergebnis.stderr


# --- Fixrunde Prüfpanel (#205) ------------------------------------------------


def test_sessions_stand_erkennt_direktstart_aus_dem_skill(repo: Path) -> None:
    """Direkt gestartete Launcher (``skripte/bau.py``) müssen in ``sessions`` auftauchen,
    sonst startet ein zweiter Spawn ein Duplikat auf demselben Worktree (#187)."""
    modul = _lade_skript("sessions_stand", "_umzug205_sessions_direkt")
    for zeile, erwartet in (
        ("python3 /home/bau/.claude/skills/to-spawn/skripte/bau.py 205", ("bau", "205")),
        ("python3 scripts/wache.py 202", ("wache", "202")),
    ):
        treffer = modul.MUSTER.search(zeile)
        assert treffer is not None, zeile
        assert treffer.groups() == erwartet


def _konfig_mit_modellen(repo: Path) -> None:
    ordner = repo / ".to-spawn"
    ordner.mkdir(exist_ok=True)
    (ordner / "config.json").write_text(
        json.dumps({"modelle": {"ticket": "claude-probe-ticket", "waechter": "claude-probe-waechter"}}),
        encoding="utf-8",
    )


def _probelauf(repo: Path, name: str, nummer: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKRIPTE / f"{name}.py"), nummer, "--dry-run"],
        cwd=str(repo),
        env={k: v for k, v in os.environ.items() if k != "TO_SPAWN_REPO"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize(
    ("name", "nummer", "modell"),
    [("bau", "901", "claude-probe-ticket"), ("wache", "900", "claude-probe-waechter")],
)
def test_modell_kommt_aus_der_repo_konfig(repo: Path, name: str, nummer: str, modell: str) -> None:
    """Ein Modell in ``.to-spawn/config.json`` muss wirken — sonst ist das Feld nur Deko."""
    _konfig_mit_modellen(repo)
    ergebnis = _probelauf(repo, name, nummer)
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    assert f"--model {modell}" in ausgabe, ausgabe


@pytest.mark.parametrize(("name", "nummer"), [("bau", "901"), ("wache", "900")])
def test_probelauf_legt_keine_konfig_an(repo: Path, name: str, nummer: str) -> None:
    """``--dry-run`` = keine Seiteneffekte, wie bei ``to_spawn.py pruefen --dry-run``."""
    ergebnis = _probelauf(repo, name, nummer)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert not (repo / ".to-spawn" / "config.json").exists()
