"""#277: Startzeile für Ablösung und Folge-Runde läuft ohne Maschinen-Einstellungen.

Ein Spec-Klon hat keine ``.venv``, und die Shell-Funktion ``bau`` aus ``~/.bashrc``
gibt es nicht auf jedem Rechner. Die Startzeile aus ``capo.bau_startzeile`` muss
darum ``bau.py`` des Skills direkt aufrufen — echter Lauf in ``bash -lc`` mit leerem
``HOME`` (kein ``.bashrc``, keine Funktion ``bau``), Schalter ``--help`` statt Bau.
Stirbt ``bau.py``, steht der Exit-Code in ``.to-spawn/fenster_<N>.log``.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from to_spawn import capo, respawn

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="tmux-Startzeile läuft nur unter Linux-bash",
)

TICKET = 7


def _klon(tmp_path: Path) -> Path:
    """Spec-Klon wie ``~/<repo>-<S>``: ohne ``.venv``, ohne ``scripts/bau.py``."""
    repo = tmp_path / "repo-9"
    repo.mkdir()
    return repo


def _bash(zeile: str, repo: Path, home: Path) -> subprocess.CompletedProcess[str]:
    home.mkdir(exist_ok=True)
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
    }
    return subprocess.run(
        ["bash", "-lc", zeile],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _laeuft_ohne_maschine(repo: Path, home: Path, *schalter: str) -> None:
    """Dieselbe Startzeile mit ``--help``: erreicht ``bau.py`` ohne ``.bashrc``/``.venv``."""
    lauf = _bash(capo.bau_startzeile(repo, TICKET, "", *schalter, "--help"), repo, home)
    assert lauf.returncode == 0, lauf.stderr
    assert "usage" in lauf.stdout.lower()


def test_folge_runde_startet_bau_py_ohne_bashrc_und_venv(tmp_path: Path) -> None:
    repo = _klon(tmp_path)
    assert capo.folge_befehl(repo, 9, TICKET, ["wache 9"])[-1] == capo.bau_startzeile(
        repo, TICKET, ""
    )
    _laeuft_ohne_maschine(repo, tmp_path / "home")


def test_abloesung_startet_bau_py_ohne_bashrc_und_venv(tmp_path: Path) -> None:
    repo = _klon(tmp_path)
    argv = shlex.split(respawn._startbefehl(repo, TICKET))
    assert argv == [
        "bash",
        "-lc",
        capo.bau_startzeile(repo, TICKET, "", "--ohne-prompt"),
    ]
    _laeuft_ohne_maschine(repo, tmp_path / "home", "--ohne-prompt")


def test_startzeile_nennt_skill_bau_py_und_repo(tmp_path: Path) -> None:
    repo = _klon(tmp_path)
    zeile = capo.bau_startzeile(repo, TICKET, "", "--ohne-prompt")
    skill_bau = Path(capo.__file__).resolve().parent.parent / "skripte" / "bau.py"
    assert str(skill_bau) in zeile
    assert f"TO_SPAWN_REPO={repo}" in zeile
    assert ".venv" not in zeile and "bau 7" not in zeile


def test_gestorbenes_fenster_hinterlaesst_exit_code(tmp_path: Path) -> None:
    repo = _klon(tmp_path)
    zeile = capo.bau_startzeile(repo, TICKET, "", "--gibt-es-nicht")
    lauf = _bash(zeile, repo, tmp_path / "home")
    assert lauf.returncode == 2
    log = repo / ".to-spawn" / f"fenster_{TICKET}.log"
    assert log.is_file()
    assert f"bau.py #{TICKET} Exit 2" in log.read_text(encoding="utf-8")


_SCHEITERT = "import sys\nprint('Absicht: Manifest fehlt', file=sys.stderr)\nsys.exit(3)\n"


def _attrappe_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str = _SCHEITERT
) -> None:
    """Skill-Attrappe, deren ``bau.py`` (Standard: mit Meldung auf stderr) scheitert."""
    skill = tmp_path / "skill"
    (skill / "skripte").mkdir(parents=True)
    (skill / "skripte" / "bau.py").write_text(code, encoding="utf-8")
    monkeypatch.setattr(capo, "_SKILL", skill)


def test_gestorbenes_fenster_hinterlaesst_fehlermeldung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Befund PR 293: stderr von ``bau.py`` steht im Fenster-Log, nicht nur der Exit-Code."""
    _attrappe_skill(tmp_path, monkeypatch)
    repo = _klon(tmp_path)
    lauf = _bash(capo.bau_startzeile(repo, TICKET, "", "--ohne-prompt"), repo, tmp_path / "home")
    assert lauf.returncode == 3
    text = (repo / ".to-spawn" / f"fenster_{TICKET}.log").read_text(encoding="utf-8")
    assert "Absicht: Manifest fehlt" in text
    assert f"bau.py #{TICKET} Exit 3" in text
    assert text.index("Absicht: Manifest fehlt") < text.index(f"bau.py #{TICKET} Exit 3")
    assert "Absicht: Manifest fehlt" in lauf.stderr  # Fenster sieht die Meldung weiter


