"""#262: Aufpasser prüft die Deploy-Wache VOR dem Hausmeister-Lauf.

Entscheidung an einer Stelle (``to_spawn/aufpasser.py``): Läuft ein passender
Deploy-Prozess oder ist die Prozessliste der Deploy-Wache nicht abfragbar
(fail-closed), fasst auch der Hausmeister in diesem Lauf nichts an.

Weg-Test ohne Attrappen für die Wache: echter laufender Prozess, dessen
Befehlszeile auf ``--deploy-muster`` passt, echter aufräumbarer Worktree
(geschlossenes Ticket, > 24 h alt) und echter ``Aufpasser``-Lauf.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_hausmeister_1369 import Welt, _platte, welt  # noqa: F401 — Fixture-Import

from to_spawn import aufpasser, prozessbaum

WINDOWS = sys.platform == "win32"


def _marke() -> str:
    return f"wachehausmeister262_{os.getpid()}_{uuid.uuid4().hex[:8]}"


def _beenden(prozess: subprocess.Popen[bytes]) -> None:
    for pid in reversed(prozessbaum.baum(prozess.pid)):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    prozess.wait(timeout=30)


def _bis_sichtbar(pid: int, muster: str) -> list[tuple[int, str]]:
    treffer: list[tuple[int, str]] = []
    for _ in range(100):
        treffer = prozessbaum.passende(muster)
        if any(p == pid for p, _ in treffer):
            return treffer
        time.sleep(0.05)
    return treffer


@pytest.fixture
def deploy_prozess(tmp_path: Path) -> Iterator[tuple[subprocess.Popen[bytes], str]]:
    """Echter Prozess ``python <marke>.py`` — stellt ein laufendes Deploy-Skript nach."""
    marke = _marke()
    skript = tmp_path / f"{marke}.py"
    skript.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    prozess = subprocess.Popen([sys.executable, str(skript)])
    try:
        yield prozess, marke
    finally:
        _beenden(prozess)


def test_laufender_deploy_sperrt_hausmeister_und_ende_gibt_frei(
    welt: Welt,  # noqa: F811
    deploy_prozess: tuple[subprocess.Popen[bytes], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Weg-Test #262: laufender passender Prozess → Hausmeister fasst nichts an;
    erst nach dessen Ende räumt der nächste Aufpasser-Lauf den Worktree auf."""
    prozess, marke = deploy_prozess
    muster = re.escape(marke)
    assert _bis_sichtbar(prozess.pid, muster), "Vorbedingung: Deploy-Prozess sichtbar"

    alt = welt.worktree(62)
    welt.ticket(62, "CLOSED", 48)
    _platte(welt, monkeypatch, 0.50)

    zustand = welt.tmp / "zustand"
    e = aufpasser.Einstellungen(
        zustand=zustand,
        tmux_socket=f"wache262-{os.getpid()}",
        deploy_muster=muster,
        trocken=False,
    )

    assert aufpasser.lauf_mit_einstellungen(e) == 0
    assert alt.is_dir(), "Deploy lief — Hausmeister durfte den Worktree nicht löschen"
    assert not welt.log.exists(), "Deploy lief — Hausmeister durfte keinen Lauf loggen"
    log_text = (zustand / "aufpasser.log").read_text(encoding="utf-8")
    assert "Deploy/Gate läuft" in log_text and marke in log_text, log_text
    assert "Hausmeister:" not in log_text, log_text

    # Nach Ende des Deploy-Prozesses räumt der nächste Lauf normal auf.
    _beenden(prozess)
    assert prozessbaum.passende(muster) == []
    assert aufpasser.lauf_mit_einstellungen(e) == 0
    assert not alt.exists(), "Ohne laufenden Deploy muss der Hausmeister den Worktree räumen"
    log_danach = (zustand / "aufpasser.log").read_text(encoding="utf-8")
    assert re.search(r"Hausmeister: 1 gelöscht, 0 bleibt", log_danach), log_danach


