"""#560 Weg-Test: nach „wartet auf Platz“ starten die wartenden Fenster ohne Eingriff nach.

1. Rechner fremd voll → ``spawn_srv.sh`` öffnet nur ``wache <S>`` und vermerkt ``bau 901`` in der
   Warte-Datei → solange voll, bleibt alles liegen → Platz wird frei → der Nachstarter aus
   ``wache.py`` startet ``bau 901`` im nächsten Takt (Sekunden), die Warte-Datei ist weg.
2. ``wache.py`` stirbt während des Wartens → das Fenster startet es neu, mit derselben Warte-Datei.

Attrappen nur an der Prozess-Grenze wie in ``test_obergrenze_rechner_460_fix1_spawn``
(``/proc``, meminfo, tmux; in 2. der ``wache.py``-Prozess); ``spawn_srv.sh``, ``speicher``,
``sessions_stand`` und der Nachstarter aus ``wache.py`` sind echt.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import test_spec_rand_e2 as e2
from test_obergrenze_rechner_460_fix1_spawn import _spawn

from to_spawn import bash_wahl

SKILL = Path(__file__).resolve().parents[1]


def _wache() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wache_560", SKILL / "skripte" / "wache.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _spawn_fremd_voll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[subprocess.CompletedProcess[str], dict, Path]:
    """Fremd voller Rechner (7 fremde + 2 eigene, Grenze 9); liefert Lauf, Umgebung, Repo."""
    mitschnitt: dict = {}
    echt = bash_wahl.lauf

    def lauf(argumente, **optionen):
        mitschnitt.setdefault("env", dict(optionen["env"]))
        mitschnitt.setdefault("cwd", optionen["cwd"])
        return echt(argumente, **optionen)

    monkeypatch.setattr(bash_wahl, "lauf", lauf)
    ergebnis, _ = _spawn(tmp_path, max_sessions=6, belegt=7, belegt_eigen=2)
    return ergebnis, mitschnitt["env"], Path(mitschnitt["cwd"])


def _gestartet(tmp_path: Path) -> str:
    return (tmp_path / "bin" / "tmux_aufrufe.txt").read_text(encoding="utf-8")


@pytest.mark.braucht("bash", "posix")
def test_fremd_voll_platz_frei_wartende_fenster_starten_ohne_eingriff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lauf, umgebung, repo = _spawn_fremd_voll(tmp_path, monkeypatch)
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0 and "wartet auf Platz: bau 901" in ausgabe, ausgabe
    assert "-n bau 901" not in _gestartet(tmp_path)
    datei = repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}"
    assert datei.read_text(encoding="utf-8").strip() == "901"
    assert f"TO_SPAWN_WARTE_DATEI={datei}" in _gestartet(tmp_path)

    wache = _wache()
    assert wache.NACHSTART_TAKT_S <= 30  # Sekunden nach „Platz frei“, nicht Takt-Minuten
    # Noch voll: ein Takt, nichts startet, die Datei bleibt.
    laeufe = wache.wartende_nachstarten(datei, e2.SPEC, repo, env=umgebung, schlafen=lambda _s: None, max_laeufe=1)
    assert laeufe == 1 and datei.is_file()
    assert "-n bau 901" not in _gestartet(tmp_path)

    # Platz wird frei: die fremden Sessions enden.
    proc = Path(umgebung["TO_SPAWN_SPEICHER_PROC"])
    sessions = Path(umgebung["TO_SPAWN_SPEICHER_SESSIONS"])
    for pid in range(201, 208):
        for teil in (proc / str(pid)).iterdir():
            teil.unlink()
        (proc / str(pid)).rmdir()
        (sessions / f"{pid}.json").unlink()
    geschlafen: list[float] = []
    laeufe = wache.wartende_nachstarten(datei, e2.SPEC, repo, env=umgebung, schlafen=geschlafen.append, max_laeufe=3)
    assert laeufe == 1 and not geschlafen, (laeufe, geschlafen)
    assert "-n bau 901" in _gestartet(tmp_path)
    assert not datei.exists()


_WACHE_ABSTURZ = """
import os, signal, sys
from pathlib import Path
zaehler = Path(sys.argv[0]).with_name("laeufe.txt")
n = int(zaehler.read_text()) + 1 if zaehler.exists() else 1
zaehler.write_text(str(n))
Path(sys.argv[0]).with_name(f"warte_{n}.txt").write_text(os.environ.get("TO_SPAWN_WARTE_DATEI", ""))
if n == 1:
    os.kill(os.getpid(), signal.SIGKILL)  # Absturz mitten im Warten
"""


@pytest.mark.braucht("bash", "posix")
def test_wache_stirbt_beim_warten_fenster_startet_sie_neu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lauf, _umgebung, repo = _spawn_fremd_voll(tmp_path, monkeypatch)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    zeile = next(z for z in _gestartet(tmp_path).splitlines() if f"-n wache {e2.SPEC} " in z)
    befehl = zeile.split(" bash -lc ", 1)[1]
    attrappe = tmp_path / "absturz" / "wache.py"
    attrappe.parent.mkdir()
    attrappe.write_text(_WACHE_ABSTURZ, encoding="utf-8")
    echt = str(SKILL / "skripte" / "wache.py")
    assert echt in befehl, befehl
    fenster = subprocess.run(
        ["bash", "-c", befehl.replace(echt, str(attrappe))],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"},
    )
    assert fenster.returncode == 0, fenster.stderr
    assert (attrappe.parent / "laeufe.txt").read_text() == "2"
    assert "Neustart 1/3" in fenster.stderr, fenster.stderr
    datei = str(repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}")
    assert (attrappe.parent / "warte_2.txt").read_text() == datei
