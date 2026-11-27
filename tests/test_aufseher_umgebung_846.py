"""#846: ``TO_SPAWN_MASCHINE`` und ``TO_SPAWN_HOME`` gehen beim Start über den Aufseher nicht verloren.

Prüft alle Startwege (Aufseher/``capo.folge_befehl``, Ablösung/``respawn``, Aufpasser
``bau_befehl`` + ``wache_befehl``, Windows-Tab ``spawn.wt_tab_befehl``) sowie einen echten
``bash -lc``-Lauf ohne geerbte tmux-Server-Umgebung.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import aufpasser, capo, respawn, sitzung_env, spawn


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="bash-Startzeile läuft nur unter POSIX mit bash",
)
def test_aufseher_startweg_vererbt_maschine_und_skill_home_echt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rot-Beweis-Weg-Test (#846): Start über ``capo.folge_befehl`` (Aufseher / ``to_spawn.py neustart``)
    mit gesetzter ``TO_SPAWN_MASCHINE`` und ``TO_SPAWN_HOME`` → der gestartete ``bau.py``-Prozess
    erhält beide Variablen, selbst wenn der tmux-Server sie nicht in seiner Umgebung hat."""
    skill = tmp_path / "probe-skill"
    (skill / "skripte").mkdir(parents=True)
    ausgabe = tmp_path / "bau_env.json"
    (skill / "skripte" / "bau.py").write_text(
        "import json, os, sys\n"
        f"open({str(ausgabe)!r}, 'w', encoding='utf-8').write(json.dumps({{\n"
        "    k: os.environ.get(k) for k in ('TO_SPAWN_MASCHINE', 'TO_SPAWN_HOME', 'TO_SPAWN_REPO', 'REPO')\n"
        "}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    maschine = tmp_path / "probe-skill" / ".maschine.json"
    maschine.write_text('{"rolle": "server", "repos": []}', encoding="utf-8")
    repo = tmp_path / "probe-repo"
    repo.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    monkeypatch.setenv("TO_SPAWN_HOME", str(skill))

    argv = capo.folge_befehl(repo, 1, 2, ["wache 1"])
    assert argv[-3:-1] == ["bash", "-lc"]
    zeile = argv[-1]

    # Saubere Umgebung wie in einem bestehenden tmux-Server ohne TO_SPAWN_MASCHINE / TO_SPAWN_HOME.
    saubere_env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
    }
    lauf = subprocess.run(
        ["bash", "-lc", zeile],
        cwd=repo,
        env=saubere_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stderr
    assert ausgabe.is_file(), f"bau.py aus TO_SPAWN_HOME wurde nicht gestartet; stderr={lauf.stderr!r}"
    geerbt = json.loads(ausgabe.read_text(encoding="utf-8"))
    assert geerbt["TO_SPAWN_MASCHINE"] == str(maschine)
    assert geerbt["TO_SPAWN_HOME"] == str(skill)
    assert geerbt["TO_SPAWN_REPO"] == str(repo)
    assert geerbt["REPO"] == str(repo)


def test_alle_linux_startwege_exportieren_to_spawn_maschine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``capo.bau_startzeile``, ``respawn._startbefehl``, ``aufpasser.bau_befehl`` und
    ``aufpasser.wache_befehl`` tragen alle ``TO_SPAWN_MASCHINE`` weiter — und lassen sie weg,
    wenn sie nicht gesetzt ist."""
    repo = tmp_path / "repo"
    repo.mkdir()
    maschine = tmp_path / "mit leerzeichen" / ".maschine.json"
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))

    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "z", repo=repo))
    erwartet = f"TO_SPAWN_MASCHINE={shlex.quote(str(maschine))}"

    for name, zeile in (
        ("capo.bau_startzeile", capo.bau_startzeile(repo, 2, "")),
        ("respawn._startbefehl", shlex.split(respawn._startbefehl(repo, 2))[-1]),
        ("aufpasser.bau_befehl", a.bau_befehl(repo, 2, None)),
        ("aufpasser.wache_befehl", a.wache_befehl(repo, "1", None)),
    ):
        assert erwartet in zeile, f"{name}: {zeile}"

    monkeypatch.delenv("TO_SPAWN_MASCHINE", raising=False)
    for name, zeile in (
        ("capo.bau_startzeile", capo.bau_startzeile(repo, 2, "")),
        ("aufpasser.wache_befehl", a.wache_befehl(repo, "1", None)),
    ):
        assert "TO_SPAWN_MASCHINE=" not in zeile, f"{name}: {zeile}"


def test_windows_tab_startweg_reicht_to_spawn_maschine_und_home_ohne_semikolon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows-Startweg ``spawn.wt_tab_befehl`` reicht ``TO_SPAWN_MASCHINE`` und ``TO_SPAWN_HOME``
    ohne ``;`` (``wt``-Tab-Trenner) weiter — und lässt beide weg, wenn sie nicht gesetzt sind."""
    repo = tmp_path / "repo"
    repo.mkdir()
    maschine = tmp_path / "probe" / ".maschine.json"
    skill = tmp_path / "probe-skill"
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    monkeypatch.setenv("TO_SPAWN_HOME", str(skill))

    cmd = spawn.wt_tab_befehl(repo, 2, "")
    innen = cmd[cmd.index("-Command") + 1]
    assert ";" not in innen, innen
    assert f"Set-Item Env:TO_SPAWN_MASCHINE '{maschine}'" in innen, innen
    assert f"Set-Item Env:TO_SPAWN_HOME '{skill}'" in innen, innen

    monkeypatch.delenv("TO_SPAWN_MASCHINE", raising=False)
    monkeypatch.delenv("TO_SPAWN_HOME", raising=False)
    cmd_leer = spawn.wt_tab_befehl(repo, 2, "")
    innen_leer = cmd_leer[cmd_leer.index("-Command") + 1]
    assert "TO_SPAWN_MASCHINE" not in innen_leer, innen_leer
    assert "TO_SPAWN_HOME" not in innen_leer, innen_leer


def test_sitzung_env_buendelt_fenster_umgebung_und_quotet_sonderzeichen(tmp_path: Path) -> None:
    """Eine Stelle (``sitzung_env.fenster_umgebung`` / ``shell_vorspann`` / ``pwsh_vorspann``)
    für alle Startwege — samt Quoting von Leerzeichen und Hochkommata."""
    repo = tmp_path / "mein repo"
    skill = tmp_path / "probe skill"
    maschine = tmp_path / "nutzer's dir" / ".maschine.json"
    env = {"TO_SPAWN_MASCHINE": str(maschine), "TO_SPAWN_HOME": str(skill)}

    werte = sitzung_env.fenster_umgebung(
        repo,
        warte_datei=tmp_path / "warte",
        auftrag="Weiterbauen",
        env=env,
    )
    assert werte == {
        "REPO": str(repo),
        "TO_SPAWN_HOME": str(skill),
        "TO_SPAWN_REPO": str(repo),
        "TO_SPAWN_WARTE_DATEI": str(tmp_path / "warte"),
        "TO_SPAWN_MASCHINE": str(maschine),
        "BAU_AUFTRAG": "Weiterbauen",
    }

    shell = sitzung_env.shell_vorspann(repo, env=env)
    assert shell.startswith("export ") and shell.endswith(";")
    assert f"TO_SPAWN_MASCHINE={shlex.quote(str(maschine))}" in shell
    assert f"TO_SPAWN_HOME={shlex.quote(str(skill))}" in shell

    pwsh = sitzung_env.pwsh_vorspann(repo, env=env)
    assert ";" not in pwsh
    assert "nutzer''s dir" in pwsh
    assert f"Set-Item Env:TO_SPAWN_HOME '{skill}'" in pwsh
