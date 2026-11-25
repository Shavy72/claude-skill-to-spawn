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
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from to_spawn import prozessbaum
from to_spawn.aufpasser import Aufpasser, Einstellungen, deploy_art

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


# --- Fix-Runde 1: Programmname plattformfest (Live-Tür per ``ssh.exe``) ---------------


def _ssh_programm() -> Path | None:
    r"""Das ``ssh`` der Live-Tür: unter Windows das System-OpenSSH (``…\OpenSSH\ssh.exe``,
    dessen Kinder hängen sauber im Prozessbaum), unter Linux ``ssh`` aus ``PATH``."""
    if WINDOWS:
        pfad = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / "ssh.exe"
        return pfad if pfad.is_file() else None
    gefunden = shutil.which("ssh")
    return Path(gefunden) if gefunden else None


@pytest.fixture
def live_tuer_prozess(tmp_path: Path) -> Iterator[tuple[subprocess.Popen[bytes], str]]:
    """Echter ``ssh``-Prozess mit der Befehlszeile der Live-Tür, ganz ohne Netz.

    ``ProxyCommand`` ist ein Python-Skript, das nur schläft: ssh öffnet keinen Socket,
    löst keinen Namen auf (``beispiel-host`` ist ein Platzhalter) und wartet auf das
    Begrüßungsbanner, das nie kommt. ``-F none`` hält fremde ssh-Konfig fern.
    """
    ssh = _ssh_programm()
    if ssh is None:
        pytest.skip("kein ssh-Programm auf diesem Rechner")
    marke = _marke()
    schlaf_marke = _marke()  # eigene Marke: das Proxy-Kind darf nie selbst als Treffer zählen
    schlaf = tmp_path / f"{schlaf_marke}.py"
    schlaf.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    proxy = f'"{Path(sys.executable).as_posix()}" "{schlaf.as_posix()}"'
    prozess = subprocess.Popen(
        [
            str(ssh),
            "-F",
            "none",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            "-o",
            f"ProxyCommand={proxy}",
            "beispiel-host",
            f"bash /opt/beispiel/scripts/server_tuer.sh live {marke}",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield prozess, marke
    finally:
        _beenden(prozess)
        for pid, _ in prozessbaum.passende(re.escape(schlaf_marke)):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


def test_live_tuer_per_echtem_ssh_programm_sperrt(tmp_path: Path, live_tuer_prozess) -> None:
    r"""Rot auf 2b496da unter Windows: ``…\OpenSSH\ssh.exe`` hieß nicht ``ssh``, die
    Wache meldete „frei“, obwohl die Live-Tür lief."""
    prozess, marke = live_tuer_prozess
    muster = re.escape(marke)
    treffer = _bis_sichtbar(prozess.pid, muster)
    assert prozess.pid in dict(treffer), treffer
    argv = prozessbaum.argv(prozess.pid)
    if WINDOWS:
        assert argv[0].lower().endswith("ssh.exe"), argv
    assert "server_tuer.sh" not in " ".join(argv[:3])  # nur der ssh-Zweig kann ihn finden
    assert deploy_art(argv) == "live", argv
    laeuft = _wache(tmp_path, muster).deploy_laeuft(muster)
    assert any(kopf.startswith(argv[0]) for kopf in laeuft), laeuft


@pytest.mark.parametrize(
    ("roh", "erwartet"),
    [
        (r"C:\Windows\System32\OpenSSH\ssh.exe", "ssh"),
        ("SSH.EXE", "ssh"),
        (r"C:\Program Files\Git\usr\bin\ssh.EXE", "ssh"),
        (r'"C:\Program Files\Git\bin\bash.exe"', "bash"),
        ("C:/tools/Python313/python.exe", "python"),
        (r"C:\npm\claude.cmd", "claude"),
        ("Pytest.BAT", "pytest"),
        ("ssh", "ssh"),
        ("ssh.exe.bak", "ssh.exe.bak"),
    ],
)
def test_programm_name_unter_windows(roh: str, erwartet: str) -> None:
    assert prozessbaum.programm_name(roh, windows=True) == erwartet


@pytest.mark.parametrize(
    ("roh", "erwartet"),
    [
        ("/usr/bin/ssh", "ssh"),
        ("ssh", "ssh"),
        ("-bash", "-bash"),
        ("SSH", "SSH"),  # Linux bleibt genau: Groß/Klein zählt, Endungen bleiben
        ("/opt/x/ssh.exe", "ssh.exe"),
    ],
)
def test_programm_name_unter_linux_unveraendert(roh: str, erwartet: str) -> None:
    assert prozessbaum.programm_name(roh, windows=False) == erwartet


@pytest.mark.skipif(
    not WINDOWS, reason="Windows-Pfade zerlegt nur Windows so; Linux deckt test_programm_name_unter_linux_unveraendert"
)
def test_alle_namensvergleiche_im_aufpasser_kennen_windows_namen() -> None:
    from to_spawn import aufpasser

    tuer = [r"C:\Windows\System32\OpenSSH\SSH.EXE", "-o", "BatchMode=yes", "h", "bash /x/server_tuer.sh live"]
    assert deploy_art(tuer) == "live"
    assert deploy_art([r"C:\venv\Scripts\PYTEST.EXE", "-q"]) == "pruef"
    assert aufpasser.session_id_aus_argv([r"C:\npm\claude.cmd", "--resume", "abc"]) == "abc"
