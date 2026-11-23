"""Eigener, kurzer pytest-Temp-Ordner je Lauf (#349).

Parallele pytest-Läufe teilten sich ``<temp>/pytest-of-<nutzer>``; verschwand der Ordner
mitten im Lauf, scheiterten alle folgenden ``tmp_path``-Tests im Setup
(``could not create numbered dir``). ``tests/conftest.py`` gibt jedem Lauf ohne
``--basetemp`` einen eigenen Ordner direkt unter dem System-Temp.

Die Kind-Läufe laden genau die Hooks aus ``tests/conftest.py`` (per Dateipfad), damit das
Mini-Testverzeichnis nicht die ganze Suite einsammelt.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CONFTEST = REPO / "tests" / "conftest.py"

#: Längster erlaubter Ordnername unter dem System-Temp (``pt-`` + Zufall).
MAX_NAME = 12

_KIND_CONFTEST = f"""
import importlib.util

_spec = importlib.util.spec_from_file_location("_echt_conftest_349", {str(CONFTEST)!r})
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
for _name in ("pytest_configure", "pytest_sessionfinish"):
    if hasattr(_mod, _name):
        globals()[_name] = getattr(_mod, _name)
"""

_KIND_TEST = """
import os
import time
from pathlib import Path


def test_eins(tmp_path):
    Path(os.environ["BERICHT"]).write_text(str(tmp_path.parent), encoding="utf-8")
    los = Path(os.environ.get("LOS", ""))
    if os.environ.get("LOS"):
        ende = time.monotonic() + 60
        while not los.exists() and time.monotonic() < ende:
            time.sleep(0.05)


def test_zwei(tmp_path):
    assert tmp_path.is_dir()
    if os.environ.get("FEHLER"):
        raise AssertionError("absichtlich rot")
"""


def _welt(ordner: Path) -> Path:
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "conftest.py").write_text(_KIND_CONFTEST, encoding="utf-8")
    (ordner / "test_kind.py").write_text(_KIND_TEST, encoding="utf-8")
    return ordner


def _env(bericht: Path, **mehr: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "TO_SPAWN_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(REPO), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["BERICHT"] = str(bericht)
    env.update(mehr)
    return env


def _start(welt: Path, env: dict[str, str], *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "pytest", str(welt), "-q", "-p", "no:cacheprovider", "-p", "no:xdist", *args],
        cwd=welt,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _warten(proc: subprocess.Popen[str], timeout: float = 120) -> tuple[int, str]:
    try:
        ausgabe, _ = proc.communicate(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    return proc.returncode, ausgabe


def _bis_da(datei: Path, proc: subprocess.Popen[str], timeout: float = 60) -> Path:
    ende = time.monotonic() + timeout
    while not datei.exists() or not datei.read_text(encoding="utf-8"):
        if proc.poll() is not None or time.monotonic() > ende:
            code, ausgabe = _warten(proc)
            pytest.fail(f"Kind-Lauf meldete keinen Temp-Ordner (Exit {code}):\n{ausgabe}")
        time.sleep(0.05)
    return Path(datei.read_text(encoding="utf-8"))


def test_paralleler_lauf_ueberlebt_fremden_verlust_des_gemeinsamen_ordners(tmp_path: Path) -> None:
    """Lauf A läuft, Lauf B endet daneben, der gemeinsame ``pytest-of-*`` verschwindet → A bleibt grün."""
    temp = tmp_path / "t"
    temp.mkdir()
    welt = _welt(tmp_path / "welt")
    los = tmp_path / "los"
    a = _start(welt, _env(tmp_path / "a.txt", TMPDIR=str(temp), LOS=str(los)))
    try:
        basis_a = _bis_da(tmp_path / "a.txt", a)
        code_b, ausgabe_b = _warten(_start(welt, _env(tmp_path / "b.txt", TMPDIR=str(temp))))
        assert code_b == 0, ausgabe_b
        basis_b = Path((tmp_path / "b.txt").read_text(encoding="utf-8"))
        # Was ein fremder Lauf mit dem gemeinsamen Ordner tat: weg ist er.
        for gemeinsam in temp.glob("pytest-of-*"):
            shutil.rmtree(gemeinsam, ignore_errors=True)
        los.write_text("los", encoding="utf-8")
        code_a, ausgabe_a = _warten(a)
    finally:
        if a.poll() is None:
            a.kill()
            a.communicate()
    assert code_a == 0, ausgabe_a
    assert basis_a != basis_b
    assert basis_a.parent == temp.resolve() and basis_b.parent == temp.resolve(), (basis_a, basis_b)


def test_ausdruecklicher_basetemp_gewinnt_und_bleibt_liegen(tmp_path: Path) -> None:
    welt = _welt(tmp_path / "welt")
    eigen = tmp_path / "eigen"
    code, ausgabe = _warten(_start(welt, _env(tmp_path / "a.txt"), f"--basetemp={eigen}"))
    assert code == 0, ausgabe
    assert Path((tmp_path / "a.txt").read_text(encoding="utf-8")) == eigen.resolve()
    assert eigen.is_dir(), "fremder --basetemp darf nicht gelöscht werden"


def test_eigener_ordner_ist_kurz_und_nach_gruenem_lauf_weg(tmp_path: Path) -> None:
    welt = _welt(tmp_path / "welt")
    env = _env(tmp_path / "a.txt")
    code, ausgabe = _warten(_start(welt, env))
    assert code == 0, ausgabe
    basis = Path((tmp_path / "a.txt").read_text(encoding="utf-8"))
    system_temp = Path(env.get("TMPDIR") or tempfile.gettempdir()).resolve()
    assert basis.parent == system_temp, basis
    assert len(basis.name) <= MAX_NAME, basis
    assert not basis.exists(), "Ordner eines grünen Laufs muss weg sein"


def test_roter_lauf_laesst_seinen_ordner_zum_nachsehen(tmp_path: Path) -> None:
    temp = tmp_path / "t"
    temp.mkdir()
    welt = _welt(tmp_path / "welt")
    code, ausgabe = _warten(_start(welt, _env(tmp_path / "a.txt", TMPDIR=str(temp), FEHLER="1")))
    assert code == 1, ausgabe
    basis = Path((tmp_path / "a.txt").read_text(encoding="utf-8"))
    assert basis.parent == temp.resolve(), basis
    assert basis.is_dir()


@pytest.mark.skipif(not __import__("importlib").util.find_spec("xdist"), reason="pytest-xdist fehlt")
def test_xdist_worker_liegen_im_ordner_des_leit_prozesses(tmp_path: Path) -> None:
    temp = tmp_path / "t"
    temp.mkdir()
    welt = _welt(tmp_path / "welt")
    proc = subprocess.Popen(
        [sys.executable, "-m", "pytest", str(welt), "-q", "-p", "no:cacheprovider", "-n", "2"],
        cwd=welt,
        env=_env(tmp_path / "a.txt", TMPDIR=str(temp), FEHLER="1"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    code, ausgabe = _warten(proc)
    assert code == 1, ausgabe
    worker = Path((tmp_path / "a.txt").read_text(encoding="utf-8"))
    assert worker.name.startswith("popen-gw"), worker
    assert worker.parent.parent == temp.resolve(), worker
    assert len(worker.parent.name) <= MAX_NAME, worker
    assert [p.name for p in temp.iterdir()] == [worker.parent.name]
