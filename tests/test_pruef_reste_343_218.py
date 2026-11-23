"""Reste #343 (Prüf-Cache) und #218 (Prüf-Auswahl): echtes git-Repo, echter pytest-Lauf, echtes ``.pyc``.

- #343 Punkt 1: ``node_modules`` ohne Paketliste (pnpm/yarn/bun) bleibt ohne Schlüssel, meldet aber
  nur ``info`` statt bei jedem Lauf ``warning``.
- #343 Punkt 3: ``.pyc`` mit ``--invalidation-mode unchecked-hash`` lädt Python ohne Abgleich mit der
  Quelle — es ist kein Werkzeug-Rest (zählt als schmutzig bzw. im Schlüssel).
- #218 Punkt 3/4: Test-Setup (``vitest.setup.*``, ``jest.setup.*``, ``setupTests.*``) und
  ``requirements/``-Ordner bzw. ``*.in`` sind R3.
- #218 Punkt 6: nach einem ``gate-fehler`` prüft der nächste Lauf voll (LF).
"""

from __future__ import annotations

import logging
import py_compile
from pathlib import Path

import pytest
from test_pruef_buendel_238_224_204_246 import _ausschliessen, _schreibe
from test_pruef_schmutz_218 import _code_commit, _pruef_stufe, repo  # noqa: F401 — ``repo`` ist ein Fixture
from test_pruef_tuer_3b import _probe_py

import to_spawn.pruef as pruef
from to_spawn.pruef import cache, regeln, sofort

# --- #343 Punkt 1: ohne Paketliste nur ``info`` --------------------------------------------------


def test_node_modules_ohne_paketliste_meldet_info_statt_warnung(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    probe = _probe_py(tmp_path / "py")
    _ausschliessen(probe, "node_modules/")
    _schreibe(probe, "node_modules/.modules.yaml", "layoutVersion: 5\n")
    _schreibe(probe, "node_modules/paket/index.js", "1")
    with caplog.at_level(logging.INFO, logger="to_spawn.pruef"):
        assert cache.schluessel(probe, "base", "HEAD") is None  # weiter fail-closed: kein Schlüssel
    eigene = [r for r in caplog.records if r.name == "to_spawn.pruef"]
    assert not [r for r in eigene if r.levelno >= logging.WARNING], [r.getMessage() for r in eigene]
    assert any("ohne .package-lock.json" in r.getMessage() and r.levelno == logging.INFO for r in eigene)


def test_unlesbare_versteckte_datei_warnt_weiter(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Nur „ohne Paketliste“ ist leise; echte Lesefehler bleiben ``warning``."""
    probe = _probe_py(tmp_path / "py")
    _ausschliessen(probe, "ign/")
    (probe / "ign" / "ordner").mkdir(parents=True)
    (probe / "ign" / "link").symlink_to(probe / "ign" / "ordner")
    with caplog.at_level(logging.INFO, logger="to_spawn.pruef"):
        assert cache.schluessel(probe, "base", "HEAD") is None
    assert any(r.levelno == logging.WARNING for r in caplog.records if r.name == "to_spawn.pruef")


# --- #343 Punkt 3: ``unchecked-hash``-Bytecode ist kein Rest -------------------------------------


def _pyc(ordner: Path, modus: py_compile.PycInvalidationMode) -> str:
    quelle = _schreibe(ordner, "app/werk.py", "def eins() -> int:\n    return 1\n")
    ziel = ordner / "app" / "__pycache__" / "werk.cpython-3.pyc"
    py_compile.compile(str(quelle), cfile=str(ziel), invalidation_mode=modus, doraise=True)
    return "app/__pycache__/werk.cpython-3.pyc"


def test_unchecked_hash_pyc_ist_kein_werkzeug_rest(tmp_path: Path) -> None:
    pfad = _pyc(tmp_path, py_compile.PycInvalidationMode.UNCHECKED_HASH)
    assert not cache.ist_werkzeug_rest(tmp_path, pfad)
    assert not cache.ist_werkzeug_rest(tmp_path, "app/__pycache__/")


@pytest.mark.parametrize(
    "modus", [py_compile.PycInvalidationMode.TIMESTAMP, py_compile.PycInvalidationMode.CHECKED_HASH]
)
def test_gepruefter_pyc_bleibt_werkzeug_rest(tmp_path: Path, modus: py_compile.PycInvalidationMode) -> None:
    pfad = _pyc(tmp_path, modus)
    assert cache.ist_werkzeug_rest(tmp_path, pfad)
    assert cache.ist_werkzeug_rest(tmp_path, "app/__pycache__/")


def test_unchecked_hash_pyc_im_baum_erzwingt_voll_mit_ab(repo: Path) -> None:  # noqa: F811
    basis = _code_commit(repo)
    _pyc(repo, py_compile.PycInvalidationMode.UNCHECKED_HASH)
    _schreibe(repo, "app/werk.py", "def eins() -> int:\n    return 1  # neu\n")  # Quelle wie im Commit
    assert sofort.ungespeichert(repo) == ["app/__pycache__/"]
    stufe = _pruef_stufe(repo, basis)
    assert stufe.name == "voll" and stufe.grund.startswith("AB"), stufe.grund


# --- #218 Punkt 3/4: Test-Setup und ``requirements/`` sind R3 -----------------------------------


@pytest.mark.parametrize(
    "pfad",
    [
        "vitest.setup.ts",
        "web/vitest.setup.js",
        "src/setupTests.ts",
        "setupTests.js",
        "jest.setup.js",
        "requirements/dev.txt",
        "requirements.in",
        "requirements/base.in",
        "web/requirements/test.txt",
    ],
)
@pytest.mark.parametrize("adapter", ["py", "node"])
def test_setup_und_requirements_ordner_sind_r3(pfad: str, adapter: str) -> None:
    grund = regeln.vorab([pfad], adapter, [])
    assert grund.startswith("R3"), grund


def test_r3_trifft_keinen_code_der_nur_so_heisst() -> None:
    assert regeln.vorab(["app/requirements.py"], "py", []) == ""


# --- #218 Punkt 6: ``gate-fehler`` zählt für LF ------------------------------------------------


def test_nach_gate_fehler_prueft_der_naechste_lauf_voll(repo: Path) -> None:  # noqa: F811
    basis = _code_commit(repo)
    urteil = pruef.pruefen(repo, basis, "gibt-es-nicht")
    assert urteil.zeile["urteil"] == "gate-fehler"
    stufe = _pruef_stufe(repo, basis)
    assert stufe.name == "voll" and stufe.grund.startswith("LF"), stufe.grund
