"""Tests für #408: ``spawn <S> --ziel srv`` auf dem Bau-Server selbst wie ``local`` behandeln.

Im Server-Modus (Maschinen-Datei ``rolle: "server"`` / ``bau_server: true`` oder passender
Hostname zu ``ssh_ziel``) gilt ``srv`` als „hier“ (wie ``local``, ohne ``server_repo`` und ohne
Stand-Check per SSH). Im PC-Modus ohne ``server_repo`` bleibt Exit 2 mit Hinweis
„auf dem Server: --ziel local“.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
GH_STUB = Path(__file__).resolve().parent / "hilfen" / "gh_stub.py"

if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import config, spawn, umzug


def _maschine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inhalt: dict[str, object]) -> Path:
    datei = tmp_path / "maschine.json"
    datei.write_text(json.dumps(inhalt, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(datei))
    return datei


def _sauberes_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    sauber = type("Bericht", (), {"sauber": True, "text": lambda self: "sauber"})()
    monkeypatch.setattr(spawn.manifest, "pruefe", lambda *_a, **_k: sauber)


def _wegwerf_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Kleines Git-Repo mit sauberem Manifest und gestelltem gh — für echte CLI-Läufe."""
    fern = tmp_path / "fern.git"
    subprocess.run(["git", "init", "--bare", "-q", str(fern)], check=True, capture_output=True)
    repo = tmp_path / "probe"
    repo.mkdir()
    for args in (
        ("init", "-q"),
        ("remote", "add", "origin", fern.as_posix()),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Test"),
    ):
        subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)
    ordner = repo / "docs" / "agents" / "manifests"
    ordner.mkdir(parents=True)
    (ordner / "spec-1.json").write_text(
        json.dumps(
            {
                "spec": 1,
                "feature": "probe",
                "tickets": {"2": {"title": "Eins", "schaetzung_k": 80, "umfang": "Test bauen."}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(GH_STUB))
    for name in ("GH_STUB_ZU", "GH_STUB_DATEN", "GH_STUB_BLOCKER"):
        monkeypatch.delenv(name, raising=False)
    return repo


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=dict(os.environ),
        check=False,
    )


# --- 1. Bau-Server-Erkennung (eine Stelle: config.ist_bau_server) ----------------


@pytest.mark.parametrize(
    ("inhalt", "erwartet"),
    [
        ({"rolle": "server"}, True),
        ({"rolle": "bau-server"}, True),
        ({"rolle": "bau_server"}, True),
        ({"rolle": "SERVER"}, True),
        ({"bau_server": True}, True),
        ({"rolle": "pc"}, False),
        ({"rolle": "laptop"}, False),
        ({"rolle": "lokal"}, False),
        ({"bau_server": False}, False),
    ],
)
def test_ist_bau_server_aus_maschinen_datei(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inhalt: dict[str, object], erwartet: bool
) -> None:
    _maschine(tmp_path, monkeypatch, inhalt)
    monkeypatch.setattr(config.socket, "gethostname", lambda: "mein-arbeitsplatz")
    assert config.ist_bau_server({}) is erwartet


def test_ist_bau_server_rolle_pc_ueberstimmt_hostnamen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _maschine(tmp_path, monkeypatch, {"rolle": "pc"})
    monkeypatch.setattr(config.socket, "gethostname", lambda: "bau-server")
    assert config.ist_bau_server({}) is False


@pytest.mark.parametrize(
    ("hostname", "konfig", "erwartet"),
    [
        ("bau-server", {}, True),
        ("BAU-SERVER.intern.local", {}, True),
        ("mein-srv", {"ssh_ziel": "bau@mein-srv"}, True),
        ("mein-laptop", {}, False),
        ("bau-server", {"ssh_ziel": "anderer-server"}, False),
        ("localhost", {"ssh_ziel": "localhost"}, False),
    ],
)
def test_ist_bau_server_ueber_hostnamen(
    monkeypatch: pytest.MonkeyPatch, hostname: str, konfig: dict[str, object], erwartet: bool
) -> None:
    monkeypatch.setattr(config.socket, "gethostname", lambda: hostname)
    assert config.ist_bau_server(konfig) is erwartet


def test_ssh_ziel_eine_quelle_mit_umzug(monkeypatch: pytest.MonkeyPatch) -> None:
    assert config.ssh_ziel({}) == "bau-server"
    assert config.ssh_ziel({"ssh_ziel": "eigener"}) == "eigener"
    assert umzug.ssh_ziel({"ssh_ziel": "eigener"}) == "eigener"
    monkeypatch.setenv("TO_SPAWN_SSH_ZIEL", "aus-env")
    assert config.ssh_ziel({"ssh_ziel": "eigener"}) == "aus-env"
    assert umzug.ssh_ziel({"ssh_ziel": "eigener"}) == "aus-env"


# --- 2. spawn --ziel srv im Server-Modus startet wie local -----------------------


def test_baue_befehl_srv_im_server_modus_wie_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _maschine(tmp_path, monkeypatch, {"rolle": "server"})
    monkeypatch.setattr(sys, "platform", "linux")
    befehl_srv = spawn.baue_befehl("srv", 1, ["2"], {}, True)
    befehl_local = spawn.baue_befehl("local", 1, ["2"], {}, True)
    assert befehl_srv == befehl_local
    assert befehl_srv[0] == "bash"
    assert "spawn_srv.sh" in befehl_srv[1]


def test_spawn_srv_im_server_modus_startet_wie_local_ohne_stand_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _maschine(tmp_path, monkeypatch, {"rolle": "server"})
    monkeypatch.setattr(sys, "platform", "linux")
    _sauberes_manifest(monkeypatch)
    stand_gerufen = False

    def stand_nie(*_a: object, **_k: object) -> int:
        nonlocal stand_gerufen
        stand_gerufen = True
        return 1

    monkeypatch.setattr(spawn.stand, "sichere_stand", stand_nie)
    rc = spawn.spawn(tmp_path, 1, {}, ziel="srv", dry_run=True)
    ausgabe = capsys.readouterr().out
    assert rc == 0
    assert not stand_gerufen
    assert "spawn_srv.sh" in ausgabe
    assert "ssh " not in ausgabe


def test_spawn_srv_per_hostname_startet_wie_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(config.socket, "gethostname", lambda: "bau-server")
    monkeypatch.setattr(sys, "platform", "linux")
    _sauberes_manifest(monkeypatch)
    rc = spawn.spawn(tmp_path, 1, {}, ziel="srv", dry_run=True)
    ausgabe = capsys.readouterr().out
    assert rc == 0
    assert "spawn_srv.sh" in ausgabe


def test_cli_spawn_srv_im_server_modus_gleich_wie_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Echter CLI-Weg: ``to_spawn.py spawn 1 --ziel srv --dry-run`` im Server-Modus = ``--ziel local``."""
    repo = _wegwerf_repo(tmp_path, monkeypatch)
    _maschine(tmp_path, monkeypatch, {"rolle": "server"})
    lauf_srv = _cli(repo, "spawn", "1", "--ziel", "srv", "--dry-run")
    lauf_local = _cli(repo, "spawn", "1", "--ziel", "local", "--dry-run")
    assert lauf_srv.returncode == 0, lauf_srv.stdout + lauf_srv.stderr
    assert lauf_local.returncode == 0, lauf_local.stdout + lauf_local.stderr
    assert lauf_srv.stdout.splitlines()[-1] == lauf_local.stdout.splitlines()[-1]


# --- 3. PC-Modus ohne server_repo: Exit 2 mit Hinweistext ------------------------


def test_spawn_srv_im_pc_modus_ohne_server_repo_exit_2_mit_hinweis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _maschine(tmp_path, monkeypatch, {"rolle": "pc"})
    _sauberes_manifest(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="to_spawn.spawn"):
        rc = spawn.spawn(tmp_path, 1, {}, ziel="srv", dry_run=True)
    assert rc == 2
    assert "server_repo" in caplog.text
    assert "auf dem Server: --ziel local" in caplog.text


def test_cli_spawn_srv_im_pc_modus_ohne_server_repo_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Echter CLI-Weg: im PC-Modus ohne ``server_repo`` bleibt Exit 2 mit Hinweistext."""
    repo = _wegwerf_repo(tmp_path, monkeypatch)
    _maschine(tmp_path, monkeypatch, {"rolle": "pc"})
    lauf = _cli(repo, "spawn", "1", "--ziel", "srv", "--dry-run")
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 2, ausgabe
    assert "server_repo" in ausgabe
    assert "auf dem Server: --ziel local" in ausgabe


def test_baue_befehl_srv_im_pc_modus_mit_server_repo_bleibt_ssh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _maschine(tmp_path, monkeypatch, {"rolle": "pc"})
    monkeypatch.setattr(sys, "platform", "linux")
    befehl = spawn.baue_befehl("srv", 1, None, {"ssh_ziel": "bau-server", "server_repo": "~/r"}, True)
    assert befehl[:2] == ["ssh", "bau-server"]


# --- 4. neustart --ziel srv folgt derselben Regel --------------------------------


def test_neustart_srv_im_server_modus_wie_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _maschine(tmp_path, monkeypatch, {"rolle": "server"})
    aus = type("Eintrag", (), {"zustand": "aus", "pid": None, "session_pid": None})()
    monkeypatch.setattr(spawn, "ticket_eintrag", lambda *_a: aus)
    monkeypatch.setattr(spawn, "_neustart_lokal_befehl", lambda *_a: (["echo", "ok"], False))
    monkeypatch.setattr(
        spawn.stand,
        "sichere_stand",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("kein Stand-Check auf dem Server")),
    )
    assert spawn.neustart(tmp_path, 1, 2, {}, ziel="srv", dry_run=True) == 0


def test_neustart_srv_im_pc_modus_ohne_server_repo_exit_2_mit_hinweis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _maschine(tmp_path, monkeypatch, {"rolle": "pc"})
    with caplog.at_level(logging.ERROR, logger="to_spawn.spawn"):
        rc = spawn.neustart(tmp_path, 1, 2, {"ssh_ziel": "x"}, ziel="srv", dry_run=True)
    assert rc == 2
    assert "server_repo" in caplog.text
    assert "auf dem Server: --ziel local" in caplog.text
