"""Werkzeug-Tests laufen nie in der Prüfung einer App (#425 Abnahme 4, #415).

Echtes kleines App-Repo in ``tmp`` (git, eine grüne App-Testdatei) plus absichtlich ROTE Tests des
Bau-Werkzeugs dort, wo es im App-Repo liegt (``.to-spawn/…``, ``.claude/skills/…``). ``pruefen``
läuft als echter Prozess-Weg, beide Auswahl-Fälle:

- **auswahl**: gezielte Auswahl über pytest-impacted. Braucht ``TO_SPAWN_PROBE`` mit ``.venv``
  (pytest + pytest-impacted, siehe ``messung/README.md``), sonst übersprungen.
- **voll**: Rückfall auf den vollen Lauf (Prüf-Konfig geändert → R3), läuft überall.

Je Fall zwei App-Konfigurationen: pytest-Vorgabe und eine eigene ``norecursedirs`` der App (die
ersetzt pytests Vorgabe ``.*`` und würde versteckte Ordner sonst wieder einsammeln).
Erwartung: Prüfung grün, kein Werkzeug-Test eingesammelt (``--collect-only`` desselben Befehls).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from to_spawn import pruef

PROBE = Path(os.environ["TO_SPAWN_PROBE"]) if os.environ.get("TO_SPAWN_PROBE") else None

#: Rote Werkzeug-Tests an den Orten, an denen das Bau-Werkzeug im App-Repo liegt.
WERKZEUG_ROT = (
    ".to-spawn/werkzeug/tests/test_werkzeug_rot.py",
    ".claude/skills/to-spawn/tests/test_skill_rot.py",
)
ROT = "def test_rot() -> None:\n    raise AssertionError('Werkzeug-Test darf nie in der App-Prüfung laufen')\n"

KONFIG = {
    "vorgabe": '[project]\nname = "app"\nversion = "0"\n',
    "eigene_norecursedirs": (
        '[project]\nname = "app"\nversion = "0"\n\n[tool.pytest.ini_options]\nnorecursedirs = ["build"]\n'
    ),
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _app_repo(ordner: Path, konfig: str, mit_venv: bool) -> Path:
    repo = ordner / "app_repo"
    (repo / "app").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pyproject.toml").write_text(KONFIG[konfig], encoding="utf-8")
    (repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "app" / "rechne.py").write_text(
        "def plus(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
    )
    (repo / "tests" / "test_rechne.py").write_text(
        "from app.rechne import plus\n\n\ndef test_plus() -> None:\n    assert plus(2, 3) == 5\n",
        encoding="utf-8",
    )
    for pfad in WERKZEUG_ROT:
        datei = repo / pfad
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_text(ROT, encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "probe@example.org")
    _git(repo, "config", "user.name", "probe")
    if mit_venv:
        assert PROBE is not None
        (repo / ".venv").symlink_to(PROBE / ".venv", target_is_directory=True)
    with (repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as datei:
        datei.write("/.venv\n/.to-spawn/pruef/\n__pycache__/\n.pytest_cache/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "basis")
    _git(repo, "tag", "base")
    return repo


@pytest.mark.parametrize("konfig", sorted(KONFIG))
@pytest.mark.parametrize(
    "weg",
    [
        pytest.param(
            "auswahl",
            marks=pytest.mark.skipif(
                PROBE is None,
                reason="TO_SPAWN_PROBE (mit pytest-impacted) nicht gesetzt",
            ),
        ),
        "voll",
    ],
)
def test_werkzeug_tests_nie_in_app_pruefung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, weg: str, konfig: str
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = _app_repo(tmp_path, konfig, mit_venv=weg == "auswahl")
    if weg == "auswahl":
        datei = repo / "app" / "rechne.py"
        datei.write_text(
            datei.read_text(encoding="utf-8") + "\n\nNULL = 0\n", encoding="utf-8"
        )
    else:
        datei = repo / "pyproject.toml"
        datei.write_text(
            datei.read_text(encoding="utf-8") + "\n# Prüf-Konfig geändert\n",
            encoding="utf-8",
        )
    _git(repo, "commit", "-qam", "änderung")

    urteil = pruef.pruefen(repo, "base", "HEAD")

    stufe = next(s for s in urteil.zeile["stufen"] if s["name"] in ("auswahl", "voll"))
    assert stufe["name"] == weg, stufe["grund"]
    assert urteil.exit == 0, urteil.zeile["fehler_kurz"]
    gesammelt = subprocess.run(
        [*stufe["befehl"].split(), "--collect-only"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert "tests/test_rechne.py::test_plus" in gesammelt
    for pfad in WERKZEUG_ROT:
        assert pfad not in gesammelt, f"Werkzeug-Test eingesammelt: {pfad}"
