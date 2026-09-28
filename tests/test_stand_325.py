"""Weg-Tests für den Skill-Stand-Schritt vor dem Server-Start (duoplus-management#325).

Echt laufen: CLI ``to_spawn.py stand``/``spawn``, die echte Skill-Stand-Wache aus dem
DuoPlus-Repo (mit ``--fern-lokal``: der „Server“ ist ein Ordner im ``tmp_path``) und ein
echter Kopier-Befehl als Push. Gestellt ist nur die ``gh``-CLI (GitHub ist ein externer
Dienst, per ``TO_SPAWN_GH_STUB``) und im Spawn-Test ``pwsh``/``powershell`` als
Sicherheitsnetz (schreibt nur eine Marker-Datei, damit nie ein echtes Terminal aufgeht).
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
GH_STUB = Path(__file__).resolve().parent / "hilfen" / "gh_stub.py"
#: Echte Skill-Stand-Wache aus dem DuoPlus-Worktree (#282/#325).
WACHE = Path(
    os.environ.get(
        "SKILL_STAND_WACHE", "C:/dev/wt-325/scripts/hooks/skill_stand_wache.py"
    )
)

SPEC = 900
TICKET = "901"

pytestmark = pytest.mark.skipif(not WACHE.is_file(), reason=f"Wache fehlt: {WACHE}")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


def _skill(wurzel: Path, inhalt: str) -> Path:
    (wurzel / "to_spawn").mkdir(parents=True, exist_ok=True)
    (wurzel / "to_spawn" / "kern.py").write_text(inhalt, encoding="utf-8")
    return wurzel


def _py(code: str) -> str:
    return f"python -c {shlex.quote(code)}"


@pytest.fixture()
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Wegwerf-Repo mit Bare-Remote, Manifest, gestelltem gh, PC- und Server-Skill."""
    fern = tmp_path / "fern.git"
    subprocess.run(
        ["git", "init", "--bare", str(fern)], check=True, capture_output=True
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "remote", "add", "origin", fern.as_posix())  # Slug braucht „/“
    ordner = repo / "docs" / "agents" / "manifests"
    ordner.mkdir(parents=True)
    eintrag = {"title": "Wegwerf-Ticket", "schaetzung_k": 120, "umfang": "Kern bauen."}
    (ordner / f"spec-{SPEC}.json").write_text(
        json.dumps({"spec": SPEC, "feature": "wegwerf", "tickets": {TICKET: eintrag}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(GH_STUB))
    for name in ("GH_STUB_ZU", "GH_STUB_DATEN", "GH_STUB_BLOCKER", "TO_SPAWN_TICKET"):
        monkeypatch.delenv(name, raising=False)
    pc = _skill(tmp_path / "pc", "print('neu')\n")
    server = _skill(tmp_path / "server", "print('alt')\n")
    return {"tmp": tmp_path, "repo": repo, "pc": pc, "server": server}


def _pruefen(welt: dict[str, Path], server: Path | None = None) -> str:
    fern = (server or welt["server"]).as_posix()
    return (
        f"python {shlex.quote(WACHE.as_posix())} --ziel ungueltig.invalid "
        f"--lokal {shlex.quote(welt['pc'].as_posix())} --fern-lokal {shlex.quote(fern)}"
    )


def _push_kopie(welt: dict[str, Path]) -> str:
    marker = (welt["tmp"] / "push_lief").as_posix()
    return _py(
        "import shutil, pathlib; "
        f"shutil.copytree({welt['pc'].as_posix()!r}, {welt['server'].as_posix()!r}, "
        "dirs_exist_ok=True); "
        f"pathlib.Path({marker!r}).write_text('ja')"
    )


def _konfig(welt: dict[str, Path], stand: dict[str, str] | None) -> None:
    daten: dict[str, object] = {
        "ssh_ziel": "ungueltig.invalid",
        "server_repo": "/nirgends",
    }
    if stand is not None:
        # Syntax-Sperre prüft die Test-Welt, nicht den echten Skill-Ordner.
        daten["stand"] = {"skill_ordner": welt["pc"].as_posix(), **stand}
    (welt["repo"] / ".to-spawn").mkdir(exist_ok=True)
    (welt["repo"] / ".to-spawn" / "config.json").write_text(
        json.dumps(daten), encoding="utf-8"
    )


def _cli(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, **(env or {})},
        check=False,
    )


def _aus(ergebnis: subprocess.CompletedProcess[str]) -> str:
    return ergebnis.stdout + ergebnis.stderr


# --- (a) abweichend → Push → Nachprüfung grün --------------------------------


def test_abweichender_server_wird_nachgezogen(welt: dict[str, Path]) -> None:
    _konfig(welt, {"pruefen": _pruefen(welt), "push": _push_kopie(welt)})
    ergebnis = _cli(welt["repo"], "stand")
    assert ergebnis.returncode == 0, _aus(ergebnis)
    assert (welt["tmp"] / "push_lief").exists()
    assert (welt["server"] / "to_spawn" / "kern.py").read_text(
        encoding="utf-8"
    ) == "print('neu')\n"
    assert "Push" in _aus(ergebnis)
    # Unabhängige Nachprüfung mit der echten Wache: jetzt gleich.
    nach = subprocess.run(
        [sys.executable, *shlex.split(_pruefen(welt))[1:]],
        capture_output=True,
        text=True,
        check=False,
    )
    assert nach.returncode == 0, nach.stdout + nach.stderr


# --- (b) gleich → kein Push ---------------------------------------------------


def test_gleicher_stand_pusht_nicht(welt: dict[str, Path]) -> None:
    _skill(welt["server"], "print('neu')\n")
    _konfig(welt, {"pruefen": _pruefen(welt), "push": _push_kopie(welt)})
    ergebnis = _cli(welt["repo"], "stand")
    assert ergebnis.returncode == 0, _aus(ergebnis)
    assert not (welt["tmp"] / "push_lief").exists()


# --- (c) Push scheitert --------------------------------------------------------


def test_gescheiterter_push_weigert_sich(welt: dict[str, Path]) -> None:
    _konfig(welt, {"pruefen": _pruefen(welt), "push": _py("import sys; sys.exit(5)")})
    ergebnis = _cli(welt["repo"], "stand")
    assert ergebnis.returncode != 0, _aus(ergebnis)
    assert "Push auf den Bau-Server gescheitert (Exit 5) — nichts gestartet" in _aus(
        ergebnis
    )


# --- (d) nicht prüfbar ---------------------------------------------------------


def test_nicht_pruefbar_weigert_sich(welt: dict[str, Path]) -> None:
    stand = {
        "pruefen": _pruefen(welt, welt["tmp"] / "gibt_es_nicht"),
        "push": _push_kopie(welt),
    }
    _konfig(welt, stand)
    ergebnis = _cli(welt["repo"], "stand")
    assert ergebnis.returncode != 0, _aus(ergebnis)
    assert "Stand nicht prüfbar (Exit 1) — nichts gestartet" in _aus(ergebnis)
    assert not (welt["tmp"] / "push_lief").exists()


def test_fehlender_befehl_ohne_stacktrace(welt: dict[str, Path]) -> None:
    _konfig(welt, {"pruefen": "gibt-es-nicht-325 --x", "push": ""})
    ergebnis = _cli(welt["repo"], "stand")
    assert ergebnis.returncode != 0, _aus(ergebnis)
    assert "Traceback" not in _aus(ergebnis)
    assert "gibt-es-nicht-325" in _aus(ergebnis)


# --- (e) spawn --ziel srv startet bei kaputtem Push nichts --------------------


def _terminal_marker(tmp: Path) -> tuple[Path, dict[str, str]]:
    marker = tmp / "terminal_gestartet"
    bin_ordner = tmp / "bin"
    bin_ordner.mkdir()
    for name in ("pwsh", "powershell"):
        if os.name == "nt":
            (bin_ordner / f"{name}.cmd").write_text(
                f'@echo %* >> "{marker}"\r\n', encoding="utf-8"
            )
        else:
            programm = bin_ordner / name
            programm.write_text(
                f'#!/bin/sh\necho "$@" >> "{marker}"\n', encoding="utf-8"
            )
            programm.chmod(programm.stat().st_mode | stat.S_IEXEC)
    return marker, {"PATH": f"{bin_ordner}{os.pathsep}{os.environ['PATH']}"}


def test_spawn_srv_mit_kaputtem_push_startet_nicht(welt: dict[str, Path]) -> None:
    _konfig(welt, {"pruefen": _pruefen(welt), "push": _py("import sys; sys.exit(5)")})
    marker, env = _terminal_marker(welt["tmp"])
    ergebnis = _cli(welt["repo"], "spawn", str(SPEC), "--ziel", "srv", env=env)
    assert ergebnis.returncode == 3, _aus(ergebnis)
    assert "Push auf den Bau-Server gescheitert (Exit 5) — nichts gestartet" in _aus(
        ergebnis
    )
    assert "Ziel srv ·" not in _aus(ergebnis)
    assert not marker.exists()


def test_spawn_srv_dry_run_prueft_nur(welt: dict[str, Path]) -> None:
    _konfig(welt, {"pruefen": _pruefen(welt), "push": _push_kopie(welt)})
    ergebnis = _cli(welt["repo"], "spawn", str(SPEC), "--ziel", "srv", "--dry-run")
    assert ergebnis.returncode == 0, _aus(ergebnis)
    assert not (welt["tmp"] / "push_lief").exists()
    assert "weicht ab" in _aus(ergebnis)


# --- (f) ohne Konfig übersprungen ----------------------------------------------


@pytest.mark.parametrize("stand", [None, {"pruefen": "", "push": ""}])
def test_ohne_stand_konfig_uebersprungen(
    welt: dict[str, Path], stand: dict[str, str] | None
) -> None:
    _konfig(welt, stand)
    ergebnis = _cli(welt["repo"], "stand")
    assert ergebnis.returncode == 0, _aus(ergebnis)
    assert "Stand-Check nicht eingerichtet" in _aus(ergebnis)


# --- Fixrunde nach Prüfpanel (#325) --------------------------------------------


def test_bash_befehl_laeuft_unter_windows_mit_git_bash(welt: dict[str, Path]) -> None:
    """Führendes ``bash`` darf unter Windows nicht die WSL-bash aus System32 treffen."""
    _konfig(welt, {"pruefen": "bash -c 'exit 0'", "push": ""})
    ergebnis = _cli(welt["repo"], "stand")
    assert ergebnis.returncode == 0, _aus(ergebnis)


def test_haengender_pruefbefehl_bricht_nach_zeitlimit_ab(welt: dict[str, Path]) -> None:
    stand: dict[str, object] = {
        "pruefen": _py("import time; time.sleep(5)"),
        "push": _push_kopie(welt),
        "timeout_pruefen_s": 1,
    }
    _konfig(welt, stand)  # type: ignore[arg-type]
    ergebnis = _cli(welt["repo"], "stand")
    assert ergebnis.returncode != 0, _aus(ergebnis)
    assert "Zeitlimit (1 s) überschritten" in _aus(ergebnis)
    assert "Traceback" not in _aus(ergebnis)
    assert not (welt["tmp"] / "push_lief").exists()


def test_fehlende_stand_konfig_warnt(welt: dict[str, Path]) -> None:
    _konfig(welt, None)
    ergebnis = _cli(welt["repo"], "stand")
    assert ergebnis.returncode == 0, _aus(ergebnis)
    assert (
        "WARNING Stand-Check nicht eingerichtet — Server könnte alten Skill haben"
        in _aus(ergebnis)
    )


def test_befehle_bekommen_den_python_des_aufrufers(welt: dict[str, Path]) -> None:
    """Push-Kette (nest_push.sh) nimmt sonst ``python3`` vom PATH — auf Windows ein
    älteres Python als der Aufrufer (Live-Lauf #325: ImportError ``datetime.UTC``)."""
    probe = (
        "python -c \"import os,sys; p=os.environ.get('TO_SPAWN_PY'); "
        "sys.exit(0 if p and os.path.samefile(p, sys.executable) else 7)\""
    )
    _konfig(welt, {"pruefen": probe, "push": ""})
    ergebnis = _cli(welt["repo"], "stand")
    assert ergebnis.returncode == 0, _aus(ergebnis)


def test_halbfertiger_skill_wird_nicht_gepusht(welt: dict[str, Path]) -> None:
    """Live-Lauf #325: eine andere Session hatte capo.py halb gespeichert, der Auto-Push
    trug den Syntaxfehler auf den Bau-Server. Syntaxfehler im Skill → kein Push."""
    (welt["pc"] / "to_spawn" / "kaputt.py").write_text('x = f"offen\n', encoding="utf-8")
    _konfig(
        welt,
        {
            "pruefen": _pruefen(welt),
            "push": _push_kopie(welt),
            "skill_ordner": welt["pc"].as_posix(),
        },
    )
    ergebnis = _cli(welt["repo"], "stand")
    assert ergebnis.returncode != 0, _aus(ergebnis)
    assert not (welt["tmp"] / "push_lief").exists(), _aus(ergebnis)
    assert "Syntaxfehler" in _aus(ergebnis) and "kaputt.py" in _aus(ergebnis)
