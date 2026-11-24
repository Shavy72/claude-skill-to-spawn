"""Kleines Paket #320: eine Git-Zeitgrenze, ``git fetch`` mit Zeitgrenze, gh-Ersatz nur im Test.

Echt sind git und die Kind-Prozesse; ``gh`` ist externer Dienst und kommt nur als
Attrappe vor. Der Zeitablauf von ``git fetch`` entsteht echt: ``origin`` ist ein
lokales Repo, dessen ``uploadpack`` erst schläft.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import gh  # noqa: E402

# --- B: eine Zeitgrenze -----------------------------------------------------------


def test_git_zeitgrenzen_nur_in_gh() -> None:
    """``GIT_ZEIT_S``/``FETCH_ZEIT_S`` werden nur in gh.py festgelegt (keine zweite Wahrheit)."""
    muster = re.compile(r"^(GIT_ZEIT_S|FETCH_ZEIT_S)\s*[:=]", re.MULTILINE)
    treffer = sorted(
        p.relative_to(SKILL).as_posix()
        for p in [*SKILL.glob("to_spawn/*.py"), *SKILL.glob("skripte/*.py"), SKILL / "to_spawn.py"]
        if muster.search(p.read_text(encoding="utf-8"))
    )
    assert treffer == ["to_spawn/gh.py"]


def _repo_mit_langsamem_origin(tmp_path: Path) -> Path:
    """Repo mit Commit ``(#5)`` auf ``origin/master``; danach braucht jedes ``git fetch`` ≥ 3 s."""
    repo = tmp_path / "repo"
    ursprung = tmp_path / "ursprung.git"

    def git(*args: str, cwd: Path = repo) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    repo.mkdir()
    git("init", "-q", "--bare", "-b", "master", str(ursprung), cwd=tmp_path)
    git("init", "-q", "-b", "master")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "feat: x (#5)")
    git("remote", "add", "origin", str(ursprung))
    git("push", "-q", "origin", "master")
    git("fetch", "-q", "origin")
    git("config", "remote.origin.uploadpack", "sleep 3; git-upload-pack")
    return repo


@pytest.mark.braucht("posix")
def test_blocker_gruende_fetch_zeitablauf_wartet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hängt ``git fetch``, gibt ``blocker_gruende`` einen Grund statt still auf altem Stand frei."""
    repo = _repo_mit_langsamem_origin(tmp_path)
    monkeypatch.setattr(gh, "FETCH_ZEIT_S", 0.5, raising=False)
    gruende = gh.blocker_gruende(repo, [{"number": 5, "state": "closed", "labels": []}])
    assert len(gruende) == 1
    assert gruende[0].startswith("#5 zu, aber git fetch")


# --- C: gh-Ersatz nur im Test -----------------------------------------------------

_ABFRAGE = "from to_spawn import gh; print(gh.gh_befehl())"


def _gh_befehl_im_kind(tmp_path: Path, *, test_merker: bool) -> subprocess.CompletedProcess[str]:
    """``gh.gh_befehl()`` in einem echten Kind-Prozess; PATH ohne ``gh``."""
    stub = tmp_path / "gh_stub.py"
    stub.write_text("print('[]')\n", encoding="utf-8")
    leer = tmp_path / "leer"
    leer.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_CURRENT_TEST", "TO_SPAWN_TESTLAUF")}
    env.update(TO_SPAWN_GH_STUB=str(stub), PATH=str(leer), PYTHONPATH=str(SKILL))
    if test_merker:
        env["TO_SPAWN_TESTLAUF"] = "1"
    return subprocess.run(
        [sys.executable, "-c", _ABFRAGE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=tmp_path,
        check=False,
    )


def test_gh_stub_ausserhalb_test_ignoriert_und_gemeldet(tmp_path: Path) -> None:
    erg = _gh_befehl_im_kind(tmp_path, test_merker=False)
    assert erg.returncode == 0, erg.stderr
    assert erg.stdout.strip() == "None"
    assert "TO_SPAWN_GH_STUB" in erg.stderr


def test_gh_stub_unter_test_wirkt(tmp_path: Path) -> None:
    erg = _gh_befehl_im_kind(tmp_path, test_merker=True)
    assert erg.returncode == 0, erg.stderr
    assert "gh_stub.py" in erg.stdout
    assert "TO_SPAWN_GH_STUB" not in erg.stderr
