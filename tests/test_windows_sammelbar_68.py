"""#68 (L1 P7a): Die Suite muss unter Windows sammelbar sein — und unter Linux bleiben.

Ein ungeschützter Import eines Plattform-Moduls (``fcntl`` & Co. gibt es nur unter
Unix, ``msvcrt`` & Co. nur unter Windows) auf Modulebene bricht das Sammeln der ganzen
Datei — und jeder Datei, die aus ihr importiert. Dieser Prüf-Test bricht den Build,
sobald so ein Import in ``to_spawn/``, ``skripte/``, ``tests/`` oder ``to_spawn.py``
auftaucht.

Geschützt heißt: im Rumpf einer Funktion (läuft erst beim Aufruf), in einem ``try`` mit
``except ImportError`` (oder breiter), in einem ``if`` über ``sys.platform`` /
``os.name`` / ``platform.system()`` bzw. ``TYPE_CHECKING`` — oder in Tests per
``pytest.importorskip`` (kein ``import``-Satz, fällt hier gar nicht auf).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
ORDNER = ("to_spawn", "skripte", "tests")

#: Module, die es nur auf einer Plattform gibt.
NUR_UNIX = frozenset(
    {
        "fcntl",
        "pwd",
        "grp",
        "resource",
        "termios",
        "tty",
        "pty",
        "posix",
        "crypt",
        "spwd",
        "syslog",
        "curses",
        "readline",
    }
)
NUR_WINDOWS = frozenset({"msvcrt", "winreg", "_winapi", "winsound", "nt"})
PLATTFORM_MODULE = NUR_UNIX | NUR_WINDOWS

#: Nur Importfehler zählen als Schutz; ``except Exception`` fängt zu viel (P7b, #79).
_SCHUTZ_AUSNAHMEN = frozenset({"ImportError", "ModuleNotFoundError"})
_PLATTFORM_AUSDRUECKE = frozenset({"sys.platform", "os.name", "platform.system()"})
_WINDOWS_WERTE = frozenset({"win32", "nt", "Windows"})


def _zweig_erlaubt(test: ast.expr) -> tuple[frozenset[str], frozenset[str]] | None:
    """(im if-Zweig, im else-Zweig) erlaubte Plattform-Module; ``None`` = Bedingung unbekannt.

    Erkennt ``sys.platform``/``os.name``/``platform.system()`` mit ``==``/``!=``,
    ``sys.platform.startswith("win")``, ``not …`` und ``TYPE_CHECKING`` (Rumpf läuft nie).
    """
    if isinstance(test, ast.Name | ast.Attribute) and ast.unparse(test).endswith("TYPE_CHECKING"):
        return PLATTFORM_MODULE, frozenset()
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        innen = _zweig_erlaubt(test.operand)
        return (innen[1], innen[0]) if innen else None
    windows: bool | None = None
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq | ast.NotEq)
        and ast.unparse(test.left) in _PLATTFORM_AUSDRUECKE
        and isinstance(test.comparators[0], ast.Constant)
    ):
        windows = (test.comparators[0].value in _WINDOWS_WERTE) == isinstance(test.ops[0], ast.Eq)
    elif (
        isinstance(test, ast.Call)
        and ast.unparse(test.func) == "sys.platform.startswith"
        and len(test.args) == 1
        and isinstance(test.args[0], ast.Constant)
    ):
        windows = test.args[0].value == "win"
    if windows is None:
        return None
    return (NUR_WINDOWS, NUR_UNIX) if windows else (NUR_UNIX, NUR_WINDOWS)


def _faengt_importfehler(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return False  # nacktes except: so breit wie BaseException (P7b)
    typen = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(ast.unparse(t).rsplit(".", 1)[-1] in _SCHUTZ_AUSNAHMEN for t in typen)


def _modulnamen(satz: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(satz, ast.ImportFrom):
        return [satz.module.split(".")[0]] if satz.module and satz.level == 0 else []
    return [a.name.split(".")[0] for a in satz.names]


def ungeschuetzte_importe(quelltext: str) -> list[tuple[int, str]]:
    """(Zeile, Modul) je Plattform-Import, der beim Laden des Moduls ungeschützt läuft."""
    treffer: list[tuple[int, str]] = []

    # ``erlaubt`` = Plattform-Module, die an dieser Stelle geschützt laden (P7b: je Zweig).
    def besuche(saetze: list[ast.stmt], erlaubt: frozenset[str]) -> None:
        geschuetzt = erlaubt
        for satz in saetze:
            if isinstance(satz, ast.Import | ast.ImportFrom):
                treffer.extend(
                    (satz.lineno, m) for m in _modulnamen(satz) if m in PLATTFORM_MODULE and m not in erlaubt
                )
            elif isinstance(satz, ast.FunctionDef | ast.AsyncFunctionDef):
                continue  # Rumpf läuft erst beim Aufruf
            elif isinstance(satz, ast.If):
                zweige = _zweig_erlaubt(satz.test) or (frozenset(), frozenset())
                besuche(satz.body, erlaubt | zweige[0])
                besuche(satz.orelse, erlaubt | zweige[1])
            elif isinstance(satz, ast.Try | ast.TryStar):
                besuche(
                    satz.body,
                    PLATTFORM_MODULE if any(_faengt_importfehler(h) for h in satz.handlers) else erlaubt,
                )
                for h in satz.handlers:
                    besuche(h.body, geschuetzt)
                besuche(satz.orelse, geschuetzt)
                besuche(satz.finalbody, geschuetzt)
            elif isinstance(
                satz,
                ast.ClassDef | ast.With | ast.AsyncWith | ast.For | ast.AsyncFor | ast.While,
            ):
                besuche(satz.body, geschuetzt)
                besuche(getattr(satz, "orelse", []), geschuetzt)

    besuche(ast.parse(quelltext).body, frozenset())
    return treffer


def _dateien() -> list[Path]:
    dateien = [WURZEL / "to_spawn.py"]
    for ordner in ORDNER:
        dateien.extend(sorted((WURZEL / ordner).rglob("*.py")))
    return [d for d in dateien if d.is_file()]


def test_selbstcheck_erkennt_schutzformen() -> None:
    quelle = (
        "import fcntl\n"
        "try:\n    import pwd\nexcept ImportError:\n    pwd = None\n"
        "if sys.platform == 'win32':\n    import msvcrt\nelse:\n    import termios\n"
        "def f():\n    import grp\n"
        "try:\n    import resource\nexcept ValueError:\n    pass\n"
        "from fcntl import flock\n"
    )
    assert ungeschuetzte_importe(quelle) == [
        (1, "fcntl"),
        (13, "resource"),
        (16, "fcntl"),
    ]


def test_kein_ungeschuetzter_plattform_import_auf_modulebene() -> None:
    funde = [
        f"{datei.relative_to(WURZEL).as_posix()}:{zeile} import {modul}"
        for datei in _dateien()
        for zeile, modul in ungeschuetzte_importe(datei.read_text(encoding="utf-8"))
    ]
    assert not funde, (
        "Plattform-Modul auf Modulebene ungeschützt importiert — bricht das Sammeln unter "
        "dem anderen Betriebssystem. In Tests `pytest.importorskip(...)`, im Skill-Code "
        "`try/except ImportError` oder `if sys.platform`:\n" + "\n".join(funde)
    )


#: Testdateien, die unter Windows am fehlenden ``fcntl`` scheiterten (Issue #68) —
#: samt der zwei, die aus ``test_aufpasser_236_fix`` importieren.
_FCNTL_DATEIEN = (
    "tests/test_docker_reste_1372.py",
    "tests/test_aufpasser_236_fix.py",
    "tests/test_aufpasser_236_nachfix.py",
    "tests/test_aufpasser_236_review.py",
)


def test_ohne_fcntl_ueberspringt_statt_sammelfehler() -> None:
    """Simulation Windows: ``fcntl`` fehlt → die Linux-Dateien werden übersprungen, nicht rot gesammelt."""
    # ``to_spawn.leitstand`` wählt per ``sys.platform`` (unter Windows ``msvcrt``) — das kann
    # die Simulation unter Linux nicht nachstellen, also vorher laden; der Skill-Code ist
    # ohnehin durch den AST-Check oben abgedeckt. Geprüft wird hier nur die Test-Seite.
    code = (
        "import sys\n"
        "import to_spawn.leitstand\n"
        "sys.modules['fcntl'] = None\n"
        "import pytest\n"
        f"raise SystemExit(pytest.main(['--co', '-q', '-p', 'no:cacheprovider', *{list(_FCNTL_DATEIEN)!r}]))\n"
    )
    ergebnis = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(WURZEL),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert "error" not in ausgabe.lower(), ausgabe[-3000:]
    # Exit 5 = nichts gesammelt (alles übersprungen) — das ist hier der Soll-Zustand.
    assert ergebnis.returncode in (0, 5), ausgabe[-3000:]


def test_kein_import_ueber_paketname_tests() -> None:
    """Ohne ``tests/__init__.py`` überdeckt ein fremdes Paket ``tests`` (site-packages) den Ordner (#68).

    Einheitlicher Weg der Suite: Nachbar-Testmodule direkt (``from test_nest_210 import …``),
    so wie es die übrigen Dateien schon tun.
    """
    funde: list[str] = []
    for datei in sorted((WURZEL / "tests").rglob("*.py")):
        for knoten in ast.walk(ast.parse(datei.read_text(encoding="utf-8"))):
            namen = (
                [knoten.module or ""]
                if isinstance(knoten, ast.ImportFrom) and knoten.level == 0
                else [a.name for a in knoten.names]
                if isinstance(knoten, ast.Import)
                else []
            )
            funde.extend(
                f"{datei.relative_to(WURZEL).as_posix()}:{knoten.lineno} {n}"
                for n in namen
                if n == "tests" or n.startswith("tests.")
            )
    assert not funde, "Import über Paketname `tests`:\n" + "\n".join(funde)


def test_selbstcheck_schutz_nur_passender_zweig_und_nur_importfehler() -> None:
    """P7b (#79): ``if sys.platform`` schützt nur den Zweig der passenden Plattform;
    ``except Exception`` ist kein Import-Schutz (fängt zu viel, versteckt echte Fehler)."""
    quelle = (
        "if sys.platform != 'win32':\n    import msvcrt\nelse:\n    import fcntl\n"
        "if os.name == 'nt':\n    import msvcrt\nelse:\n    import fcntl\n"
        "if sys.platform.startswith('win'):\n    import winreg\n"
        "if TYPE_CHECKING:\n    import fcntl\n"
        "try:\n    import fcntl\nexcept Exception:\n    fcntl = None\n"
        "if sys.platform == 'win32' and x:\n    import msvcrt\n"
        "try:\n    import pwd\nexcept:\n    pass\n"
    )
    assert ungeschuetzte_importe(quelle) == [
        (2, "msvcrt"),
        (4, "fcntl"),
        (14, "fcntl"),
        (18, "msvcrt"),
        (20, "pwd"),
    ]
