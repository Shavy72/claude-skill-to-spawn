"""#658: Kein Unterprozess von to_spawn öffnet unter Windows ein Konsolenfenster.

Vorfall: Die geplante Aufgabe „to-spawn Hausmeister“ läuft als ``pythonw.exe`` (ohne Konsole).
Jedes Konsolen-Kind (git fetch …) bekam eine eigene Konsole, Windows Terminal machte daraus
~300 sichtbare Fenster. Regel: jeder ``subprocess``-Start nimmt ``**prozessbaum.fensterlos()``
(oder setzt ``creationflags`` bewusst selbst). Die Quelltext-Prüfung läuft überall, die echte
Fenster-Messung nur unter Windows.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent

#: Start-Funktionen; ohne ``creationflags`` bekommt ein Konsolen-Kind unter ``pythonw`` ein Fenster.
_START = {
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("asyncio", "create_subprocess_exec"),
    ("asyncio", "create_subprocess_shell"),
}
#: Starts ohne ``creationflags``-Möglichkeit: hier nie erlaubt.
_VERBOTEN = {
    ("subprocess", "getoutput"),
    ("subprocess", "getstatusoutput"),
    ("os", "system"),
    ("os", "popen"),
    ("os", "startfile"),
}
_HELFER = {"fensterlos", "ohne_fenster"}

#: (Datei, Funktion) → Grund. Nur Stellen, deren Optionen nachweislich ``fensterlos()`` enthalten.
AUSNAHMEN: dict[tuple[str, str], str] = {
    ("to_spawn/starter.py", "starte"): "Start-Tür: popen_args = {**prozessbaum.fensterlos(), **popen_args}",
    ("to_spawn/bash_wahl.py", "lauf"): "optionen = {**prozessbaum.fensterlos(), **optionen} am Funktionsanfang",
    (
        "to_spawn/prozessbaum.py",
        "session_beenden",
    ): "Hintergrund-Nachlauf: optionen = ohne_fenster() unter Windows",
}


def _aliase(baum: ast.AST) -> dict[str, tuple[str, str | None]]:
    """Lokaler Name → (Modul, Funktion): ``import subprocess as sp`` → ``sp: (subprocess, None)``,
    ``from subprocess import run as r`` → ``r: (subprocess, run)``."""
    namen: dict[str, tuple[str, str | None]] = {}
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            for alias in knoten.names:
                namen[alias.asname or alias.name] = (alias.name, None)
        elif isinstance(knoten, ast.ImportFrom) and knoten.module in ("subprocess", "os", "asyncio"):
            for alias in knoten.names:
                namen[alias.asname or alias.name] = (knoten.module, alias.name)
    return namen


def _ziel(knoten: ast.expr, aliase: dict[str, tuple[str, str | None]]) -> tuple[str, str] | None:
    if isinstance(knoten, ast.Attribute) and isinstance(knoten.value, ast.Name):
        modul, fn = aliase.get(knoten.value.id, (knoten.value.id, None))
        return (modul, knoten.attr) if fn is None else None
    if isinstance(knoten, ast.Name) and knoten.id in aliase:
        modul, fn = aliase[knoten.id]
        return (modul, fn) if fn is not None else None
    return None


def _ist_start(ziel: tuple[str, str] | None) -> bool:
    if ziel is None:
        return False
    return ziel in _START or ziel in _VERBOTEN or (ziel[0] == "os" and ziel[1].startswith("spawn"))


def _ist_helfer(wert: ast.expr) -> bool:
    """``**prozessbaum.fensterlos()`` oder ``**{**fensterlos(), **kwargs}``."""
    if isinstance(wert, ast.Call):
        fn = wert.func
        return (fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")) in _HELFER
    if isinstance(wert, ast.Dict):
        return any(k is None and _ist_helfer(v) for k, v in zip(wert.keys, wert.values))
    return False


def _fensterlos(aufruf: ast.Call, ziel: tuple[str, str]) -> bool:
    if ziel in _VERBOTEN or ziel[0] == "os":
        return False
    return any(kw.arg == "creationflags" or (kw.arg is None and _ist_helfer(kw.value)) for kw in aufruf.keywords)


def _funde_in(quelle: str, rel: str) -> list[tuple[str, str, int]]:
    """Nackte Starts als (Datei, Funktion, Zeile). Verweise wie ``lauf = subprocess.run`` zählen mit."""
    baum = ast.parse(quelle)
    aliase = _aliase(baum)
    funde: list[tuple[str, str, int]] = []

    def besuche(knoten: ast.AST, ort: str, typ: bool = False) -> None:
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for kind in [*knoten.decorator_list, *knoten.args.defaults, *knoten.args.kw_defaults]:
                if kind is not None:
                    besuche(kind, ort)
            for kind in knoten.body:
                besuche(kind, knoten.name)
            return  # Annotationen nennen nur den Typ
        if isinstance(knoten, ast.AnnAssign):
            if knoten.value is not None:
                besuche(knoten.value, ort)
            return
        if isinstance(knoten, ast.Call):
            ziel = _ziel(knoten.func, aliase)
            if _ist_start(ziel):
                assert ziel is not None
                if not _fensterlos(knoten, ziel):
                    funde.append((rel, ort, knoten.lineno))
                for kind in [*knoten.args, *(kw.value for kw in knoten.keywords)]:
                    besuche(kind, ort)
                return
        elif isinstance(knoten, (ast.Attribute, ast.Name)) and _ist_start(_ziel(knoten, aliase)):
            if not isinstance(getattr(knoten, "ctx", None), ast.Store):
                funde.append((rel, ort, knoten.lineno))
            return
        for kind in ast.iter_child_nodes(knoten):
            besuche(kind, ort)

    besuche(baum, "<modul>")
    return funde


def _alle_funde() -> list[tuple[str, str, int]]:
    dateien = [SKILL / "to_spawn.py", *(SKILL / "to_spawn").rglob("*.py"), *(SKILL / "skripte").rglob("*.py")]
    funde: list[tuple[str, str, int]] = []
    for datei in sorted(dateien):
        funde += _funde_in(datei.read_text(encoding="utf-8"), datei.relative_to(SKILL).as_posix())
    return funde


@pytest.mark.parametrize(
    "quelle",
    [
        'import subprocess\ndef f():\n    subprocess.run(["git", "fetch"])',
        'import subprocess as sp\ndef f():\n    sp.check_output(["git"], text=True)',
        'from subprocess import Popen\ndef f():\n    Popen(["x"])',
        'from subprocess import run as r\ndef f(o):\n    r(["x"], **o)',
        'import subprocess\ndef f():\n    lauf = subprocess.Popen\n    lauf(["x"])',
        'import subprocess\ndef f():\n    subprocess.getoutput("git status")',
        'import os\ndef f():\n    os.system("git fetch")',
        'import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec("git")',
        'import subprocess\ndef f():\n    subprocess.run(["x"], **sonst_was())',
    ],
)
def test_pruefung_erkennt_nackten_start(quelle: str) -> None:
    assert _funde_in(quelle, "x.py"), quelle


@pytest.mark.parametrize(
    "quelle",
    [
        'import subprocess\nfrom to_spawn import prozessbaum\ndef f():\n    subprocess.run(["git"], **prozessbaum.fensterlos())',
        'import subprocess\ndef f(k):\n    subprocess.run(["git"], **{**fensterlos(), **k})',
        'import subprocess\ndef f():\n    subprocess.run(["schtasks"], creationflags=0x08000000)',
        'import subprocess\ndef f(p: subprocess.Popen[str]) -> subprocess.CompletedProcess[str]:\n    raise subprocess.TimeoutExpired("x", 1)',
    ],
)
def test_pruefung_laesst_fensterlosen_start_durch(quelle: str) -> None:
    assert _funde_in(quelle, "x.py") == [], quelle


def test_kein_nackter_unterprozess_start() -> None:
    """Neuer Start ohne ``**prozessbaum.fensterlos()`` → rot (#658). Ausnahmen nur mit Grund."""
    funde = _alle_funde()
    nackt = sorted(f"{rel}:{zeile} ({ort})" for rel, ort, zeile in funde if (rel, ort) not in AUSNAHMEN)
    assert nackt == [], "Start ohne **prozessbaum.fensterlos() (#658):\n" + "\n".join(nackt)
    veraltet = set(AUSNAHMEN) - {(rel, ort) for rel, ort, _ in funde}
    assert not veraltet, f"Ausnahme ohne Fund, bitte streichen: {sorted(veraltet)}"


#: Kind meldet sein Konsolenfenster (0 = keins). Mit Fix bleibt es 0, ohne Fix bekommt das Kind
#: eines konsolenlosen Elternprozesses eine eigene Konsole samt Fenster.
_SONDE = "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"

#: Elternprozess wie ``pythonw.exe``: ohne Konsole (``DETACHED_PROCESS``, nur hier im Test).
#: Versteckter Start (``SW_HIDE``), damit auch der Rot-Fall kein Fenster nach vorn reißt.
_ELTERN = textwrap.dedent(
    """
    import ctypes, json, subprocess, sys
    sys.path.insert(0, sys.argv[1])
    from to_spawn import konsole
    sonde = [sys.executable, "-c", sys.argv[2]]
    versteckt = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=0)
    kontrolle = subprocess.run(sonde, capture_output=True, text=True, startupinfo=versteckt, check=True)
    echt = konsole.lauf_daten(sonde, startupinfo=versteckt, check=True)
    print(json.dumps({
        "eltern": ctypes.windll.kernel32.GetConsoleWindow(),
        "kontrolle": int(kontrolle.stdout.strip()),
        "echt": int(echt.stdout.strip()),
    }))
    """
)


@pytest.mark.skipif(sys.platform != "win32", reason="Konsolenfenster gibt es nur unter Windows")
def test_kind_ohne_konsolenfenster_wenn_eltern_ohne_konsole() -> None:
    """Eltern ohne Konsole (wie die Hausmeister-Aufgabe) → 0 neue Konsolenfenster über den echten Weg."""
    lauf = subprocess.run(
        [sys.executable, "-c", _ELTERN, str(SKILL), _SONDE],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        creationflags=subprocess.DETACHED_PROCESS,  # Nachstellung pythonw, nie im Produktcode
    )
    assert lauf.returncode == 0, lauf.stderr
    stand = json.loads(lauf.stdout)
    assert stand["eltern"] == 0, f"Eltern hat doch eine Konsole: {stand}"
    assert stand["kontrolle"] != 0, f"Messung blind: Kind ohne Flag bekam kein Konsolenfenster: {stand}"
    assert stand["echt"] == 0, f"to_spawn-Unterprozess öffnete ein Konsolenfenster (#658): {stand}"


def test_fensterlos_ohne_ctypes_windll_liefert_leer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fehlt ``ctypes.windll`` bei simuliertem Windows (``sys.platform``/``_WINDOWS``) → ``{}`` statt Absturz."""
    from to_spawn import prozessbaum

    monkeypatch.setattr(prozessbaum.sys, "platform", "win32")
    monkeypatch.delattr(prozessbaum.ctypes, "windll", raising=False)
    assert prozessbaum.fensterlos() == {}
    monkeypatch.setattr(prozessbaum, "_WINDOWS", True)
    assert prozessbaum.fensterlos() == {}
