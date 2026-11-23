"""Reste eigener pytest-Temp-Ordner räumen (#177, Nachtrag zu #349).

``tests/conftest.py`` gibt jedem Lauf einen eigenen ``<Temp>/pt-*`` und löscht ihn nur nach
grünem Lauf. Rote Läufe und Abbrüche ließen ihn für immer liegen, ``--help``/``--markers``
einen leeren — auf dem Bau-Server ist ``/tmp`` eine RAM-Platte. Jetzt trägt jeder eigene
Ordner eine Marker-Datei mit der PID des Laufs; der nächste Lauf räumt beim Start nur
markierte Ordner, deren Prozess tot und deren Marker alt ist.

Echte Kind-Läufe, echte Ordner, echte Prozesse. Die Kind-Läufe laden genau die Hooks aus
``tests/conftest.py`` (per Dateipfad).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CONFTEST = REPO / "tests" / "conftest.py"

#: Name der Marker-Datei (muss zu ``tests/conftest.py`` passen).
MARKER = ".pt-lauf"

#: Älter als jede Schonfrist des Räumers.
SEHR_ALT_S = 3 * 24 * 3600

_KIND_CONFTEST = f"""
import importlib.util

_spec = importlib.util.spec_from_file_location("_echt_conftest_177", {str(CONFTEST)!r})
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
for _name in ("pytest_configure", "pytest_sessionstart", "pytest_sessionfinish", "pytest_unconfigure"):
    if hasattr(_mod, _name):
        globals()[_name] = getattr(_mod, _name)
"""

_KIND_TEST = """
import os
import time
from pathlib import Path


def test_eins(tmp_path):
    (tmp_path / "inhalt.txt").write_text("x", encoding="utf-8")
    Path(os.environ["BERICHT"]).write_text(str(tmp_path.parent), encoding="utf-8")
    los = Path(os.environ.get("LOS", ""))
    if os.environ.get("LOS"):
        ende = time.monotonic() + 90
        while not los.exists() and time.monotonic() < ende:
            time.sleep(0.05)


def test_zwei(tmp_path):
    assert tmp_path.is_dir()
    if os.environ.get("FEHLER"):
        raise AssertionError("absichtlich rot")
"""

_HAT_XDIST = importlib.util.find_spec("xdist") is not None


def _welt(ordner: Path) -> Path:
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "conftest.py").write_text(_KIND_CONFTEST, encoding="utf-8")
    (ordner / "test_kind.py").write_text(_KIND_TEST, encoding="utf-8")
    return ordner


def _env(temp: Path, bericht: Path, **mehr: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "TO_SPAWN_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(REPO), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["BERICHT"] = str(bericht)
    for name in ("TMPDIR", "TEMP", "TMP"):
        env[name] = str(temp)
    env.update(mehr)
    return env


def _start(welt: Path, env: dict[str, str], *args: str) -> subprocess.Popen[str]:
    extra = list(args) if "-n" in args else ["-p", "no:xdist", *args]
    return subprocess.Popen(
        [sys.executable, "-m", "pytest", str(welt), "-q", "-p", "no:cacheprovider", *extra],
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


def _tote_pid() -> int:
    """PID eines echten, schon beendeten Prozesses."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _altern(pfad: Path) -> None:
    alt = time.time() - SEHR_ALT_S
    os.utime(pfad, (alt, alt))


def _rest(temp: Path, name: str, pid: int | None, *, alt: bool) -> Path:
    """Ordner wie ein liegengebliebener Lauf: Inhalt + (optional) Marker mit PID."""
    ordner = temp / name
    (ordner / "test_x0").mkdir(parents=True)
    (ordner / "test_x0" / "inhalt.txt").write_text("x", encoding="utf-8")
    if pid is not None:
        (ordner / MARKER).write_text(f"{pid}\n", encoding="utf-8")
        if alt:
            _altern(ordner / MARKER)
    if alt:
        _altern(ordner)
    return ordner


def _pt_ordner(temp: Path) -> list[str]:
    return sorted(p.name for p in temp.iterdir() if p.name.startswith("pt-"))


def test_naechster_lauf_raeumt_nur_alte_eigene_reste_toter_laeufe(tmp_path: Path) -> None:
    temp = tmp_path / "t"
    temp.mkdir()
    tot = _tote_pid()
    alt_tot = _rest(temp, "pt-alttot1", tot, alt=True)
    jung_tot = _rest(temp, "pt-jungtot", tot, alt=False)
    fremd = _rest(temp, "pt-fremd01", None, alt=True)  # ohne Marker: nicht von uns
    alt_lebt = _rest(temp, "pt-altlebt", os.getpid(), alt=True)  # dieser Prozess lebt
    welt = _welt(tmp_path / "welt")
    code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "a.txt")))
    assert code == 0, ausgabe
    assert not alt_tot.exists(), "alter Rest eines toten Laufs muss weg sein"
    assert jung_tot.is_dir(), "junger Rest bleibt zum Nachsehen"
    assert fremd.is_dir(), "Ordner ohne Marker ist fremd und bleibt"
    assert alt_lebt.is_dir(), "Ordner eines lebenden Prozesses bleibt"


def test_help_und_markers_hinterlassen_keinen_ordner(tmp_path: Path) -> None:
    temp = tmp_path / "t"
    temp.mkdir()
    welt = _welt(tmp_path / "welt")
    for schalter in ("--help", "--markers"):
        code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "a.txt"), schalter))
        assert code == 0, ausgabe
        assert _pt_ordner(temp) == [], (schalter, _pt_ordner(temp))