def test_gestoerte_deploy_wache_sperrt_hausmeister(
    welt: Welt,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed #262: ist die Deploy-Wache nicht abfragbar, läuft auch der Hausmeister nicht."""
    alt = welt.worktree(63)
    welt.ticket(63, "CLOSED", 48)
    _platte(welt, monkeypatch, 0.50)

    if WINDOWS:

        def kaputt() -> dict[int, tuple[int, int]]:
            raise OSError("NtQuerySystemInformation: Status 0xC0000005")

        monkeypatch.setattr(prozessbaum, "_momentaufnahme_windows", kaputt)
    else:
        monkeypatch.setenv("PATH", str(welt.tmp / "leer-path"))
        (welt.tmp / "leer-path").mkdir()

    zustand = welt.tmp / "zustand-stoerung"
    e = aufpasser.Einstellungen(
        zustand=zustand,
        tmux_socket=f"wache262-stoer-{os.getpid()}",
        deploy_muster=re.escape(_marke()),
        trocken=False,
    )

    assert aufpasser.lauf_mit_einstellungen(e) == 1
    assert alt.is_dir(), "Gestörte Wache (fail-closed) — Hausmeister durfte nichts anfassen"
    assert not welt.log.exists(), "Gestörte Wache — Hausmeister durfte nicht laufen"
    log_text = (zustand / "aufpasser.log").read_text(encoding="utf-8")
    assert "Lauf abgebrochen, nichts angefasst: Deploy-Wache:" in log_text, log_text
    assert "Hausmeister:" not in log_text, log_text


def test_repo_ordner_nutzt_pane_start_path_als_rueckfall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#262-Nachzug: Fehlt ``pane_current_path`` direkt nach ``tmux new-session -d``
    oder zeigt auf einen Fremdordner, findet ``repo_ordner`` das Repo über ``pane_start_path``."""
    repo = tmp_path / "repo"
    (repo / ".to-spawn").mkdir(parents=True)
    (repo / ".to-spawn" / "config.json").write_text("{}", encoding="utf-8")
    fremd = tmp_path / "fremd"
    fremd.mkdir()

    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "zustand"))

    # Fall 1: pane_current_path leer (Start-Rennen direkt nach new-session -d).
    monkeypatch.setattr(a, "tmux", lambda *_a, **_k: f"0\thalter\t\t1234\t@0\t%0\t{repo}\n")
    fenster_leer = a.fenster("spec-9262", "9262")
    assert len(fenster_leer) == 1
    assert fenster_leer[0].pfad == str(repo)
    assert a.repo_ordner(fenster_leer) == repo

    # Fall 2: pane_current_path im Fremdordner (vor chdir oder nach cd weg), start_pfad im Repo.
    monkeypatch.setattr(a, "tmux", lambda *_a, **_k: f"0\thalter\t{fremd}\t1234\t@0\t%0\t{repo}\n")
    fenster_fremd = a.fenster("spec-9262", "9262")
    assert a.repo_ordner(fenster_fremd) == repo


def test_echtes_tmux_findet_repo_trotz_weggewechseltem_pane_pfad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weg-Test mit echtem tmux: selbst wenn ``pane_current_path`` leer ist oder auf
    ``/`` steht, liefert ``Aufpasser.repo_ordner`` über ``pane_start_path`` das Repo."""
    from conftest import braucht_fehlt

    if grund := braucht_fehlt("tmux"):
        pytest.skip(f"braucht tmux: {grund}")

    repo = tmp_path / "repo"
    (repo / ".to-spawn").mkdir(parents=True)
    (repo / ".to-spawn" / "config.json").write_text("{}", encoding="utf-8")

    socket = f"wache262-pfad-{os.getpid()}"
    sitzung = "spec-9262"
    monkeypatch.delenv("TMUX", raising=False)
    subprocess.run(
        [
            "tmux",
            "-L",
            socket,
            "new-session",
            "-d",
            "-s",
            sitzung,
            "-n",
            "halter",
            "-c",
            str(repo),
            "cd / && exec sleep 30",
        ],
        check=True,
    )
    try:
        a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "zustand", tmux_socket=socket))
        fenster = a.fenster(sitzung, "9262")
        assert a.repo_ordner(fenster) == repo
    finally:
        subprocess.run(["tmux", "-L", socket, "kill-server"], check=False)
