"""Einrichter (L1 Etappe 1, P5): Soll-Liste als Daten, ``stand.json``, ``--pruefen``.

Alles in einem Wegwerf-Repo unter ``tmp_path``; ``gh`` ist eine Attrappe über
``TO_SPAWN_GH_STUB`` (kein echter GitHub-Aufruf), die Maschinen-Liste lenkt
``tests/conftest.py`` auf ``tmp_path``.
"""

from __future__ import annotations

import dataclasses
import io
import json
import subprocess
from pathlib import Path

import pytest

from to_spawn import config, einrichten


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ort, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Frisches App-Repo mit getrackter ``.gitignore`` und GitHub-origin."""
    ort = tmp_path / "app"
    ort.mkdir()
    _git(ort, "init", "-q", "-b", "main")
    _git(ort, "config", "user.email", "t@t")
    _git(ort, "config", "user.name", "t")
    _git(ort, "remote", "add", "origin", "https://github.com/test-org/app.git")
    (ort / "README.md").write_text("app\n", encoding="utf-8")
    (ort / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    _git(ort, "add", ".")
    _git(ort, "commit", "-q", "-m", "start")
    stub = tmp_path / "gh_stub.py"
    stub.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    return ort


def _lauf(repo: Path, pruefen: bool = False) -> tuple[int, str]:
    ausgabe = io.StringIO()
    code = einrichten.einrichten(repo, pruefen=pruefen, ausgabe=ausgabe)
    return code, ausgabe.getvalue()


def _stand(repo: Path) -> Path:
    return repo / ".to-spawn" / "stand.json"


def test_frisch_einrichten_dann_pruefen_exit_0_und_git_status_leer(repo: Path) -> None:
    gitignore_vorher = (repo / ".gitignore").read_bytes()
    code, text = _lauf(repo)
    assert code == 0, text
    stand = json.loads(_stand(repo).read_text(encoding="utf-8"))
    assert set(stand) == {"skill_stand", "bausteine"}
    assert set(stand["bausteine"]) == {b.name for b in einrichten._BAUSTEINE}
    assert (repo / config.KONFIG_PFAD).is_file()
    assert (repo / "docs" / "agents" / "manifests" / "_default.json").is_file()
    code, text = _lauf(repo, pruefen=True)
    assert code == 0, text
    assert "alles da und aktuell" in text
    assert _git(repo, "status", "--porcelain") == ""
    assert (repo / ".gitignore").read_bytes() == gitignore_vorher


def test_zweiter_lauf_schreibt_nichts(repo: Path) -> None:
    assert _lauf(repo)[0] == 0
    mtime = _stand(repo).stat().st_mtime_ns
    maschine_mtime = config.maschine_datei().stat().st_mtime_ns
    code, text = _lauf(repo)
    assert code == 0, text
    zeilen = [z for z in text.splitlines() if z.startswith("  ")]
    assert len(zeilen) == len(einrichten._BAUSTEINE)
    assert all(": da_aktuell" in z for z in zeilen), text
    assert _stand(repo).stat().st_mtime_ns == mtime
    assert config.maschine_datei().stat().st_mtime_ns == maschine_mtime


def test_erhoehte_version_zieht_genau_diesen_baustein_nach(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _lauf(repo)[0] == 0
    neu = [
        dataclasses.replace(b, version=b.version + 1) if b.name == "manifest" else b
        for b in einrichten._BAUSTEINE
    ]
    monkeypatch.setattr(einrichten, "_BAUSTEINE", neu)
    assert _lauf(repo, pruefen=True)[0] == 3
    code, text = _lauf(repo)
    assert code == 0, text
    zeilen = [z.strip() for z in text.splitlines() if z.startswith("  ")]
    nachgezogen = [z for z in zeilen if ": nachgezogen" in z]
    assert len(nachgezogen) == 1 and nachgezogen[0].startswith("manifest:"), text
    assert sum(": da_aktuell" in z for z in zeilen) == len(neu) - 1
    stand = json.loads(_stand(repo).read_text(encoding="utf-8"))
    assert (
        stand["bausteine"]["manifest"]
        == neu[[b.name for b in neu].index("manifest")].version
    )
    assert _lauf(repo, pruefen=True)[0] == 0


def test_pruefen_schreibt_nichts_und_meldet_abweichung(repo: Path) -> None:
    code, text = _lauf(repo, pruefen=True)
    assert code == 3, text
    assert not (repo / ".to-spawn").exists()
    assert not config.maschine_datei().exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_kaputte_stand_json_heisst_neu_einrichten(repo: Path) -> None:
    _stand(repo).parent.mkdir(parents=True)
    _stand(repo).write_text("{kaputt", encoding="utf-8")
    code, text = _lauf(repo)
    assert code == 0, text
    assert json.loads(_stand(repo).read_text(encoding="utf-8"))["bausteine"]
    assert _lauf(repo, pruefen=True)[0] == 0


def test_weiterleitungen_ungetrackt_erneuert_getrackt_nur_gemeldet(repo: Path) -> None:
    skripte = repo / "scripts"
    skripte.mkdir()
    (skripte / "bau.py").write_text("# alt, getrackt\n", encoding="utf-8")
    _git(repo, "add", "scripts/bau.py")
    _git(repo, "commit", "-q", "-m", "alte Weiterleitung")
    (skripte / "wache.py").write_text("# alt, ungetrackt\n", encoding="utf-8")
    code, text = _lauf(repo)
    assert code == 0, text  # große Lücke hält nicht auf
    vorlage = einrichten._VORLAGEN / "wache.py"
    assert (skripte / "wache.py").read_bytes() == vorlage.read_bytes()
    assert (skripte / "bau.py").read_text(encoding="utf-8") == "# alt, getrackt\n"
    assert "weiterleitungen: grosse_luecke" in text
    assert "veraltet" in text and "scripts/bau.py" in text
    assert _lauf(repo, pruefen=True)[0] == 3


# --- Fix-Runde 1 (Review P5) ------------------------------------------------


def _gh_stub(repo: Path, inhalt: str, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = repo.parent / "gh_stub_fix1.py"
    stub.write_text(inhalt, encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))


def test_git_fehler_gilt_als_getrackt_und_schreibt_nicht(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed: scheitert ``git ls-files`` (z. B. 128 safe.directory), wird nichts überschrieben."""
    skripte = repo / "scripts"
    skripte.mkdir()
    (skripte / "wache.py").write_text("# alt, ungetrackt\n", encoding="utf-8")
    echt = einrichten._git

    def git(wurzel: Path, *args: str) -> tuple[int, str]:
        return (128, "") if args[:1] == ("ls-files",) else echt(wurzel, *args)

    monkeypatch.setattr(einrichten, "_git", git)
    code, text = _lauf(repo)
    assert code == 0, text
    assert (skripte / "wache.py").read_text(encoding="utf-8") == "# alt, ungetrackt\n"
    assert "weiterleitungen: grosse_luecke" in text


