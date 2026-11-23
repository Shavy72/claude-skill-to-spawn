"""Folge zu #343 und #355 im Prüf-Werkzeugkasten: echtes ``.pyc``, echte FIFO, echter pytest-Lauf.

- #343: eine FIFO namens ``*.pyc`` in ``__pycache__`` hängt das Lesen des Kopfs nie auf und zählt
  wie Code (strengere Seite); gemischter ``__pycache__`` ist nur Rest, wenn ALLE ``.pyc`` geprüft
  sind; abgeschnittener Kopf stürzt nicht ab.
- #355 Teil 1: die ``to_spawn_pytest_cache_*``-Ordner des Python-Adapters sind nach Prozessende weg,
  nach Erfolg wie nach Fehler.
"""

from __future__ import annotations

import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest
from test_pruef_buendel_238_224_204_246 import _schreibe
from test_pruef_tuer_3b import _probe_py

from to_spawn.pruef import cache

SKILL = Path(__file__).resolve().parent.parent
FRIST_S = 20


def _pyc(ordner: Path, name: str, modus: py_compile.PycInvalidationMode) -> None:
    quelle = _schreibe(ordner, f"app/{name}.py", "def eins() -> int:\n    return 1\n")
    ziel = ordner / "app" / "__pycache__" / f"{name}.cpython-3.pyc"
    py_compile.compile(str(quelle), cfile=str(ziel), invalidation_mode=modus, doraise=True)


# --- #343: FIFO, gemischter Ordner, abgeschnittener Kopf -----------------------------------------


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO gibt es nur auf POSIX")
def test_fifo_als_pyc_haengt_nicht_und_zaehlt_als_code(tmp_path: Path) -> None:
    ordner = tmp_path / "app" / "__pycache__"
    ordner.mkdir(parents=True)
    os.mkfifo(ordner / "werk.cpython-3.pyc")
    # Eigener Prozess mit Zeitgrenze: ein Rückfall (``open`` auf die FIFO) hängt sonst den ganzen Lauf.
    code = (
        "import sys; from pathlib import Path; from to_spawn.pruef import cache; r = Path(sys.argv[1]); "
        "print(cache.ist_werkzeug_rest(r, 'app/__pycache__/werk.cpython-3.pyc'), "
        "cache.ist_werkzeug_rest(r, 'app/__pycache__/'))"
    )
    try:
        erg = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)],
            cwd=SKILL,
            capture_output=True,
            text=True,
            timeout=FRIST_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"FIFO als .pyc blockiert das Lesen (> {FRIST_S} s)")
    assert erg.returncode == 0, erg.stderr
    assert erg.stdout.split() == ["False", "False"]


def test_gemischter_pycache_ist_kein_rest(tmp_path: Path) -> None:
    _pyc(tmp_path, "alt", py_compile.PycInvalidationMode.TIMESTAMP)
    _pyc(tmp_path, "werk", py_compile.PycInvalidationMode.UNCHECKED_HASH)
    assert cache.ist_werkzeug_rest(tmp_path, "app/__pycache__/alt.cpython-3.pyc")
    assert not cache.ist_werkzeug_rest(tmp_path, "app/__pycache__/werk.cpython-3.pyc")
    # Ein einziger ungeprüfter Bytecode reicht, damit der ganze Ordner wie Code zählt.
    assert not cache.ist_werkzeug_rest(tmp_path, "app/__pycache__/")


@pytest.mark.parametrize("laenge", [0, 3, 7])
def test_abgeschnittener_pyc_kopf_stuerzt_nicht_ab(tmp_path: Path, laenge: int) -> None:
    # Ohne vollständigen Kopf lädt Python den Bytecode nie (übersetzt die Quelle neu) → Rest wie bisher.
    _schreibe(tmp_path, "app/__pycache__/kurz.cpython-3.pyc", "")
    (tmp_path / "app" / "__pycache__" / "kurz.cpython-3.pyc").write_bytes(b"\x01" * laenge)
    assert cache.ist_werkzeug_rest(tmp_path, "app/__pycache__/kurz.cpython-3.pyc")
    assert cache.ist_werkzeug_rest(tmp_path, "app/__pycache__/")


# --- #355 Teil 1: Python-Adapter räumt seine Cache-Ordner selbst ---------------------------------

_LAUF = """
import sys
from pathlib import Path
from to_spawn.pruef import adapter_py, ausfuehren

repo = Path(sys.argv[1])
adapter = adapter_py.PythonAdapter()
plan = adapter.voll(repo)
assert not plan.fehler, plan.fehler
nochmal = adapter.voll_nochmal_befehl(plan.befehl)
caches = [t.split("=", 1)[1] for t in plan.befehl + nochmal if t.startswith("cache_dir=")]
assert len(set(caches)) == 2 and all(Path(c).is_dir() for c in caches), caches
if sys.argv[2] == "fehler":
    raise RuntimeError("Lauf scheitert mitten in der Prüfung")
assert ausfuehren.lauf(plan.befehl, repo).code == 0
"""


@pytest.mark.parametrize("ausgang", ["erfolg", "fehler"])
def test_pytest_cache_ordner_sind_nach_dem_lauf_weg(tmp_path: Path, ausgang: str) -> None:
    probe = _probe_py(tmp_path / "py")
    temp = tmp_path / "temp"
    temp.mkdir()
    erg = subprocess.run(
        [sys.executable, "-c", _LAUF, str(probe), ausgang],
        cwd=SKILL,
        env={**os.environ, "TMPDIR": str(temp)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert erg.returncode == (0 if ausgang == "erfolg" else 1), erg.stderr
    if ausgang == "fehler":
        assert "Lauf scheitert" in erg.stderr
    assert not list(temp.glob("to_spawn_pytest_cache_*"))