#: ``bau.py``-Attrappe: hinterlässt ein Rest-Kind, das die Fehlerausgabe erbt und offen hält.
_REST_KIND = (
    "import subprocess, sys\n"
    "kind = subprocess.Popen(['sleep', '60'], stdout=subprocess.DEVNULL)\n"
    "open('rest_kind.pid', 'w').write(str(kind.pid))\n"
    "print('Absicht: Rest-Kind bleibt', file=sys.stderr)\n"
    "sys.exit(4)\n"
)


def test_rest_kind_haelt_fenster_nicht_offen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review PR 293: hält ein Rest-Kind von ``bau.py`` stderr offen, endet das Fenster
    trotzdem binnen weniger Sekunden, und die Exit-Zeile steht im Log."""
    _attrappe_skill(tmp_path, monkeypatch, _REST_KIND)
    repo = _klon(tmp_path)
    (tmp_path / "home").mkdir()
    env = {"HOME": str(tmp_path / "home"), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    zeile = capo.bau_startzeile(repo, TICKET, "", "--ohne-prompt")
    # stderr in eine Datei, nicht in eine Pipe: tee lebt mit dem Rest-Kind weiter.
    with (tmp_path / "fenster.txt").open("w", encoding="utf-8") as fenster:
        lauf = subprocess.Popen(
            ["bash", "-lc", zeile],
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=fenster,
        )
        beginn = time.monotonic()
        try:
            code = lauf.wait(timeout=15)
            dauer = time.monotonic() - beginn
        finally:
            lauf.kill()
            pid_datei = repo / "rest_kind.pid"
            if pid_datei.is_file():
                try:
                    os.kill(int(pid_datei.read_text(encoding="utf-8")), signal.SIGKILL)
                except ProcessLookupError:
                    pass
    assert code == 4
    assert dauer < 8, f"Fenster stand {dauer:.1f} s"
    text = (repo / ".to-spawn" / f"fenster_{TICKET}.log").read_text(encoding="utf-8")
    assert f"bau.py #{TICKET} Exit 4" in text
    assert text.index("Absicht: Rest-Kind bleibt") < text.index(f"bau.py #{TICKET} Exit 4")


def test_fenster_log_pfad_steht_in_der_startzeile(tmp_path: Path) -> None:
    log = capo.fenster_log(tmp_path, TICKET)
    assert log == tmp_path / ".to-spawn" / f"fenster_{TICKET}.log"
    assert shlex.quote(str(log)) in capo.bau_startzeile(tmp_path, TICKET, "")


def test_welche_bau_py_tmux_immer_skill_lokal_weiterleitung(tmp_path: Path) -> None:
    """Punkt 2: tmux-Zeile nimmt immer ``bau.py`` des Skills, der Weg ohne Vorspann
    (lokal/Windows) die Weiterleitung ``scripts/bau.py`` des Repos, sonst den Skill."""
    repo = _klon(tmp_path)
    skill_bau = capo._SKILL / "skripte" / "bau.py"
    assert capo._bau_skript(repo) == skill_bau
    (repo / "scripts").mkdir()
    (repo / "scripts" / "bau.py").write_text("", encoding="utf-8")
    assert capo._bau_skript(repo) == repo / "scripts" / "bau.py"
    assert str(skill_bau) in capo.bau_startzeile(repo, TICKET, "")
    assert str(repo / "scripts" / "bau.py") not in capo.bau_startzeile(repo, TICKET, "")