def test_haengendes_gh_haelt_spawn_nicht_auf(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    _gh_stub(repo, "import sys, time\ntime.sleep(3)\nsys.exit(1)\n", monkeypatch)
    monkeypatch.setattr(einrichten, "_GH_TIMEOUT", 0.3, raising=False)
    start = time.monotonic()
    code, text = _lauf(repo)
    dauer = time.monotonic() - start
    assert code == 0, text
    assert "checkpoint_label: grosse_luecke" in text
    assert dauer < 2.5, f"{dauer:.1f} s — gh ohne Zeitlimit"


def test_gh_fehler_meldet_und_haelt_nicht_auf(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gh_stub(repo, "import sys\nsys.exit(1)\n", monkeypatch)
    code, text = _lauf(repo)
    assert code == 0, text
    assert "checkpoint_label: grosse_luecke" in text


def test_leere_worktree_basis_in_getrackter_konfig_ist_gueltig(repo: Path) -> None:
    """DuoPlus: ``worktree_basis`` leer + Konfig getrackt = gültige Vorgabe, Exit 0."""
    assert _lauf(repo)[0] == 0
    konfig = repo / config.KONFIG_PFAD
    roh = json.loads(konfig.read_text(encoding="utf-8"))
    roh["worktree_basis"] = ""
    konfig.write_text(json.dumps(roh, indent=2) + "\n", encoding="utf-8")
    _git(repo, "add", "-f", str(config.KONFIG_PFAD))
    _git(repo, "commit", "-q", "-m", "Konfig getrackt")
    code, text = _lauf(repo, pruefen=True)
    assert code == 0, text
    assert "worktree_basis: da_aktuell" in text


def test_zweiter_lauf_aendert_keine_datei(repo: Path) -> None:
    """Schnappschuss aller Dateien (Inhalt + mtime) statt nur zweier mtimes."""

    def schnappschuss() -> dict[str, tuple[bytes, int]]:
        orte = [repo, config.maschine_datei().parent]
        return {
            str(p): (p.read_bytes(), p.stat().st_mtime_ns)
            for ort in orte
            for p in ort.rglob("*")
            if p.is_file() and ".git/objects" not in p.as_posix()
        }

    assert _lauf(repo)[0] == 0
    vorher = schnappschuss()
    assert _lauf(repo)[0] == 0
    assert schnappschuss() == vorher


def test_spawn_dry_run_geht_ueber_pruefweg(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util

    from to_spawn import spawn as spawn_modul

    cli_spec = importlib.util.spec_from_file_location(
        "to_spawn_cli_p5_fix1", einrichten._SKILL / "to_spawn.py"
    )
    assert cli_spec and cli_spec.loader
    cli = importlib.util.module_from_spec(cli_spec)
    cli_spec.loader.exec_module(cli)
    aufrufe: list[bool] = []
    echt = einrichten.einrichten

    def aufzeichnen(wurzel: Path, *, pruefen: bool = False, ausgabe=None) -> int:  # type: ignore[no-untyped-def]
        aufrufe.append(pruefen)
        return echt(wurzel, pruefen=pruefen, ausgabe=io.StringIO())

    monkeypatch.setattr(einrichten, "einrichten", aufzeichnen)
    monkeypatch.setattr(spawn_modul, "spawn", lambda *a, **k: 0)
    monkeypatch.chdir(repo)
    assert cli.main(["spawn", "999", "--dry-run"]) == 0
    assert aufrufe == [True]
    assert not _stand(repo).exists()
