"""#138 — Folge-Befunde zu #89: Installer und ``skill_stand`` verhalten sich in Randfällen ehrlich.

(1) Klon ohne ``origin`` → Hinweis nennt die Ursache statt „einmal neu installieren“.
(2) kein Python → Abbruch vor dem Kopieren, Zielordner unverändert (sh + ps1).
(3) Neuinstallation meldet nicht „nur im alten Stand: STAND.json“ (sh + ps1).
(4) ungespeicherte Änderung im Klon → Stempel trägt ``dirty``, Vergleich meldet nicht „aktuell“.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_skill_stand_e2 import (
    INSTALLER,
    PWSH,
    SKILL,
    _commit,
    _git,
    _skill_klon,
    _umgebung,
)

from to_spawn import skill_stand


def _mini_klon(tmp: Path, *, origin: bool = True) -> Path:
    """Kleiner Git-Klon (eine Datei), auf Wunsch mit Bare-Repo als ``origin``."""
    klon = tmp / "klon"
    klon.mkdir()
    _git(klon, "init", "-q", "-b", "main")
    (klon / "datei.txt").write_text("a\n", encoding="utf-8")
    _commit(klon, "start")
    if origin:
        bare = tmp / "origin.git"
        _git(tmp, "init", "-q", "--bare", "-b", "main", str(bare))
        _git(klon, "remote", "add", "origin", str(bare))
        _git(klon, "push", "-q", "origin", "main")
    return klon


def _lauf(art: str, klon: Path, skills: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    claude_md = skills.parent / "CLAUDE.md"
    if art == "sh":
        befehl = [
            "bash",
            str(klon / "install.sh"),
            "--skills",
            str(skills),
            "--claude-md",
            str(claude_md),
        ]
    else:
        befehl = [PWSH or "pwsh", "-NoProfile", "-File", str(klon / "install.ps1")]
        befehl += ["-Skills", str(skills), "-ClaudeMd", str(claude_md)]
    return subprocess.run(
        befehl,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )


# --- (1) Klon ohne origin -----------------------------------------------------------------


def test_klon_ohne_origin_hinweis_nennt_ursache(tmp_path: Path) -> None:
    klon = _mini_klon(tmp_path, origin=False)
    ziel = tmp_path / "ziel"
    ziel.mkdir()
    daten = skill_stand.stempel(ziel, klon)
    assert daten["commit"] and not daten["quelle"]
    zustand, text = skill_stand.vergleich(ziel)
    assert zustand == skill_stand.UNBEKANNT
    assert "ohne origin" in text, text


# --- (2) kein Python → Abbruch vor dem Kopieren -------------------------------------------


def _pfad_ohne_python(tmp: Path) -> str:
    """PATH mit den Werkzeugen des Installers, aber ohne ``python``/``python3``."""
    werkzeuge = tmp / "werkzeuge"
    werkzeuge.mkdir()
    for name in (
        "bash",
        "dirname",
        "basename",
        "mkdir",
        "date",
        "mv",
        "cp",
        "find",
        "rm",
        "chmod",
        "sed",
        "git",
    ):
        pfad = shutil.which(name)
        if pfad:
            (werkzeuge / name).symlink_to(pfad)
    return str(werkzeuge)


@pytest.mark.parametrize("art", INSTALLER)
def test_ohne_python_abbruch_vor_dem_kopieren(art: str, tmp_path: Path) -> None:
    env = _umgebung(tmp_path)
    env["PATH"] = _pfad_ohne_python(tmp_path)
    env.pop("TO_SPAWN_PY", None)  # auch kein Python des Aufrufers
    skills = tmp_path / "heim" / "skills"
    alt = skills / "to-spawn"
    alt.mkdir(parents=True)
    (alt / "alt.txt").write_text("alter Stand\n", encoding="utf-8")
    lauf = _lauf(art, SKILL, skills, env)
    assert lauf.returncode != 0, lauf.stdout + lauf.stderr
    assert "python" in (lauf.stdout + lauf.stderr).lower()
    assert sorted(p.name for p in alt.iterdir()) == ["alt.txt"], "Zielordner wurde verändert"
    assert not (skills / "_alt").exists(), "alter Stand wurde schon weggeschoben"


# --- (3) Neuinstallation ohne STAND.json-Hinweis ------------------------------------------


@pytest.mark.parametrize("art", INSTALLER)
def test_neuinstallation_meldet_stempel_nicht_als_altlast(art: str, tmp_path: Path) -> None:
    klon, _bare, _sha = _skill_klon(tmp_path)
    env = _umgebung(tmp_path)
    skills = tmp_path / "heim" / "skills"
    erster = _lauf(art, klon, skills, env)
    assert erster.returncode == 0, erster.stdout + erster.stderr
    zweiter = _lauf(art, klon, skills, env)
    assert zweiter.returncode == 0, zweiter.stdout + zweiter.stderr
    assert "Alter Stand gesichert" in zweiter.stdout
    falsch = [z for z in zweiter.stdout.splitlines() if "nur im alten Stand" in z and skill_stand.STEMPEL in z]
    assert not falsch, falsch
    assert (skills / "to-spawn" / skill_stand.STEMPEL).exists()


# --- (4) ungespeicherte Änderungen → dirty ------------------------------------------------


def test_ungespeicherte_aenderung_stempel_dirty_nicht_aktuell(tmp_path: Path) -> None:
    klon = _mini_klon(tmp_path)
    ziel = tmp_path / "ziel"
    ziel.mkdir()
    sauber = skill_stand.stempel(ziel, klon)
    assert sauber["dirty"] == "nein"
    assert skill_stand.vergleich(ziel)[0] == skill_stand.AKTUELL

    (klon / "datei.txt").write_text("geändert\n", encoding="utf-8")
    daten = skill_stand.stempel(ziel, klon)
    assert daten["dirty"] == "ja"
    assert json.loads((ziel / skill_stand.STEMPEL).read_text(encoding="utf-8"))["dirty"] == "ja"
    zustand, text = skill_stand.vergleich(ziel)
    assert zustand != skill_stand.AKTUELL, text
    assert "ungespeichert" in text, text


def test_pfad_ohne_python_hat_wirklich_kein_python(tmp_path: Path) -> None:
    """Selbstprüfung der Attrappe: im Test-PATH findet sich kein Python."""
    pfad = _pfad_ohne_python(tmp_path)
    assert shutil.which("python3", path=pfad) is None and shutil.which("python", path=pfad) is None
    assert os.path.isdir(pfad)
