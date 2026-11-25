"""#457: tmux-Session ``spec-<S>`` ohne Repo — zwei Repos mit gleicher Spec-Nummer teilen sich
sonst eine Session (Probe-Lauf 2, 24.11.: Spec #1 in zwei frischen Repos).

Sicherer Kern: ``spawn_srv.sh`` erkennt beim Start, dass die Session ``spec-<S>`` schon einem
anderen Repo gehört (Arbeitsordner der Session, Repo-Kennung = ``origin``-Adresse, ohne
``origin`` der echte Pfad), und verweigert mit Exit 9 — nichts wird in die fremde Session gelegt.
Ein anderer Klon desselben Repos (gleiche ``origin``) und die eigene Session laufen weiter.

Echt: ``spawn_srv.sh``, tmux (eigener Server per ``-L``, nie der Standard-Socket mit den
laufenden Bau-Sessions), git. Keine Attrappe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import test_spec_rand_e2 as e2
from test_einrichtungs_ticket_410 import _aufbau

SPAWN_SH = e2.CLI.parent / "skripte" / "spawn_srv.sh"
SITZUNG = f"spec-{e2.SPEC}"
SITZUNG_NEU = f"spec-{e2.SLUG.split('/')[-1]}-{e2.SPEC}"

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux fehlt")


@pytest.fixture()
def socket() -> Iterator[str]:
    """Eigener tmux-Server je Test; danach beendet (nur dieser Socket)."""
    name = f"t457-{uuid.uuid4().hex[:8]}"
    yield name
    subprocess.run(["tmux", "-L", name, "kill-server"], check=False, capture_output=True)


def _tmux(socket: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["tmux", "-L", socket, *args], check=False, capture_output=True, text=True)


def _git_ordner(ordner: Path, origin: str | None) -> Path:
    ordner.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(ordner)], check=True)
    if origin:
        subprocess.run(["git", "-C", str(ordner), "remote", "add", "origin", origin], check=True)
    return ordner


def _vorhandene_sitzung(socket: str, ordner: Path, name: str = SITZUNG) -> None:
    """Session ``name`` mit Arbeitsordner ``ordner`` — wie sie ein früherer Start anlegt."""
    lauf = _tmux(
        socket,
        "new-session",
        "-d",
        "-s",
        name,
        "-n",
        f"wache {e2.SPEC}",
        "-c",
        str(ordner),
        "sleep 300",
    )
    assert lauf.returncode == 0, lauf.stderr


def _spawn(
    tmp_path: Path, socket: str, *extra: str
) -> subprocess.CompletedProcess[str]:
    """``spawn_srv.sh <S>`` mit einem ``tmux`` im PATH, das nur ``-L socket`` spricht."""
    from to_spawn import bash_wahl

    tmp_path.mkdir(parents=True, exist_ok=True)

    repo, _welt, umgebung = _aufbau(
        tmp_path,
        konfig={
            "hauptzweig": "main",
            "worktree_basis": (tmp_path / "wt").as_posix(),
            "speicher": {"max_sessions": 9, "min_frei_mib": 0, "staffel_s": 0},
            "leitstand": {"aktiv": False},
        },
    )
    bin_ordner = tmp_path / "bin"
    bin_ordner.mkdir()
    huelle = bin_ordner / "tmux"
    huelle.write_text(
        f'#!/usr/bin/env bash\nexec {shutil.which("tmux")!r} -L {socket!r} "$@"\n',
        encoding="utf-8",
    )
    huelle.chmod(0o755)
    umgebung = {k: v for k, v in umgebung.items() if k != "TMUX"}
    umgebung.update(
        {
            "PATH": f"{bin_ordner}{os.pathsep}{umgebung['PATH']}",
            "TO_SPAWN_REPO": str(repo),
            "TO_SPAWN_TEST_BIN": str(bin_ordner),
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    argumente = list(extra) if extra else ["--dry-run"]
    return bash_wahl.lauf(
        [str(SPAWN_SH), str(e2.SPEC), *argumente],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def _fenster(socket: str, name: str = SITZUNG) -> list[str]:
    return _tmux(socket, "list-windows", "-t", f"={name}", "-F", "#W").stdout.splitlines()


@pytest.mark.braucht("bash", "posix")
def test_fremdes_repo_gleiche_spec_wird_verweigert(tmp_path: Path, socket: str) -> None:
    """Ziel-Session gehört einem anderen Repo (gleicher Kurzname) → Exit 9, klare Meldung, nichts angefasst.

    Läuft bewusst OHNE ``--dry-run`` (behebt :131), damit ein fehlender Schutz wirklich ein
    Fenster in die fremde Session legte und die Fenster-Prüfung rot werden kann.
    """
    fremd = _git_ordner(tmp_path / "fremd", "https://github.com/fremd/repo.git")
    _vorhandene_sitzung(socket, fremd, SITZUNG_NEU)
    vorher = _fenster(socket, SITZUNG_NEU)

    lauf = _spawn(tmp_path / "eigen", socket, "--ohne-regularien", "--nur-wache")

    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 9, ausgabe
    assert SITZUNG_NEU in ausgabe and "fremd/repo" in ausgabe and "#457" in ausgabe, ausgabe
    assert "gestartet:" not in ausgabe, ausgabe
    assert _fenster(socket, SITZUNG_NEU) == vorher


@pytest.mark.braucht("bash", "posix")
def test_fremder_ordner_ohne_origin_wird_verweigert(tmp_path: Path, socket: str) -> None:
    """Ohne ``origin`` zählt der gemeinsame Git-Ordner: anderer Ordner = anderes Repo → Exit 9."""
    fremd = _git_ordner(tmp_path / "ohne-origin", None)
    _vorhandene_sitzung(socket, fremd, SITZUNG_NEU)

    lauf = _spawn(tmp_path / "eigen", socket)

    assert lauf.returncode == 9, lauf.stdout + lauf.stderr


@pytest.mark.braucht("bash", "posix")
def test_anderer_klon_desselben_repos_darf_weiter(tmp_path: Path, socket: str) -> None:
    """Gleiche ``origin`` (z. B. Klon ``~/duoplus-<S>``) = dieselbe Spec → kein Verweigern."""
    klon = _git_ordner(tmp_path / "klon", f"https://github.com/{e2.SLUG}.git")
    _vorhandene_sitzung(socket, klon)

    lauf = _spawn(tmp_path / "eigen", socket)

    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "Probelauf — nichts gestartet." in ausgabe, ausgabe


@pytest.mark.braucht("bash", "posix")
def test_ohne_vorhandene_sitzung_unveraendert(tmp_path: Path, socket: str) -> None:
    """Keine Session ``spec-<S>``: Probelauf wie bisher."""
    lauf = _spawn(tmp_path / "eigen", socket)

    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "Probelauf — nichts gestartet." in ausgabe, ausgabe
