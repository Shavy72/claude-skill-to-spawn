"""P7f (#68): eine Tür startet die bash — unter Windows Git-Bash, Pfade kommen heil an.

Weg-Tests ohne Attrappe: ``startklar._standard_laeufer`` und ``stand._lauf`` starten die echte
gewählte bash mit geerbtem ``MSYS_NO_PATHCONV=1`` (setzt Claude Code), ``~/…`` muss beim
Windows-Python als vorhandene Datei ankommen. Attrappen nur für die Rechenlogik „bash fehlt“.
"""

from __future__ import annotations

import ast
import os
import shlex
import shutil
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import bash_wahl, stand, startklar  # noqa: E402

WINDOWS = sys.platform == "win32"


def _marke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Pfadsperre an, eigenes ``~`` (kein Zufallstreffer unter ``C:\\c\\…``); Python-Prüfbefehl."""
    monkeypatch.setenv("MSYS_NO_PATHCONV", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "marke_p7f").write_text("x", encoding="utf-8")
    pruefung = "import sys, pathlib; sys.exit(0 if pathlib.Path(sys.argv[1]).is_file() else 3)"
    return f"{shlex.quote(Path(sys.executable).as_posix())} -c {shlex.quote(pruefung)} ~/marke_p7f"


def test_gewaehlte_bash_ist_nie_der_wsl_stub() -> None:
    pfad = bash_wahl.bash()
    if WINDOWS:
        assert not bash_wahl.ist_wsl_stub(pfad), pfad
        assert Path(pfad).is_file(), pfad
    else:
        assert pfad == shutil.which("bash")


def test_home_pfad_kommt_trotz_pfadsperre_beim_windows_programm_an(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    befehl = _marke(tmp_path, monkeypatch)
    lauf = startklar._standard_laeufer(befehl, tmp_path, None, 60, {})
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr


def test_stand_bash_befehl_bekommt_heilen_pfad_trotz_pfadsperre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weg über ``stand._lauf`` (Konfig-Befehl ``bash -c …``): auch dort keine Pfadsperre."""
    innen = _marke(tmp_path, monkeypatch)
    assert stand._lauf(f"bash -c {shlex.quote(innen)}", tmp_path, 60) == 0


def test_ohne_bash_klare_meldung_statt_wsl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Beide Systeme: keine bash → ``FileNotFoundError`` mit Klartext, auch über den Probe-Weg."""
    for name in ("CLAUDE_CODE_GIT_BASH_PATH", "GIT_BASH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bash_wahl, "_GIT_BASH_STANDARD", "Z:/fehlt/bash.exe")
    monkeypatch.setattr(bash_wahl.shutil, "which", lambda _name: None)
    erwartet = "Git-Bash nicht gefunden" if WINDOWS else "bash fehlt im PATH"
    with pytest.raises(FileNotFoundError, match=erwartet):
        bash_wahl.bash()
    with pytest.raises(FileNotFoundError, match=erwartet):
        startklar._standard_laeufer("exit 0", tmp_path, None, 10, {})


def test_test_naht_blendet_bash_auch_bei_festem_windows_pfad_aus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#646: Test-Naht blendet bash auch dann aus, wenn Windows Git-Bash an festen Orten fände."""
    fake_bash = tmp_path / "bash.exe"
    fake_bash.write_text("", encoding="utf-8")
    monkeypatch.setattr(bash_wahl.sys, "platform", "win32")
    monkeypatch.setattr(bash_wahl.shutil, "which", lambda _name: None)
    monkeypatch.setattr(bash_wahl, "_GIT_BASH_STANDARD", str(fake_bash))
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", str(fake_bash))
    monkeypatch.setenv("GIT_BASH", str(fake_bash))
    assert bash_wahl.bash() == str(fake_bash)
    monkeypatch.setenv(bash_wahl.TEST_FEHLT_ENV, "1")
    with pytest.raises(FileNotFoundError, match=bash_wahl.TEST_FEHLT_ENV):
        bash_wahl.bash()


def test_test_naht_laesst_auch_lauf_scheitern(monkeypatch: pytest.MonkeyPatch) -> None:
    """#646: Mit ``TEST_FEHLT_ENV=1`` bricht auch ``bash_wahl.lauf`` mit ``FileNotFoundError`` ab."""
    monkeypatch.setenv(bash_wahl.TEST_FEHLT_ENV, "1")
    with pytest.raises(FileNotFoundError, match=bash_wahl.TEST_FEHLT_ENV):
        bash_wahl.lauf(["-c", "exit 0"])


def test_test_naht_wirkt_nicht_ausserhalb_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """#646: Außerhalb von Tests (``starter.unter_test() == False``) greift die Test-Naht nicht."""
    monkeypatch.setenv(bash_wahl.TEST_FEHLT_ENV, "1")
    monkeypatch.setattr(bash_wahl.starter, "unter_test", lambda: False)
    pfad = bash_wahl.bash()
    assert Path(pfad).is_file(), pfad
    if WINDOWS:
        assert not bash_wahl.ist_wsl_stub(pfad), pfad


