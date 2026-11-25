"""#197: Suite-Läufer bindet unter Windows jede Testdatei an ein Job-Objekt.

Weg-Tests ohne Attrappen für die Abnahme:
- Eine Testdatei startet echte Kindprozesse (TERM-ignorierendes Kind sowie Git-Bash-Kind
  ``sh -c "sleep 37 & wait"``) und endet selbst, ohne sie abzuräumen. Nach dem Ende der
  Testdatei über ``skripte/suite.py`` (und ``skripte/suite.sh``) lebt kein Kind mehr.
- Wird der Läufer während einer laufenden Testdatei abgebrochen, sterben die Kinder mit.
- Ohne Wurzel-``conftest.py`` bricht ``skripte/suite.py`` mit Exit 64 ab (wie ``suite.sh``).
- Der Läufer nutzt die vorhandene Tür ``to_spawn/sitzungsjob.py`` (kein zweiter Job-Weg).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from git_bash import git_bash

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import prozessbaum  # noqa: E402

WINDOWS = sys.platform == "win32"
SUITE_PY = SKILL / "skripte" / "suite.py"
SUITE_SH = SKILL / "skripte" / "suite.sh"


def _warte_tot(pid: int, frist: float = 15.0) -> bool:
    ende = time.monotonic() + frist
    while time.monotonic() < ende and prozessbaum.lebt(pid):
        time.sleep(0.1)
    return not prozessbaum.lebt(pid)


def _aufraeumen(pids: list[int]) -> None:
    for pid in pids:
        if pid > 0 and prozessbaum.lebt(pid):
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def _warte_pids(datei: Path, anzahl: int, frist: float = 20.0) -> list[int]:
    ende = time.monotonic() + frist
    while time.monotonic() < ende:
        if datei.is_file():
            teile = datei.read_text(encoding="utf-8").split()
            if len(teile) >= anzahl:
                return [int(x) for x in teile[:anzahl]]
        time.sleep(0.1)
    return []


def test_suite_py_nutzt_sitzungsjob_ohne_zweiten_weg() -> None:
    """#197: Der Läufer importiert ``sitzungsjob`` und baut kein zweites Job-Objekt."""
    assert SUITE_PY.is_file(), f"Suite-Läufer fehlt: {SUITE_PY}"
    quelle = SUITE_PY.read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    importe: set[str] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ImportFrom):
            importe.update(a.name for a in knoten.names)
        elif isinstance(knoten, ast.Import):
            importe.update(a.name for a in knoten.names)
    assert "sitzungsjob" in importe
    assert "CreateJobObjectW" not in quelle
    assert "AssignProcessToJobObject" not in quelle


