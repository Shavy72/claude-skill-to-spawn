"""#257: to-spawn fremdrepo-tauglich — Rot-Beweis vor dem Bau.

Deckt die Tabelle aus ``docs/PLAN_257_fremdrepo.md`` ab: Hauptzweig-Erkennung
(``gh.hauptzweig``), Checkpoint-Label (``gh.label_sicherstellen``), repo-neutrale
Prompt-Vorlage (``build_prompt`` + ``repo-scripts/_default.json``), ``spawn`` ohne
harte ``pwsh``-Abhängigkeit, ``spawn_srv.sh`` ohne Shell-Funktionen/``origin/master``,
Vertrauens-Dialog (``to_spawn.vertrauen``), globale Worktree-Basis (``config``),
Bau-Log-Rückfall (``bau_log.log_repo``), Skill-eigener Staffel-Hook und der
Probesitz-Sonderfall „nicht konfiguriert sperrt nicht".

Attrappen nur für ``gh`` (PATH-Stub) — alle Git-Repos sind echt (``tmp_path``).
Neue Module (``to_spawn.vertrauen``) werden erst in der Testfunktion importiert,
damit ein fehlendes Modul nur den einen Test rot macht, nicht die ganze Datei.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"

if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import bau_log, config, gh, probesitz, spawn

_ZAEHLER = itertools.count()


# --- Bausteine ---------------------------------------------------------------


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(ort), check=True, capture_output=True, text=True).stdout.strip()


def _identity(repo: Path) -> None:
    for schluessel, wert in (
        ("user.name", "Test"),
        ("user.email", "test@example.com"),
        ("commit.gpgsign", "false"),
    ):
        _git(repo, "config", schluessel, wert)


def _commit(repo: Path, datei: str = "a.txt", text: str = "x\n") -> None:
    (repo / datei).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "start")


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path) -> object:
    """``skripte/bau.py`` frisch laden, mit ``TO_SPAWN_REPO`` = ``repo`` (Vorbild ``test_umzug_205.py``)."""
    import importlib.util

    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    modulname = f"bau_257_{next(_ZAEHLER)}"
    spec = importlib.util.spec_from_file_location(modulname, SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[modulname] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(modulname, None)
    return modul


# --- 1. gh.hauptzweig ---------------------------------------------------------


def test_hauptzweig_aus_config(tmp_path: Path) -> None:
    repo = tmp_path / "config-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(json.dumps({"hauptzweig": "entwicklung"}), encoding="utf-8")
    assert gh.hauptzweig(repo) == "entwicklung"


def test_hauptzweig_aus_origin_head(tmp_path: Path) -> None:
    bare = tmp_path / "bare.git"
    _git(tmp_path, "init", "--bare", "-q", "-b", "main", str(bare))
    producer = tmp_path / "producer"
    _git(tmp_path, "init", "-q", "-b", "main", str(producer))
    _identity(producer)
    _commit(producer)
    _git(producer, "remote", "add", "origin", str(bare))
    _git(producer, "push", "-q", "-u", "origin", "main")
    klon = tmp_path / "klon"
    _git(tmp_path, "clone", "-q", str(bare), str(klon))
    assert gh.hauptzweig(klon) == "main"


def test_hauptzweig_nur_origin_master(tmp_path: Path) -> None:
    bare = tmp_path / "bare-master.git"
    _git(tmp_path, "init", "--bare", "-q", "-b", "master", str(bare))
    work = tmp_path / "work-master"
    _git(tmp_path, "init", "-q", "-b", "master", str(work))
    _identity(work)
    _commit(work)
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-q", "-u", "origin", "master")
    assert gh.hauptzweig(work) == "master"


def test_hauptzweig_nur_origin_main(tmp_path: Path) -> None:
    bare = tmp_path / "bare-main.git"
    _git(tmp_path, "init", "--bare", "-q", "-b", "main", str(bare))
    work = tmp_path / "work-main"
    _git(tmp_path, "init", "-q", "-b", "main", str(work))
    _identity(work)
    _commit(work)
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-q", "-u", "origin", "main")
    assert gh.hauptzweig(work) == "main"


def test_hauptzweig_rueckfall_master(tmp_path: Path) -> None:
    repo = tmp_path / "nichts-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    assert gh.hauptzweig(repo) == "master"


# --- 2. gh.label_sicherstellen ------------------------------------------------


def test_label_sicherstellen_mit_gh_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TO_SPAWN_GH_STUB", raising=False)
    binaer = tmp_path / "bin"
    binaer.mkdir()
    protokoll = tmp_path / "gh-aufrufe.txt"
    stub = binaer / "gh"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{protokoll}"\nexit 0\n', encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binaer}{os.pathsep}{os.environ.get('PATH', '')}")
    repo = tmp_path / "label-repo"
    repo.mkdir()
    assert gh.label_sicherstellen(repo, "checkpoint:human") is True
    text = protokoll.read_text(encoding="utf-8")
    # Fixrunde 1 (F2): erst ``gh api`` prüfen; Stub sagt „vorhanden“ → kein create, nie --force.
    assert "api repos/{owner}/{repo}/labels/checkpoint%3Ahuman" in text
    assert "--force" not in text


def test_label_sicherstellen_ohne_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TO_SPAWN_GH_STUB", raising=False)
    leer = tmp_path / "leeres-path"
    leer.mkdir()
    monkeypatch.setenv("PATH", str(leer))
    repo = tmp_path / "label-repo-ohne"
    repo.mkdir()
    assert gh.label_sicherstellen(repo, "checkpoint:human") is False


# --- 3. skripte/bau.py build_prompt -------------------------------------------


def test_build_prompt_ersetzt_repo_hauptzweig_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "projrepo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", "https://github.com/acme/produkt.git")
    modul = _lade_bau(monkeypatch, repo)
    vorlage = "Repo {REPO} Zweig {HAUPTZWEIG} Label {CHECKPOINT_LABEL} Ticket {N} Spec {S}"
    ergebnis = modul.build_prompt(
        vorlage,
        "5",
        "1",
        "Titel",
        "Kontext",
        konfig={"regularien": {"checkpoint_label": "checkpoint:human"}},
    )
    assert "{" not in ergebnis, ergebnis
    assert "acme/produkt" in ergebnis
    assert "checkpoint:human" in ergebnis


# --- 4. repo-scripts/_default.json --------------------------------------------


def test_default_json_repo_neutral() -> None:
    text = (SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8")
    for verboten in ("duoplus", "clawy-vps", "safe_deploy", "[skip ci]", "Davids", "origin/master"):
        assert verboten not in text, f"verbotenes Wort noch da: {verboten}"
    for erwartet in ("{REPO}", "{HAUPTZWEIG}", "Worktree nie", "keine Rückfragen"):
        assert erwartet in text, f"erwarteter Text fehlt: {erwartet}"


# --- 5. to_spawn.spawn.baue_befehl --------------------------------------------


def test_baue_befehl_lokal_ohne_pwsh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    befehl = spawn.baue_befehl("local", 42, None, {}, False)
    assert befehl[0] == "bash"
    assert any("spawn_srv.sh" in teil for teil in befehl)
    assert str(42) in befehl or "42" in " ".join(befehl)
    assert not any("pwsh" in teil for teil in befehl)


def test_baue_befehl_lokal_tickets_und_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    befehl = spawn.baue_befehl("local", 42, ["3", "4"], {}, True)
    assert "--tickets" in befehl
    assert befehl[befehl.index("--tickets") + 1] == "3,4"
    assert "--dry-run" in befehl
    assert not any("pwsh" in teil for teil in befehl)


def test_baue_befehl_srv_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    konfig = {"ssh_ziel": "x", "server_repo": "/srv/r"}
    befehl = spawn.baue_befehl("srv", 42, None, konfig, False)
    assert befehl[0] == "ssh"


def test_baue_befehl_windows_bleibt_pwsh(monkeypatch: pytest.MonkeyPatch) -> None:
    # shutil.which prüft bei sys.platform == "win32" intern über _winapi — auf einem
    # echten Linux-Prüfstand stürzt das ab, darum hier zusätzlich gestellt (kein
    # Verhalten des geprüften Codes, nur ein Umgebungs-Stolperstein).
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(spawn.shutil, "which", lambda *_a, **_k: None)
    befehl = spawn.baue_befehl("local", 42, None, {}, False)
    assert any("pwsh" in teil or "powershell" in teil for teil in befehl)


# --- 6. skripte/spawn_srv.sh --------------------------------------------------


def test_spawn_srv_bash_syntax_ok() -> None:
    ergebnis = subprocess.run(
        ["bash", "-n", str(SKRIPTE / "spawn_srv.sh")], capture_output=True, text=True, check=False
    )
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_spawn_srv_kein_hartes_origin_master() -> None:
    text = (SKRIPTE / "spawn_srv.sh").read_text(encoding="utf-8")
    assert text.count("origin/master") == 0, "origin/master noch hart verdrahtet"


def test_spawn_srv_fenster_ohne_shell_funktionen() -> None:
    text = (SKRIPTE / "spawn_srv.sh").read_text(encoding="utf-8")
    assert "bau.py" in text
    assert "wache.py" in text
    assert not re.search(r'GEPLANT\+=\("(bau|wache) ', text), (
        "Fenster rufen noch die Shell-Funktionen bau/wache statt bau.py/wache.py auf"
    )


@pytest.mark.skipif(shutil.which("tmux") is None, reason="spawn_srv.sh verlangt tmux")
def test_spawn_srv_dry_run_zeigt_origin_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Echter Trockenlauf in einem Repo mit Hauptzweig ``main`` und ohne ~/.bashrc-Funktionen:
    ``--ohne-regularien`` spart gh, ``--dry-run`` startet kein tmux-Fenster (B5 + B6)."""
    bare = tmp_path / "origin-main.git"
    _git(tmp_path, "init", "--bare", "-q", "-b", "main", str(bare))
    repo = tmp_path / "fremd"
    _git(tmp_path, "clone", "-q", str(bare), str(repo))
    _identity(repo)
    _git(repo, "checkout", "-q", "-B", "main")
    manifeste = repo / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    (manifeste / "spec-7.json").write_text(
        json.dumps({"spec": 7, "tickets": {"71": {"title": "Eins"}}}), encoding="utf-8"
    )
    _commit(repo)
    _git(repo, "push", "-q", "-u", "origin", "main")
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    ergebnis = subprocess.run(
        ["bash", str(SKRIPTE / "spawn_srv.sh"), "7", "--ohne-regularien", "--dry-run"],
        cwd=str(repo),
        env={**os.environ, "TO_SPAWN_REPO": str(repo)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    assert "origin/main" in ausgabe, ausgabe
    assert "origin/master" not in ausgabe, ausgabe
    assert "- wache 7" in ausgabe and "wache.py" in ausgabe, ausgabe
    assert "- bau 71" in ausgabe and "bau.py" in ausgabe, ausgabe


# --- 7. to_spawn.vertrauen.sicherstellen --------------------------------------


def test_vertrauen_setzt_flag_und_bewahrt_fremde_schluessel(tmp_path: Path) -> None:
    from to_spawn import vertrauen  # neues Modul — Import erst hier (#257)

    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(
        json.dumps({"projects": {"/x": {"foo": 1}}, "andere": 2}, ensure_ascii=False),
        encoding="utf-8",
    )
    pfad = tmp_path / "arbeit"
    pfad.mkdir()
    vertrauen.sicherstellen(pfad, claude_json=claude_json)
    daten = json.loads(claude_json.read_text(encoding="utf-8"))
    assert daten["projects"][str(pfad)]["hasTrustDialogAccepted"] is True
    assert daten["projects"]["/x"]["foo"] == 1
    assert daten["andere"] == 2

    vorher = claude_json.read_bytes()
    vertrauen.sicherstellen(pfad, claude_json=claude_json)
    assert claude_json.read_bytes() == vorher, "zweiter Aufruf ändert die Datei"


def test_vertrauen_legt_fehlende_datei_an(tmp_path: Path) -> None:
    from to_spawn import vertrauen  # neues Modul — Import erst hier (#257)

    claude_json = tmp_path / "unterordner" / ".claude.json"
    pfad = tmp_path / "arbeit2"
    pfad.mkdir()
    ergebnis = vertrauen.sicherstellen(pfad, claude_json=claude_json)
    assert claude_json.is_file()
    daten = json.loads(claude_json.read_text(encoding="utf-8"))
    assert daten["projects"][str(pfad)]["hasTrustDialogAccepted"] is True
    assert ergebnis == [str(pfad)]


# --- 8. to_spawn.config -------------------------------------------------------


def test_defaults_worktree_basis_und_hauptzweig_leer() -> None:
    assert config.DEFAULTS["worktree_basis"] == ""
    assert config.DEFAULTS["hauptzweig"] == ""


def test_worktree_pfad_mit_worktree_basis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "projekt"
    (repo / ".git").mkdir(parents=True)
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(json.dumps({"worktree_basis": "~/wt/probe"}), encoding="utf-8")
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.chdir(repo)
    ergebnis = config.worktree_pfad("5")
    assert ergebnis.endswith("wt/probe/wt-5"), ergebnis


def test_worktree_pfad_ohne_feld_altes_verhalten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "duoplus"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.delenv("BAU_WT_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.chdir(repo)
    ergebnis = config.worktree_pfad("1")
    assert ergebnis == f"{Path.home().as_posix()}/wt/wt-1"


def test_sicherstellen_frisch_setzt_repo_namen_in_worktree_basis(tmp_path: Path) -> None:
    repo = tmp_path / "mein-repo"
    (repo / ".git").mkdir(parents=True)
    datei = config.sicherstellen(repo)
    daten = json.loads(datei.read_text(encoding="utf-8"))
    assert daten["worktree_basis"].endswith(f"/{repo.name}"), daten.get("worktree_basis")


def test_sicherstellen_vorhandene_datei_bleibt_unangetastet_defaults_beim_lesen(
    tmp_path: Path,
) -> None:
    """Vorhandene Konfig bleibt byte-gleich (B8): fehlende Felder kommen erst
    beim Lesen aus ``DEFAULTS`` — ``sicherstellen`` trägt nichts nach."""
    repo = tmp_path / "alt-repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".to-spawn").mkdir()
    bestehend = {k: v for k, v in config.DEFAULTS.items() if k not in ("worktree_basis", "hauptzweig")}
    roh = json.dumps(bestehend)
    (repo / ".to-spawn" / "config.json").write_text(roh, encoding="utf-8")
    config.sicherstellen(repo)
    assert (repo / ".to-spawn" / "config.json").read_text(encoding="utf-8") == roh
    daten = config.lade(repo)
    assert daten["worktree_basis"] == ""
    assert daten["hauptzweig"] == ""


# --- 9. to_spawn.bau_log.log_repo ---------------------------------------------


def test_log_repo_faellt_auf_to_spawn_repo_zurueck(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    geloescht = tmp_path / "nicht-mehr-da"
    vorhanden = tmp_path / "hauptbaum"
    vorhanden.mkdir()
    monkeypatch.setenv("TO_SPAWN_LOG_REPO", str(geloescht))
    monkeypatch.setenv("TO_SPAWN_REPO", str(vorhanden))
    ergebnis = bau_log.log_repo()
    assert ergebnis == vorhanden.resolve(), ergebnis


# --- 10. Staffel-Hook im Skill -------------------------------------------------


def test_skill_hat_eigene_staffel_hook_kopie() -> None:
    assert (SKILL / "skripte" / "hooks" / "staffel_stop.py").is_file()


def test_staffel_hook_pfad_bevorzugt_repo_kopie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "mit-hook"
    (repo / ".git").mkdir(parents=True)
    hook = repo / "scripts" / "hooks" / "staffel_stop.py"
    hook.parent.mkdir(parents=True)
    hook.write_text("# hook\n", encoding="utf-8")
    modul = _lade_bau(monkeypatch, repo)
    assert modul.staffel_hook_pfad(repo) == hook


def test_staffel_hook_pfad_fallback_skill_kopie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "ohne-hook"
    (repo / ".git").mkdir(parents=True)
    modul = _lade_bau(monkeypatch, repo)
    erwartet = SKILL / "skripte" / "hooks" / "staffel_stop.py"
    assert modul.staffel_hook_pfad(repo) == erwartet


# --- 11. to_spawn.probesitz.offene_punkte -------------------------------------


def _zustand(punkt3_grund: str, punkt3_ok: bool = False) -> dict:
    def _pkt(ok: bool, grund: str) -> dict:
        return {"ok": ok, "grund": grund, "fehlt_noch": "", "beleg": "", "ts": "2026-09-21T00:00:00+00:00"}

    return {
        "punkte": {
            "1": _pkt(True, "ok"),
            "2": _pkt(True, "ok"),
            "3": _pkt(punkt3_ok, punkt3_grund),
            "4": _pkt(True, "ok"),
            "5": _pkt(True, "ok"),
            "6": _pkt(True, "ok"),
        }
    }


def test_offene_punkte_staging_nicht_konfiguriert_sperrt_nicht() -> None:
    zustand = _zustand("staging.url fehlt")
    assert probesitz.offene_punkte(zustand, bis=6) == []


def test_offene_punkte_staging_mit_anderem_grund_sperrt() -> None:
    zustand = _zustand("HTTP 500")
    assert probesitz.offene_punkte(zustand, bis=6) == [3]
