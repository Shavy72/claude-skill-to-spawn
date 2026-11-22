"""#251: Deploy-Wache fragt laufende Prozesse über eine Tür (``prozessbaum.passende``).

Unter Windows gibt es kein ``pgrep``; der Aufpasser-Lauf brach dort immer mit
„Deploy-Wache: pgrep nicht nutzbar“ ab. Jetzt liest die Tür unter Windows die
Kernel-Prozessliste, unter Linux weiter ``pgrep -af``.

Weg ohne Attrappe: echte Prozesse, deren Kommandozeile auf das Muster passt, echter
Aufpasser-Lauf. Nur der Störfall „Abfrage scheitert“ braucht unter Windows eine
Attrappe für den Kernel-Aufruf (er lässt sich nicht echt kaputt machen); unter Linux
fehlt ``pgrep`` wirklich (leerer ``PATH``). Jeder Test beendet seine Prozesse selbst.
"""

from __future__ import annotations

import logging
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

from to_spawn import prozessbaum
from to_spawn.aufpasser import Aufpasser, Einstellungen

REPO = Path(__file__).resolve().parents[1]
WINDOWS = sys.platform == "win32"


def _marke() -> str:
    """Eindeutiger Name, der auf keine fremde Befehlszeile passt."""
    return f"deploywache251_{os.getpid()}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def ruhiger_hausmeister(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hausmeister des Laufs sieht nur leere Ordner (keine Repos, keine Wurzeln dieses Rechners)."""
    leer = tmp_path / "leer"
    leer.mkdir()
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", "")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", "")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", str(leer))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(tmp_path / "hausmeister.jsonl"))


@pytest.fixture
def deploy_prozess(tmp_path: Path) -> Iterator[tuple[subprocess.Popen[bytes], str]]:
    """Echter Prozess ``python <marke>.py`` — so sieht ein laufendes Deploy-Skript aus."""
    marke = _marke()
    skript = tmp_path / f"{marke}.py"
    skript.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    prozess = subprocess.Popen([sys.executable, str(skript)])
    try:
        yield prozess, marke
    finally:
        _beenden(prozess)


def _beenden(prozess: subprocess.Popen[bytes]) -> None:
    """Prozess samt Kindern beenden (ein venv-``python.exe`` unter Windows startet ein Kind)."""
    for pid in reversed(prozessbaum.baum(prozess.pid)):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    prozess.wait(timeout=30)


def _wache(tmp_path: Path, muster: str, **kw: object) -> Aufpasser:
    e = Einstellungen(
        zustand=tmp_path / "zustand",
        tmux_socket=f"deploywache251-{os.getpid()}",
        deploy_muster=muster,
        **kw,  # type: ignore[arg-type]
    )
    a = Aufpasser(e)
    a.muster = a.deploy_muster_fuer(set())
    return a


def _bis_sichtbar(pid: int, muster: str) -> list[tuple[int, str]]:
    """Der frisch gestartete Prozess braucht einen Moment, bis seine Kommandozeile lesbar ist."""
    treffer: list[tuple[int, str]] = []
    for _ in range(100):
        treffer = prozessbaum.passende(muster)
        if any(p == pid for p, _ in treffer):
            return treffer
        time.sleep(0.05)
    return treffer


def test_echter_prozess_passt_wache_meldet_laeuft(tmp_path: Path, deploy_prozess) -> None:
    prozess, marke = deploy_prozess
    muster = re.escape(marke)
    treffer = _bis_sichtbar(prozess.pid, muster)
    # Nur der Prozess selbst (und ggf. sein Kind: venv-Starter unter Windows) — nichts Fremdes.
    assert prozess.pid in dict(treffer), treffer
    assert set(dict(treffer)) <= set(prozessbaum.baum(prozess.pid)), treffer
    assert all(marke in zeile for _, zeile in treffer)
    assert any(Path(teil).stem == marke for teil in prozessbaum.argv(prozess.pid)[:3])

    laeuft = _wache(tmp_path, muster).deploy_laeuft(muster)
    assert laeuft and all(marke in kopf for kopf in laeuft), laeuft


def test_ohne_prozess_ist_die_wache_frei(tmp_path: Path, deploy_prozess) -> None:
    prozess, marke = deploy_prozess
    muster = re.escape(marke)
    assert _bis_sichtbar(prozess.pid, muster), "Vorbedingung: Prozess sichtbar"
    _beenden(prozess)
    assert prozessbaum.passende(muster) == []
    assert _wache(tmp_path, muster).deploy_laeuft(muster) == []


def test_eigener_prozess_und_vorfahren_zaehlen_nie() -> None:
    """Ein Prozess, dessen eigene Befehlszeile das Muster enthält, findet sich selbst nicht."""
    marke = _marke()
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from to_spawn import prozessbaum; "
        "print([p for p, _ in prozessbaum.passende(sys.argv[2])])"
    )
    fertig = subprocess.run(
        [sys.executable, "-c", code, str(REPO), re.escape(marke)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert fertig.stdout.strip() == "[]", fertig.stdout + fertig.stderr


def test_lauf_wartet_solange_ein_passender_prozess_laeuft(
    tmp_path: Path, deploy_prozess, ruhiger_hausmeister, caplog: pytest.LogCaptureFixture
) -> None:
    prozess, marke = deploy_prozess
    muster = re.escape(marke)
    assert _bis_sichtbar(prozess.pid, muster), "Vorbedingung: Prozess sichtbar"
    caplog.set_level(logging.INFO)
    assert _wache(tmp_path, muster, trocken=True).lauf() == 0
    assert "Deploy/Gate läuft" in caplog.text and marke in caplog.text
    assert "Lauf abgebrochen" not in caplog.text


def test_abfrage_scheitert_lauf_bricht_ab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ruhiger_hausmeister, caplog: pytest.LogCaptureFixture
) -> None:
    if WINDOWS:

        def kaputt() -> dict[int, tuple[int, int]]:
            raise OSError("NtQuerySystemInformation: Status 0xC0000005")

        monkeypatch.setattr(prozessbaum, "_momentaufnahme_windows", kaputt)
    else:
        monkeypatch.setenv("PATH", str(tmp_path))  # pgrep fehlt wirklich
    muster = re.escape(_marke())
    with pytest.raises(prozessbaum.ProzesslisteFehler, match="nicht nutzbar"):
        prozessbaum.passende(muster)

    caplog.set_level(logging.INFO)
    assert _wache(tmp_path, muster, trocken=True).lauf() == 1
    assert "Lauf abgebrochen, nichts angefasst: Deploy-Wache:" in caplog.text
    assert "nicht nutzbar" in caplog.text