def test_suite_py_bricht_ohne_wurzel_conftest_ab(tmp_path: Path) -> None:
    """Ohne Wurzel-``conftest.py`` bricht ``suite.py`` mit Exit 64 ab (auch unter cp1252-Pipe)."""
    assert SUITE_PY.is_file(), f"Suite-Läufer fehlt: {SUITE_PY}"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    lauf = subprocess.run(
        [sys.executable, str(SUITE_PY), "--wurzel", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
        **prozessbaum.ohne_fenster(),
    )
    assert lauf.returncode == 64, (lauf.returncode, lauf.stdout, lauf.stderr)
    zeilen = lauf.stderr.strip().splitlines()
    assert len(zeilen) == 1 and "conftest.py" in zeilen[0] and "Klon" in zeilen[0], lauf.stderr


def test_suite_py_je_datei_exit_codes(tmp_path: Path) -> None:
    """``--je-datei`` läuft unter Linux und Windows dateiweise und meldet Rot sauber."""
    assert SUITE_PY.is_file(), f"Suite-Läufer fehlt: {SUITE_PY}"
    (tmp_path / "conftest.py").write_text('collect_ignore = ["review-hook", "probe"]\n', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / "review-hook").mkdir()
    (tmp_path / "review-hook" / "test_ignoriert.py").write_text(
        "def test_nie():\n    assert False\n", encoding="utf-8"
    )
    ok = subprocess.run(
        [sys.executable, str(SUITE_PY), "--wurzel", str(tmp_path), "--je-datei"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **prozessbaum.ohne_fenster(),
    )
    assert ok.returncode == 0, (ok.stdout, ok.stderr)

    (tmp_path / "tests" / "test_rot.py").write_text("def test_rot():\n    assert False\n", encoding="utf-8")
    rot = subprocess.run(
        [sys.executable, str(SUITE_PY), "--wurzel", str(tmp_path), "--je-datei"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **prozessbaum.ohne_fenster(),
    )
    assert rot.returncode != 0, (rot.stdout, rot.stderr)


@pytest.mark.skipif(not WINDOWS, reason="Job-Objekt gibt es nur unter Windows")
def test_suite_py_beendet_term_ignorierendes_und_git_bash_kind(tmp_path: Path) -> None:
    """#197 Abnahme: Testdatei startet ein TERM-ignorierendes Kind und ein Git-Bash-Kind
    ``sh -c 'sleep 37 & wait'`` und räumt sie nicht ab → nach Datei-Ende lebt keins mehr."""
    assert SUITE_PY.is_file(), f"Suite-Läufer fehlt: {SUITE_PY}"
    bash = git_bash()
    if bash is None:
        pytest.skip("Git Bash fehlt")
    (tmp_path / "conftest.py").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    pid_datei = tmp_path / "kinder.txt"
    testdatei = tmp_path / "tests" / "test_rest_kinder.py"
    kind_code = (
        "import os, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(37)\n"
    )
    testdatei.write_text(
        "import os, subprocess, sys, time\n"
        "from pathlib import Path\n\n"
        "def test_hinterlaesst_kinder():\n"
        f"    py_kind = subprocess.Popen([sys.executable, '-c', {kind_code!r}], "
        "creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)\n"
        f"    bash = {bash!r}\n"
        f"    out = Path({str(pid_datei)!r})\n"
        "    skript = 'sleep 37 & echo \"$(cat /proc/$!/winpid)\" > \"$1.tmp\"; mv \"$1.tmp\" \"$1\"; wait'\n"
        "    bash_pid_datei = out.with_suffix('.bash')\n"
        "    subprocess.Popen([bash, '-c', skript, '_', str(bash_pid_datei)], "
        "creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)\n"
        "    ende = time.monotonic() + 15\n"
        "    while time.monotonic() < ende and not bash_pid_datei.is_file():\n"
        "        time.sleep(0.05)\n"
        "    assert bash_pid_datei.is_file(), 'Git-Bash-Kind nicht gestartet'\n"
        "    sleep_pid = int(bash_pid_datei.read_text(encoding='utf-8').strip())\n"
        "    out.write_text(f'{py_kind.pid} {sleep_pid}\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    pids: list[int] = []
    try:
        lauf = subprocess.run(
            [sys.executable, str(SUITE_PY), "--wurzel", str(tmp_path), str(testdatei)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            **prozessbaum.ohne_fenster(),
        )
        assert lauf.returncode == 0, (lauf.stdout, lauf.stderr)
        pids = _warte_pids(pid_datei, 2, frist=5.0)
        assert len(pids) == 2, f"Kinder-PIDs fehlen: {pids}"
        for pid in pids:
            assert _warte_tot(pid), f"Kindprozess {pid} überlebte das Ende der Testdatei"
    finally:
        _aufraeumen(pids)


@pytest.mark.skipif(not WINDOWS, reason="Job-Objekt gibt es nur unter Windows")
def test_suite_py_abbruch_beendet_kinder_mit(tmp_path: Path) -> None:
    """#197: Wird der Suite-Läufer hart abgebrochen, schließt Windows den Job-Griff —
    alle Kinder der laufenden Testdatei sterben mit."""
    assert SUITE_PY.is_file(), f"Suite-Läufer fehlt: {SUITE_PY}"
    bash = git_bash()
    if bash is None:
        pytest.skip("Git Bash fehlt")
    (tmp_path / "conftest.py").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    pid_datei = tmp_path / "abbruch_kinder.txt"
    testdatei = tmp_path / "tests" / "test_haengt.py"
    testdatei.write_text(
        "import subprocess, time\n"
        "from pathlib import Path\n\n"
        "def test_wartet_lang():\n"
        f"    bash = {bash!r}\n"
        f"    out = Path({str(pid_datei)!r})\n"
        "    skript = 'sleep 37 & echo \"$(cat /proc/$!/winpid)\" > \"$1.tmp\"; mv \"$1.tmp\" \"$1\"; wait'\n"
        "    subprocess.Popen([bash, '-c', skript, '_', str(out)], "
        "creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)\n"
        "    time.sleep(37)\n",
        encoding="utf-8",
    )
    pids: list[int] = []
    laeufer: subprocess.Popen[str] | None = None
    try:
        laeufer = subprocess.Popen(
            [sys.executable, str(SUITE_PY), "--wurzel", str(tmp_path), str(testdatei)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **prozessbaum.ohne_fenster(),
        )
        pids = _warte_pids(pid_datei, 1, frist=20.0)
        assert len(pids) == 1, "Kindprozess in hängender Testdatei nicht gestartet"
        assert prozessbaum.lebt(pids[0]), "Kindprozess lebte vor dem Abbruch gar nicht"
        # Harter Abbruch des Läufers selbst: Windows schließt den Job-Griff des toten Halters.
        laeufer.kill()
        laeufer.wait(timeout=10)
        assert _warte_tot(pids[0]), f"Kindprozess {pids[0]} überlebte den Abbruch des Suite-Läufers"
    finally:
        if laeufer is not None and laeufer.poll() is None:
            laeufer.kill()
            laeufer.wait(timeout=5)
        _aufraeumen(pids)


@pytest.mark.skipif(not WINDOWS, reason="Job-Objekt gibt es nur unter Windows")
def test_suite_sh_delegiert_unter_windows_an_suite_py(tmp_path: Path) -> None:
    """#197: ``bash skripte/suite.sh`` geht unter Windows über ``skripte/suite.py`` (Job-Objekt)."""
    bash = git_bash()
    if bash is None:
        pytest.skip("Git Bash fehlt")
    assert SUITE_PY.is_file(), f"Suite-Läufer fehlt: {SUITE_PY}"
    (tmp_path / "conftest.py").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    pid_datei = tmp_path / "sh_kind.txt"
    testdatei = tmp_path / "tests" / "test_sh_kind.py"
    testdatei.write_text(
        "import subprocess, time\n"
        "from pathlib import Path\n\n"
        "def test_hinterlaesst_bash_kind():\n"
        f"    bash = {bash!r}\n"
        f"    out = Path({str(pid_datei)!r})\n"
        "    skript = 'sleep 37 & echo \"$(cat /proc/$!/winpid)\" > \"$1.tmp\"; mv \"$1.tmp\" \"$1\"; wait'\n"
        "    subprocess.Popen([bash, '-c', skript, '_', str(out)], "
        "creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)\n"
        "    ende = time.monotonic() + 15\n"
        "    while time.monotonic() < ende and not out.is_file():\n"
        "        time.sleep(0.05)\n"
        "    assert out.is_file()\n",
        encoding="utf-8",
    )
    pids: list[int] = []
    try:
        lauf = subprocess.run(
            [bash, str(SUITE_SH), "--wurzel", str(tmp_path), str(testdatei)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            **prozessbaum.ohne_fenster(),
        )
        assert lauf.returncode == 0, (lauf.stdout, lauf.stderr)
        pids = _warte_pids(pid_datei, 1, frist=5.0)
        assert len(pids) == 1, "Kindprozess-PID fehlt"
        assert _warte_tot(pids[0]), f"Kindprozess {pids[0]} überlebte skripte/suite.sh"
    finally:
        _aufraeumen(pids)
