"""#93: Test-Attrappen plattformneutral — eine Hilfe statt Shell-Skripte.

Wächter: keine Testdatei schreibt mehr eine ``/bin/sh``-Attrappe (läuft unter Windows
nicht); Attrappen entstehen nur über ``conftest.attrappe``. Die ``.sh``-Dateien unter
``tests/hilfen`` sind Kopien echter Produkt-Skripte, keine Attrappen, und bleiben außen vor.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from conftest import attrappe

TESTS = Path(__file__).resolve().parent
#: Zusammengesetzt, damit der Wächter sich nicht selbst findet.
SH_SHEBANG = "#!" + "/bin/sh"


def test_kein_sh_shebang_unter_tests() -> None:
    treffer = sorted(
        str(p.relative_to(TESTS))
        for p in TESTS.rglob("*.py")
        if p.name != Path(__file__).name and SH_SHEBANG in p.read_text(encoding="utf-8")
    )
    assert not treffer, f"{len(treffer)} Dateien mit {SH_SHEBANG}-Attrappe (conftest.attrappe nutzen): {treffer}"


def test_attrappe_laeuft_ueber_path(tmp_path: Path) -> None:
    protokoll = tmp_path / "aufrufe.txt"
    attrappe(
        tmp_path / "bin",
        "werkzeug",
        f"""
        import sys
        with open({str(protokoll)!r}, "a", encoding="utf-8") as f:
            f.write(" ".join(sys.argv[1:]) + "\\n")
        print("hallo " + sys.stdin.read().strip())
        sys.exit(3)
        """,
    )
    programm = shutil.which("werkzeug", path=str(tmp_path / "bin"))
    assert programm
    r = subprocess.run([programm, "a", "b"], check=False, input="welt", capture_output=True, text=True, timeout=30)
    assert (r.returncode, r.stdout.strip()) == (3, "hallo welt")
    assert protokoll.read_text(encoding="utf-8") == "a b\n"
