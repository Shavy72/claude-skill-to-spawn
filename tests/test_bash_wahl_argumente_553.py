"""#553: ``bash_wahl.lauf`` reicht jedes Argument Byte für Byte an bash durch.

Unter Windows zerlegt Git-Bash (MSYS) die Befehlszeile nach eigenen Regeln: in ``"…"`` wird
``\\\\`` zu ``\\``, ``'…'`` verliert die Anführungszeichen. Die Python-Standard-Quotierung
(``list2cmdline``) passt dazu nicht; ein ``python -c`` mit Windows-Pfad (``'C:\\\\Users…'``)
kam so als ``'C:\\Users…'`` an und brach mit ``SyntaxError … unicodeescape``.
"""

from __future__ import annotations

import shlex
import subprocess
import sys

import pytest

from to_spawn import bash_wahl

BS = "\\"
WERTE = [
    f"a{BS}{BS}b",
    f"a{BS}{BS}b c",
    f"a{BS}b c",
    BS,
    f"a{BS}",
    f"'x{BS}{BS}y'",
    "a*b c",
    "*",
    'a"b',
    f'{BS}"',
    "",
    "ä ö ü ß",
    "$HOME `x` %PATH%",
    "a\tb\nc",
    "{a,b}",
    "~",
]


@pytest.mark.parametrize("wert", WERTE)
def test_argument_kommt_unveraendert_an(wert: str) -> None:
    erg = bash_wahl.lauf(["-c", 'printf "%s" "$1"', "_", wert], capture_output=True, text=True, encoding="utf-8")
    assert erg.returncode == 0, erg.stderr
    assert erg.stdout == wert


def test_python_c_mit_windows_pfad_im_quelltext() -> None:
    """Der Fall aus der Komplett-Prüfung: Pfad per ``repr`` im ``python -c``-Quelltext."""
    pfad = f"C:{BS}Users{BS}x"
    code = f"import sys;sys.stdout.write({pfad!r})"
    erg = bash_wahl.lauf(
        ["-c", f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        zeitgrenze_s=60,
    )
    assert erg.returncode == 0, erg.stdout
    assert erg.stdout == pfad


def test_zeitgrenze_weg_reicht_argumente_ebenso_durch() -> None:
    erg = bash_wahl.lauf(
        ["-c", 'printf "%s" "$1"', "_", f"a{BS}{BS}b c"], stdout=subprocess.PIPE, text=True, zeitgrenze_s=60
    )
    assert erg.stdout == f"a{BS}{BS}b c"
