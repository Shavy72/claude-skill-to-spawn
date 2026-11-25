"""Gegenprobe zu #283: Start aus veraltetem Arbeitsordner, mit echtem git.

Aufbau je Test: lokales bare-Repo als ``origin``, ein Klon (der Arbeitsordner) und eine zweite
Kopie, die N Commits nachschiebt — kein Netz, kein GitHub. Die ``spawn_local.ps1``-Läufe nehmen
eine Kopie des Starters in einem Wegwerf-Skill-Ordner: dort reicht ``to_spawn.py`` nur
``arbeitsstand`` an den echten ``to_spawn.py`` weiter, alles andere beantwortet eine Attrappe
(Regularien ok, nichts zu bauen). Eine ``wt``-Attrappe vorn im PATH hält fest, falls je ein
Fenster aufginge.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from to_spawn import arbeitsstand

SKILL = Path(__file__).resolve().parent.parent
N = 3
PS = shutil.which("pwsh")

# Weiterleitung: `arbeitsstand` an den echten to_spawn.py, sonst Attrappe ohne Ausgabe (Exit 0).
WEITERLEITUNG = f"""
import subprocess, sys

if __name__ == "__main__":
    if sys.argv[1:2] == ["arbeitsstand"]:
        sys.exit(subprocess.call([sys.executable, {str(SKILL / "to_spawn.py")!r}, *sys.argv[1:]]))
    sys.exit(0)
"""


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=ort,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _commit(ort: Path, inhalt: str) -> None:
    datei = ort / "stand.txt"
    datei.write_text(inhalt, encoding="utf-8")
    _git(ort, "add", ".")
    _git(ort, "commit", "-q", "-m", inhalt)


@pytest.fixture
def klon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Arbeitsordner, der ``N`` Commits hinter ``origin/main`` liegt (sauberer Baum)."""
    monkeypatch.delenv(arbeitsstand.TEST_OK_ENV, raising=False)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    quelle = tmp_path / "quelle"
    _git(tmp_path, "clone", "-q", str(remote), str(quelle))
    _commit(quelle, "alt")
    _git(quelle, "push", "-q", "origin", "HEAD:main")
    ort = tmp_path / "app"
    _git(tmp_path, "clone", "-q", str(remote), str(ort))
    for i in range(N):
        _commit(quelle, f"neu {i}")
    _git(quelle, "push", "-q", "origin", "HEAD:main")
    return ort


def _spawn_local(klon: Path, tmp_path: Path, *schalter: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    skill = tmp_path / "skill"
    (skill / "skripte").mkdir(parents=True)
    shutil.copy(SKILL / "spawn_local.ps1", skill / "spawn_local.ps1")
    (skill / "to_spawn.py").write_text(WEITERLEITUNG, encoding="utf-8")
    (skill / "skripte" / "bau.py").write_text("", encoding="utf-8")
    attrappen_bin = tmp_path / "bin"
    attrappen_bin.mkdir()
    wt_marke = tmp_path / "wt_aufgerufen.txt"
    (attrappen_bin / "wt.cmd").write_text(f'@echo %* > "{wt_marke}"\r\n', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != arbeitsstand.TEST_OK_ENV}
    env["PATH"] = str(attrappen_bin) + os.pathsep + env.get("PATH", "")
    env["TO_SPAWN_REPO"] = str(klon)
    env["PYTHONIOENCODING"] = "utf-8"
    lauf = subprocess.run(
        [
            str(PS),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(skill / "spawn_local.ps1"),
            "-Spec",
            "48",
            "-Tickets",
            "51",
            "-OhneWache",
            *schalter,
        ],
        cwd=klon,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    return lauf, wt_marke


nur_windows_pwsh = pytest.mark.skipif(
    sys.platform != "win32" or PS is None, reason="spawn_local.ps1-Lauf nur unter Windows mit pwsh"
)


@nur_windows_pwsh
def test_spawn_local_trockenlauf_nennt_veraltet_und_zahl(klon: Path, tmp_path: Path) -> None:
    lauf, wt_marke = _spawn_local(klon, tmp_path, "-DryRun")
    ausgabe = lauf.stdout + lauf.stderr
    assert "veraltet" in ausgabe and f"{N} Commits hinter origin/main" in ausgabe, ausgabe
    assert not wt_marke.exists(), "Trockenlauf darf kein Fenster öffnen"
    assert _git(klon, "rev-list", "--count", "HEAD..origin/main") == str(N)


def test_frischer_klon_ohne_origin_head_zieht_beim_ersten_start_nach(klon: Path) -> None:
    # Wie ein Klon, dessen Remote-Refs erst der erste fetch anlegt: kein origin/HEAD, kein origin/main.
    _git(klon, "remote", "set-head", "origin", "-d")
    _git(klon, "update-ref", "-d", "refs/remotes/origin/main")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.NACHGEZOGEN, stand.text
    assert _git(klon, "rev-list", "--count", "HEAD..origin/main") == "0"


@nur_windows_pwsh
def test_spawn_local_echter_start_zieht_sauberen_klon_nach(klon: Path, tmp_path: Path) -> None:
    # Ohne -DryRun: arbeitsstand zieht nach; danach endet der Starter mit „Nichts zu starten.“ vor wt.
    lauf, wt_marke = _spawn_local(klon, tmp_path)
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "nachgezogen" in ausgabe, ausgabe
    assert _git(klon, "rev-list", "--count", "HEAD..origin/main") == "0"
    assert not wt_marke.exists(), "darf kein Fenster öffnen"


def test_ohne_netz_hinweis_nennt_alter_des_letzten_fetch(klon: Path, tmp_path: Path) -> None:
    assert arbeitsstand.sicherstellen(klon, nachziehen=False).zustand == arbeitsstand.VERALTET
    fetch_head = Path(_git(klon, "rev-parse", "--absolute-git-dir")) / "FETCH_HEAD"
    zwei_std = time.time() - 2 * 3600 - 60
    os.utime(fetch_head, (zwei_std, zwei_std))
    _git(klon, "remote", "set-url", "origin", str(tmp_path / "gibt-es-nicht.git"))
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.HINWEIS and stand.ok, stand.text
    assert "letzter erfolgreicher fetch vor 2 Std." in stand.text, stand.text


def test_ohne_netz_und_ohne_fetch_bisher_sagt_das(klon: Path, tmp_path: Path) -> None:
    _git(klon, "remote", "set-url", "origin", str(tmp_path / "gibt-es-nicht.git"))
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.HINWEIS and stand.ok, stand.text
    assert "letzter erfolgreicher fetch unbekannt" in stand.text, stand.text
