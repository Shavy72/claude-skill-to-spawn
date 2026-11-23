"""Prüf-Bündel #238, #224, #204, #246: Prüf-Ausnahme, Prüf-Cache und Prüftor ohne falsches Grün.

#238 M3: Laufdatei-Muster greifen nicht über Ordnergrenzen (``thermo_x/tief/d.json`` zählt).
#238 M6: ein ``__pycache__`` in einem versteckten Ordner zählt mit seinem Nicht-Bytecode-Inhalt.
#224 M1: ``node_modules/`` ohne ``.package-lock.json`` (oben wie tief) → kein Cache-Schlüssel.
#224 M2: das Ausführbar-Bit versteckter Dateien zählt im Schlüssel.
#204 M1/N1: Konfig mit ``.txt`` (``requirements.txt`` …) ist nie Doku; Unlesbares und Shebang nur in der Basis.
#246: ein Symlink, den das System nur als Textdatei auscheckt (Windows ohne Symlink-Recht), prüft voll.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from test_pruef_cache_reste_184 import _ausschliessen
from test_pruef_schmutz_218 import _code_commit, _pruef_stufe, repo  # noqa: F401 — ``repo`` ist ein Fixture
from test_pruef_tuer_3b import _probe_py

import test_pruef_sofort_3b as sofort_test
from to_spawn.pruef import cache, sofort


def _git(ordner: Path, *args: str) -> str:
    erg = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
        text=True,
    )
    return erg.stdout.strip()


def _schreibe(ordner: Path, pfad: str, text: str) -> Path:
    ziel = ordner / pfad
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(text, encoding="utf-8")
    return ziel


# --- #238 M3: Muster je Pfadteil ---------------------------------------------------------------


@pytest.mark.parametrize(
    "pfad",
    [
        ".to-spawn/thermo_x/tief/d.json",
        ".to-spawn/stop-7/d.json",
        ".to-spawn/umzug-anfrage-3/tief/d.json",
        ".to-spawn/thermo_5/",
        ".to-spawn/stop-7/",
    ],
)
def test_laufmuster_greift_nicht_ueber_ordnergrenzen(tmp_path: Path, pfad: str) -> None:
    assert not cache.ist_werkzeug_rest(tmp_path, pfad)


@pytest.mark.parametrize(
    "pfad",
    [
        ".to-spawn/thermo_5/teil-1.json",
        ".to-spawn/thermo_5/lauf.json",
        ".to-spawn/stop-7",
        ".to-spawn/umzug-anfrage-3",
        ".to-spawn/folgerunde-2.log",
        ".to-spawn/abschluss_12_paket.json",
        ".to-spawn/rueckblick/12/auftrag.md",
        ".to-spawn/sessions/",
    ],
)
def test_echte_laufdateien_bleiben_rest(tmp_path: Path, pfad: str) -> None:
    assert cache.ist_werkzeug_rest(tmp_path, pfad)


def test_datei_im_unterordner_eines_laufmusters_erzwingt_voll(repo: Path) -> None:  # noqa: F811
    basis = _code_commit(repo)
    _schreibe(repo, ".to-spawn/thermo_x/tief/d.json", '"rot"\n')
    stufe = _pruef_stufe(repo, basis)
    assert stufe.name == "voll", stufe.grund
    assert stufe.grund.startswith("AB"), stufe.grund


def test_versteckte_datei_unter_laufmuster_ordner_zaehlt_im_schluessel(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py")
    _ausschliessen(probe, ".to-spawn/")
    daten = _schreibe(probe, ".to-spawn/thermo_x/tief/d.json", "1")
    _schreibe(probe, ".to-spawn/thermo_x/teil-1.json", "1")
    vorher = cache.schluessel(probe, "base", "HEAD")
    assert vorher is not None
    _schreibe(probe, ".to-spawn/thermo_x/teil-1.json", "2")  # echte Laufdatei: zählt nicht
    assert cache.schluessel(probe, "base", "HEAD") == vorher
    daten.write_text("2", encoding="utf-8")
    assert cache.schluessel(probe, "base", "HEAD") not in (None, vorher)


# --- #238 M6: ``__pycache__`` beim Durchlaufen nach derselben Regel ---------------------------


def test_fremde_datei_im_pycache_eines_versteckten_ordners_zaehlt(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py")
    _ausschliessen(probe, "ign/")
    _schreibe(probe, "ign/notiz.md", "x")
    fremd = _schreibe(probe, "ign/__pycache__/d.txt", "1")
    (probe / "ign" / "__pycache__" / "m.cpython-311.pyc").write_bytes(b"\0")
    vorher = cache.schluessel(probe, "base", "HEAD")
    assert vorher is not None
    (probe / "ign" / "__pycache__" / "m.cpython-311.pyc").write_bytes(b"\1")  # Bytecode: zählt nicht
    assert cache.schluessel(probe, "base", "HEAD") == vorher
    fremd.write_text("2", encoding="utf-8")
    assert cache.schluessel(probe, "base", "HEAD") not in (None, vorher)


# --- #224 M1: ``node_modules`` ohne Paketliste ist fail-closed ---------------------------------


@pytest.mark.parametrize("ordner", ["node_modules", "web/sub/node_modules", "ign/node_modules"])
def test_node_modules_ohne_paketliste_gibt_keinen_schluessel(tmp_path: Path, ordner: str) -> None:
    probe = _probe_py(tmp_path / "py")
    _ausschliessen(probe, "node_modules/")
    _ausschliessen(probe, "ign/")
    _schreibe(probe, f"{ordner}/paket/index.js", "1")
    assert cache.schluessel(probe, "base", "HEAD") is None
    _schreibe(probe, f"{ordner}/.package-lock.json", '{"v": 1}')
    vorher = cache.schluessel(probe, "base", "HEAD")
    assert vorher is not None
    assert cache.schluessel(probe, "base", "HEAD") == vorher  # unverändert: weiter Treffer möglich
    _schreibe(probe, f"{ordner}/.package-lock.json", '{"v": 2}')
    assert cache.schluessel(probe, "base", "HEAD") not in (None, vorher)


# --- #224 M2: Ausführbar-Bit versteckter Dateien ----------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="Windows kennt kein Ausführbar-Bit")
def test_ausfuehrbar_bit_versteckter_datei_zaehlt(tmp_path: Path) -> None:
    probe = _probe_py(tmp_path / "py")
    _ausschliessen(probe, "ign/")
    skript = _schreibe(probe, "ign/s.sh", "echo ok\n")
    vorher = cache.schluessel(probe, "base", "HEAD")
    assert vorher is not None
    assert cache.schluessel(probe, "base", "HEAD") == vorher
    skript.chmod(0o755)
    assert cache.schluessel(probe, "base", "HEAD") not in (None, vorher)


# --- #204: Konfig mit ``.txt``, Unlesbares, Shebang nur in der Basis ---------------------------


def _doku_repo(ordner: Path, dateien: dict[str, str]) -> Path:
    ordner.mkdir()
    for pfad, text in dateien.items():
        _schreibe(ordner, pfad, text)
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "base")
    _git(ordner, "tag", "base")
    return ordner


@pytest.mark.parametrize(
    "pfad",
    [
        "requirements.txt",
        "requirements-dev.txt",
        "constraints.txt",
        "web/CMakeLists.txt",
        "docs/requirements.txt",
        "Requirements.TXT",
    ],
)
def test_konfig_mit_txt_endung_ist_nie_doku(tmp_path: Path, pfad: str) -> None:
    probe = _doku_repo(tmp_path / "r", {pfad: "paket==1\n", "docs/notiz.txt": "hallo\n"})
    assert not sofort._ist_doku_datei(probe, "base", pfad)
    assert sofort._ist_doku_datei(probe, "base", "docs/notiz.txt")


def test_requirements_unter_doku_muster_prueft_voll(tmp_path: Path) -> None:
    probe = sofort_test._angriff(
        tmp_path / "req",
        {"requirements.txt": "ok\n", "app.py": _liest("requirements.txt")},
    )
    sofort_test._erklaere(probe, ["docs/**", "*.txt"])
    sofort_test._commit(probe, "requirements.txt", "kaputt\n")
    sofort_test._voll_rot(probe, sofort.NICHT_NUR_DOKU)


def _liest(ziel: str) -> str:
    return f'from pathlib import Path\ndef f():\n    return (Path(__file__).parent / "{ziel}").read_text().strip()\n'


def test_unlesbare_doku_datei_gilt_als_code(tmp_path: Path) -> None:
    probe = _doku_repo(tmp_path / "u", {"README.md": "# x\n"})
    (probe / "docs" / "x.md").mkdir(parents=True)  # Ordner statt Datei: open() scheitert mit OSError
    assert not sofort._ist_doku_datei(probe, "base", "docs/x.md")


def test_shebang_nur_im_basis_stand_gilt_als_code(tmp_path: Path) -> None:
    probe = _doku_repo(tmp_path / "s", {"docs/start.txt": "#!/usr/bin/env python3\nprint('x')\n"})
    _schreibe(probe, "docs/start.txt", "nur Text\n")
    _git(probe, "commit", "-qam", "ohne Shebang")
    assert not sofort._ist_doku_datei(probe, "base", "docs/start.txt")
    assert sofort._ist_doku_datei(probe, "HEAD", "docs/start.txt")


# --- #246: Symlink, der nur als Textdatei ausgecheckt ist --------------------------------------


def test_symlink_als_textdatei_prueft_voll(tmp_path: Path) -> None:
    probe = sofort_test._angriff(tmp_path / "f6t", sofort_test._F6)
    _git(probe, "config", "core.symlinks", "false")  # wie Windows ohne Symlink-Recht
    _schreibe(probe, "vorlage.txt", "doku/x.md")
    blob = _git(probe, "hash-object", "-w", "vorlage.txt")
    _git(probe, "update-index", "--add", "--cacheinfo", f"120000,{blob},vorlage.txt")
    _git(probe, "commit", "-qm", "link als Text")
    assert not (probe / "vorlage.txt").is_symlink()
    sofort_test._erklaere(probe, ["doku/**"])
    sofort_test._commit(probe, "doku/x.md", "bad\n")
    assert sofort._nicht_nur_doku(probe, "base", ["doku/x.md"]) == sofort.SYMLINK
    sofort_test._voll_rot(probe, sofort.SYMLINK)