def test_roter_lauf_bleibt_mit_marker_auch_nach_naechstem_lauf(tmp_path: Path) -> None:
    temp = tmp_path / "t"
    temp.mkdir()
    welt = _welt(tmp_path / "welt")
    code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "a.txt", FEHLER="1")))
    assert code == 1, ausgabe
    rot = Path((tmp_path / "a.txt").read_text(encoding="utf-8"))
    assert (rot / MARKER).is_file(), "eigener Ordner trägt die Marker-Datei"
    code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "b.txt")))
    assert code == 0, ausgabe
    assert rot.is_dir(), "frischer roter Rest bleibt zum Nachsehen"
    _altern(rot / MARKER)
    code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "c.txt")))
    assert code == 0, ausgabe
    assert not rot.exists(), "alt gewordener roter Rest wird geräumt"


@pytest.mark.parametrize(
    "args",
    [
        pytest.param((), id="ein-prozess"),
        pytest.param(("-n", "2"), id="xdist-n2", marks=pytest.mark.skipif(not _HAT_XDIST, reason="pytest-xdist fehlt")),
    ],
)
def test_laufender_zweiter_lauf_behaelt_seinen_ordner(tmp_path: Path, args: tuple[str, ...]) -> None:
    """Lauf A hält seinen Ordner (Marker künstlich alt), Lauf B startet daneben → A bleibt grün."""
    temp = tmp_path / "t"
    temp.mkdir()
    welt = _welt(tmp_path / "welt")
    los = tmp_path / "los"
    a = _start(welt, _env(temp, tmp_path / "a.txt", LOS=str(los)), *args)
    try:
        basis_a = _bis_da(tmp_path / "a.txt", a)
        ordner_a = basis_a.parent if basis_a.name.startswith("popen-gw") else basis_a
        assert (ordner_a / MARKER).is_file(), ordner_a
        _altern(ordner_a / MARKER)  # nur die lebende PID schützt jetzt noch
        code_b, ausgabe_b = _warten(_start(welt, _env(temp, tmp_path / "b.txt")))
        assert code_b == 0, ausgabe_b
        assert ordner_a.is_dir(), "Räumer nahm einem laufenden Lauf den Ordner weg"
        assert any(basis_a.rglob("inhalt.txt")), "Inhalt des laufenden Laufs ist weg"
        los.write_text("los", encoding="utf-8")
        code_a, ausgabe_a = _warten(a)
    finally:
        if a.poll() is None:
            a.kill()
            a.communicate()
    assert code_a == 0, ausgabe_a


_KIND_OHNE_TMP = """
def test_ohne_tmp():
    assert True
"""

_STOERER = """
import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session):
    basis = session.config._tmp_path_factory.getbasetemp()  # legt an, pytest merkt ihn sich
    (basis / ".pt-lauf").mkdir()  # Marker-Pfad belegt -> Schreiben scheitert echt (OSError)
"""


def test_ohne_tmpdir_plugin_kein_internalerror(tmp_path: Path) -> None:
    """``-p no:tmpdir``: kein ``--basetemp``, kein ``_tmp_path_factory`` — Lauf bleibt grün."""
    temp = tmp_path / "t"
    temp.mkdir()
    welt = _welt(tmp_path / "welt")
    (welt / "test_kind.py").write_text(_KIND_OHNE_TMP, encoding="utf-8")
    code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "a.txt"), "-p", "no:tmpdir"))
    assert "INTERNALERROR" not in ausgabe, ausgabe
    assert code == 0, ausgabe
    assert _pt_ordner(temp) == [], _pt_ordner(temp)


def test_marker_schreiben_scheitert_lauf_bleibt_gruen(tmp_path: Path) -> None:
    temp = tmp_path / "t"
    temp.mkdir()
    welt = _welt(tmp_path / "welt")
    (welt / "stoerer_177.py").write_text(_STOERER, encoding="utf-8")
    code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "a.txt"), "-p", "stoerer_177"))
    assert "INTERNALERROR" not in ausgabe, ausgabe
    assert code == 0, ausgabe


@pytest.mark.parametrize("pid", ["0", "-5", str(2**31), str(2**64), "abc", ""])
def test_marker_mit_unsinniger_pid_bleibt_liegen(tmp_path: Path, pid: str) -> None:
    """Nur ``0 < pid < 2**31`` zählt; sonst nie anfassen (Linux: ``/proc/0`` fehlt = „tot“)."""
    temp = tmp_path / "t"
    temp.mkdir()
    rest = _rest(temp, "pt-unsinn1", None, alt=True)
    (rest / MARKER).write_text(f"{pid}\n", encoding="utf-8")
    _altern(rest / MARKER)
    welt = _welt(tmp_path / "welt")
    code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "a.txt")))
    assert code == 0, ausgabe
    assert rest.is_dir(), f"Ordner mit PID {pid!r} wurde gelöscht"


@pytest.mark.braucht("posix")
def test_raeumen_scheitert_an_rechten_lauf_bleibt_gruen_und_loggt(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root löscht trotz fehlender Rechte")
    temp = tmp_path / "t"
    temp.mkdir()
    rest = _rest(temp, "pt-gesperrt", _tote_pid(), alt=True)
    gesperrt = rest / "test_x0"
    gesperrt.chmod(0o500)  # Datei darin nicht löschbar
    welt = _welt(tmp_path / "welt")
    try:
        code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "a.txt")))
    finally:
        gesperrt.chmod(0o700)
    assert "INTERNALERROR" not in ausgabe, ausgabe
    assert code == 0, ausgabe
    assert "pt-gesperrt" in ausgabe, f"Räum-Fehler nicht geloggt:\n{ausgabe}"