def test_lauf_nimmt_pfadsperre_nur_unter_windows_heraus(tmp_path: Path) -> None:
    lauf = bash_wahl.lauf(
        ["-c", 'echo "${MSYS_NO_PATHCONV:-leer} $PFAD_X"'],
        env={**os.environ, "MSYS_NO_PATHCONV": "1", "PFAD_X": "a"},
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert lauf.stdout.split() == (["leer", "a"] if WINDOWS else ["1", "a"])


def test_wsl_stub_regel() -> None:
    assert bash_wahl.ist_wsl_stub(r"C:\Windows\System32\bash.exe")
    assert bash_wahl.ist_wsl_stub(r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\bash.exe")
    assert not bash_wahl.ist_wsl_stub(r"C:\Program Files\Git\bin\bash.exe")
    assert not bash_wahl.ist_wsl_stub("/usr/bin/bash")


# --- Prüf-Test: bash startet nur über bash_wahl.lauf ------------------------------------

#: Bewusste bash-Stellen außerhalb der Tür, Schlüssel (Datei, Funktion). Neue nur mit Grund.
BASH_AUSNAHMEN = frozenset(
    {
        ("to_spawn/spawn.py", "_befehl_unix"),  # nur Nicht-Windows-Zweig (ziel local), Linux-bash
        ("to_spawn/capo.py", "folge_befehl"),  # `bash -lc` als Text im tmux-Fenster
        ("to_spawn/respawn.py", "_startbefehl"),  # `bash -lc` als Text im tmux-Fenster
    }
)

_TUER = "to_spawn/bash_wahl.py"
_SUBPROCESS = {"run", "Popen", "call", "check_call", "check_output"}


def _ist_bash(knoten: ast.AST) -> bool:
    """``"bash"``/``"/bin/bash"``/``"bash.exe"`` oder ``shutil.which("bash")``."""
    if isinstance(knoten, ast.Constant) and isinstance(knoten.value, str):
        return knoten.value.replace("\\", "/").rsplit("/", 1)[-1].lower() in ("bash", "bash.exe")
    return isinstance(knoten, ast.Call) and any(_ist_bash(a) for a in knoten.args)


def bash_stellen(wurzel: Path) -> set[tuple[str, str]]:
    """(Datei, Funktion) mit bash als argv-Element oder ``subprocess.*("bash …")`` als Text."""
    treffer: set[tuple[str, str]] = set()
    for ordner in ("to_spawn", "skripte"):
        for datei in sorted((wurzel / ordner).rglob("*.py")):
            rel = datei.relative_to(wurzel).as_posix()
            if rel == _TUER:
                continue
            baum = ast.parse(datei.read_text(encoding="utf-8"), filename=rel)
            for funktion in ast.walk(baum):
                if not isinstance(funktion, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for k in ast.walk(funktion):
                    argv = isinstance(k, (ast.List, ast.Tuple)) and any(_ist_bash(e) for e in k.elts)
                    text = (
                        isinstance(k, ast.Call)
                        and isinstance(k.func, ast.Attribute)
                        and k.func.attr in _SUBPROCESS
                        and bool(k.args)
                        and isinstance(k.args[0], ast.Constant)
                        and isinstance(k.args[0].value, str)
                        and k.args[0].value.split(" ", 1)[0].rsplit("/", 1)[-1] in ("bash", "bash.exe")
                    )
                    if argv or text:
                        treffer.add((rel, funktion.name))
    return treffer


def test_bash_startet_nur_ueber_die_tuer() -> None:
    stellen = bash_stellen(SKILL)
    neu = sorted(stellen - BASH_AUSNAHMEN)
    assert not neu, f"bash an bash_wahl.lauf vorbei — über to_spawn.bash_wahl.lauf starten: {neu}"
    weg = sorted(BASH_AUSNAHMEN - stellen)
    assert not weg, f"Ausnahme ohne Treffer, aus BASH_AUSNAHMEN streichen: {weg}"


def test_bash_pruefung_findet_direkten_start(tmp_path: Path) -> None:
    (tmp_path / "to_spawn").mkdir()
    (tmp_path / "skripte").mkdir()
    (tmp_path / "to_spawn" / "neu.py").write_text(
        "import subprocess\n"
        "def los():\n    subprocess.run(['bash', '-c', 'echo x'])\n"
        "def text():\n    subprocess.Popen('bash -c true', shell=True)\n"
        "def gesucht():\n    subprocess.run([shutil.which('bash'), 'x.sh'])\n",
        encoding="utf-8",
    )
    (tmp_path / "skripte" / "ok.py").write_text(
        "def f():\n    return ['sh', '-c', 'bash ist nur Text']\n", encoding="utf-8"
    )
    assert bash_stellen(tmp_path) == {
        ("to_spawn/neu.py", "los"),
        ("to_spawn/neu.py", "text"),
        ("to_spawn/neu.py", "gesucht"),
    }
