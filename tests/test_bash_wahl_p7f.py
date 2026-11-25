"""P7f (#68): eine Tür startet die bash — unter Windows Git-Bash, Pfade kommen heil an.

Weg-Tests ohne Attrappe: ``startklar._standard_laeufer`` und ``stand._lauf`` starten die echte
gewählte bash mit geerbtem ``MSYS_NO_PATHCONV=1`` (setzt Claude Code), ``~/…`` muss beim
Windows-Python als vorhandene Datei ankommen. Attrappen nur für die Rechenlogik „bash fehlt“.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

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


# --- #186: beide Plattformen (Linux & Windows) + Wissen nur in bash_wahl ----------------


@pytest.mark.parametrize(
    ("plattform", "erwartet"),
    [("linux", "bash fehlt im PATH"), ("win32", "Git-Bash nicht gefunden")],
)
def test_ohne_bash_klare_meldung_beide_plattformen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    plattform: str,
    erwartet: str,
) -> None:
    """#186: fehlende bash meldet Klartext unter Linux UND Windows — über bash_wahl, startklar und stand."""
    monkeypatch.setattr(bash_wahl.sys, "platform", plattform)
    for name in ("CLAUDE_CODE_GIT_BASH_PATH", "GIT_BASH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bash_wahl, "_GIT_BASH_STANDARD", "Z:/fehlt/bash.exe")
    # Unter Windows auch den WSL-Stub prüfen: er darf nie als bash zählen.
    if plattform == "win32":
        monkeypatch.setattr(
            bash_wahl.shutil,
            "which",
            lambda name: r"C:\Windows\System32\bash.exe" if name == "bash" else None,
        )
    else:
        monkeypatch.setattr(bash_wahl.shutil, "which", lambda _name: None)

    with pytest.raises(FileNotFoundError, match=erwartet):
        bash_wahl.bash()
    with pytest.raises(FileNotFoundError, match=erwartet):
        startklar._standard_laeufer("exit 0", tmp_path, None, 10, {})

    werkzeug_datei = tmp_path / "werkzeug.py"
    werkzeug_datei.write_text("pass\n", encoding="utf-8")
    werkzeug = startklar.Werkzeug("probe", "echo ok", werkzeug_datei.as_posix())
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    # Hook-Weg und Trockenlauf-Weg (ohne Hooks) melden beide die eigene Klartext-Meldung.
    for dateien in ([settings], []):
        befunde = startklar.werkzeug_probe(
            tmp_path, 186, settings_dateien=dateien, werkzeuge=(werkzeug,)
        )
        assert len(befunde) == 1 and not befunde[0].ok, befunde
        assert erwartet in befunde[0].text, befunde[0]

    with caplog.at_level(logging.ERROR, logger="to_spawn.stand"):
        assert stand._lauf("bash -c true", tmp_path, 10) is None
    assert erwartet in caplog.text


@pytest.mark.parametrize(("plattform", "bleibt"), [("win32", False), ("linux", True)])
def test_stand_und_lauf_bereinigen_pfadsperren_nur_unter_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plattform: str, bleibt: bool
) -> None:
    """#186: ``stand._lauf`` und ``bash_wahl.lauf`` entfernen MSYS-Pfadsperren nur unter Windows."""
    gesehen: list[dict[str, str]] = []

    def fake_run(
        _cmd: Any, *, env: dict[str, str], **_optionen: Any
    ) -> subprocess.CompletedProcess[str]:
        gesehen.append(dict(env))
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(bash_wahl.sys, "platform", plattform)
    monkeypatch.setattr(bash_wahl, "bash", lambda: "/fake/bash")
    monkeypatch.setattr(bash_wahl.subprocess, "run", fake_run)
    monkeypatch.setenv("MSYS_NO_PATHCONV", "1")
    monkeypatch.setenv("MSYS2_ARG_CONV_EXCL", "*")

    assert stand._lauf("bash -c true", tmp_path, 30) == 0
    assert gesehen[-1]["TO_SPAWN_PY"] == sys.executable
    assert ("MSYS_NO_PATHCONV" in gesehen[-1]) is bleibt
    assert ("MSYS2_ARG_CONV_EXCL" in gesehen[-1]) is bleibt

    bash_wahl.lauf(
        ["-c", "true"],
        env={"msys_no_pathconv": "1", "MSYS2_ARG_CONV_EXCL": "*", "BLEIBT": "ja"},
    )
    assert gesehen[-1]["BLEIBT"] == "ja"
    assert ("msys_no_pathconv" in gesehen[-1]) is bleibt
    assert ("MSYS2_ARG_CONV_EXCL" in gesehen[-1]) is bleibt


def _code_strings(datei: Path) -> set[str]:
    """Alle String-Literale im Code einer Python-Datei ohne Docstrings."""
    baum = ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
    docstrings: set[int] = set()
    for knoten in ast.walk(baum):
        if isinstance(
            knoten, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and knoten.body:
            erste = knoten.body[0]
            if (
                isinstance(erste, ast.Expr)
                and isinstance(erste.value, ast.Constant)
                and isinstance(erste.value.value, str)
            ):
                docstrings.add(id(erste.value))
    return {
        k.value
        for k in ast.walk(baum)
        if isinstance(k, ast.Constant)
        and isinstance(k.value, str)
        and id(k) not in docstrings
    }


def test_pfadsperre_nur_in_bash_wahl() -> None:
    """#186: Kein eigener MSYS-Pfadsperren-Filter außerhalb von ``to_spawn/bash_wahl.py``."""
    erlaubt = {_TUER, "tests/test_bash_wahl_p7f.py"}
    treffer: list[str] = []
    for ordner in ("to_spawn", "skripte", "tests"):
        for datei in sorted((SKILL / ordner).rglob("*.py")):
            rel = datei.relative_to(SKILL).as_posix()
            if rel in erlaubt:
                continue
            if "MSYS2_ARG_CONV_EXCL" in _code_strings(datei):
                treffer.append(rel)
    assert not treffer, f"Eigener MSYS-Umgebungs-Filter außerhalb von bash_wahl.py: {treffer}"


def test_keine_eigene_git_bash_suche_ausserhalb_bash_wahl() -> None:
    """#186: Keine eigene Git-Bash-Dateisuche (``ProgramFiles`` + ``bash.exe``) außerhalb von ``bash_wahl.py``."""
    erlaubt = {_TUER, "tests/test_bash_wahl_p7f.py"}
    treffer: list[str] = []
    for ordner in ("to_spawn", "skripte", "tests"):
        for datei in sorted((SKILL / ordner).rglob("*.py")):
            rel = datei.relative_to(SKILL).as_posix()
            if rel in erlaubt:
                continue
            werte = _code_strings(datei)
            if "bash.exe" in werte and ({"ProgramFiles", "ProgramW6432"} & werte):
                treffer.append(rel)
    assert not treffer, f"Eigene Git-Bash-Suche außerhalb von bash_wahl.py: {treffer}"
