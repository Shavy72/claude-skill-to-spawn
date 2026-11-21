"""Stufe 0 Cache: war genau dieser Stand schon grün, läuft er nicht noch einmal (L3 3b-3, #102).

Schlüssel, Ablageort und Verfall leben nur hier. Der Aufrufer kennt drei Schritte:
``k = schluessel(repo, basis, ziel)`` vor dem Lauf, ``treffer(k)`` und nach dem Lauf
``merke(k, zeile)``. ``k is None`` (Schlüssel nicht bestimmbar) heißt immer „kein Treffer“ und
„nichts merken“ — der Cache ist fail-closed: Zweifel kosten einen Lauf, nie ein falsches Grün.

Schlüssel = SHA-256 über: Tree-SHA von ``ziel`` und ``basis`` (Stufe 1 prüft die Änderung gegen
``basis``), Inhalt der Lock- und Prüf-Konfig-Dateien ganz oben im Repo (auch ungetrackt oder per
``.gitignore`` versteckt — getrackte stecken ohnehin im Tree), ``PYTEST_ADDOPTS``, Quelltext des
Prüf-Pakets selbst und die Umgebung (Interpreter, installierte Pakete der Repo-venv bzw. des
laufenden Interpreters, ``node_modules/.package-lock.json``).

Ablage: eine JSON-Datei je Schlüssel im Nutzer-Cache-Ordner (``%LOCALAPPDATA%`` unter Windows,
sonst ``$XDG_CACHE_HOME`` bzw. ``~/.cache``) unter ``to-spawn/pruef/`` — nie im Arbeitsbaum.
Gemerkt wird nur das Urteil ``grün`` (nie rot, Gate-Fehler oder Wackler); nach ``VERFALL_S``
gilt ein Eintrag nicht mehr.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path
from typing import Any

log = logging.getLogger("to_spawn.pruef")

#: Neue Zahl, wenn sich Schlüssel- oder Dateiformat ändert: alte Einträge treffen dann nie.
FORMAT = "1"
#: So lange gilt ein grüner Eintrag (Sekunden).
VERFALL_S = 14 * 24 * 3600
#: Lock-Dateien ganz oben im Repo (Abhängigkeiten).
_LOCK = (
    "requirements.txt",
    "requirements-dev.txt",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
    "pdm.lock",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
)
#: Prüf-Konfig ganz oben im Repo (wie und was geprüft wird).
_KONFIG = (
    ".to-spawn/config.json",
    "pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "conftest.py",
    "package.json",
    "ruff.toml",
    ".ruff.toml",
)
#: Umgebungs-Merkmale, die ein Paket-Install verändert.
_UMGEBUNG = (".venv/pyvenv.cfg", "venv/pyvenv.cfg", "node_modules/.package-lock.json")
_PAKET = Path(__file__).resolve().parent


def ordner() -> Path:
    """Ablageort der Einträge (wird erst beim Merken angelegt)."""
    if sys.platform == "win32":
        basis = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        basis = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(basis) / "to-spawn" / "pruef"


def _tree(repo: Path, ref: str) -> str | None:
    try:
        erg = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{tree}}"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return (erg.stdout.strip() or None) if erg.returncode == 0 else None


def _datei(hasher: Any, pfad: Path, name: str) -> None:
    """Name + Inhalt (oder „fehlt“) in den Hash; unlesbar → ``OSError`` an den Aufrufer."""
    hasher.update(f"\0{name}\0".encode())
    if pfad.is_file():
        hasher.update(pfad.read_bytes())
    else:
        hasher.update(b"\0fehlt")


def _pakete(repo: Path) -> list[str]:
    """Installierte Pakete der Repo-venv(s), sonst des laufenden Interpreters (Name==Version)."""
    namen: list[str] = []
    for venv in (".venv", "venv"):
        for muster in ("lib/python*/site-packages/*.dist-info", "Lib/site-packages/*.dist-info"):
            namen += [f"{venv}:{p.name}" for p in (repo / venv).glob(muster)]
    if not namen:
        namen = [f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions()]
    return sorted(namen)


def schluessel(repo: Path, basis: str, ziel: str) -> str | None:
    """Schlüssel für den Stand ``ziel`` gegen ``basis``; ``None`` = nicht bestimmbar (kein Cache).

    Voraussetzung (prüft die Tür vorher): ``ziel`` ist ausgecheckt und der Arbeitsbaum sauber.
    """
    ziel_tree, basis_tree = _tree(repo, ziel), _tree(repo, basis)
    if ziel_tree is None or basis_tree is None:
        return None
    hasher = hashlib.sha256(f"to-spawn-pruef\0{FORMAT}\0{ziel_tree}\0{basis_tree}".encode())
    try:
        for name in (*_LOCK, *_KONFIG, *_UMGEBUNG):
            _datei(hasher, repo / name, name)
        for quelle in sorted(_PAKET.glob("*.py")):
            _datei(hasher, quelle, f"pruef/{quelle.name}")
        pakete = _pakete(repo)
    except (OSError, ValueError) as fehler:
        log.warning("Prüf-Cache: Schlüssel nicht bestimmbar (%s)", fehler)
        return None
    umgebung = [sys.executable, sys.version, os.environ.get("PYTEST_ADDOPTS", ""), *pakete]
    hasher.update("\0".join(umgebung).encode())
    return hasher.hexdigest()


def treffer(schluessel: str | None) -> bool:
    """War der Stand schon grün (Eintrag lesbar, passend, nicht verfallen)? Sonst ``False``."""
    if schluessel is None:
        return False
    datei = ordner() / f"{schluessel}.json"
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as fehler:
        log.warning("Prüf-Cache: Eintrag %s unlesbar, kein Treffer (%s)", datei, fehler)
        return False
    if not isinstance(daten, dict):
        return False
    zeit = daten.get("zeit")
    alter = time.time() - zeit if isinstance(zeit, int | float) else -1.0
    return daten.get("schluessel") == schluessel and daten.get("urteil") == "grün" and 0 <= alter <= VERFALL_S


def merke(schluessel: str | None, zeile: dict[str, Any]) -> None:
    """Merkt ein grünes Urteil; alles andere (und jeder Schreibfehler) bleibt ohne Folgen."""
    if schluessel is None or zeile.get("urteil") != "grün" or zeile.get("cache_treffer"):
        return
    eintrag = {"schluessel": schluessel, "urteil": "grün", "zeit": time.time(), "ziel": zeile.get("ziel")}
    ziel = ordner() / f"{schluessel}.json"
    try:
        ziel.parent.mkdir(parents=True, exist_ok=True)
        # Erst daneben schreiben, dann ersetzen: ein halber Eintrag ist nie lesbar.
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=ziel.parent, suffix=".tmp", delete=False) as datei:
            datei.write(json.dumps(eintrag, ensure_ascii=False))
        os.replace(datei.name, ziel)
    except OSError as fehler:
        log.warning("Prüf-Cache: Eintrag %s nicht geschrieben (%s)", ziel, fehler)
    # ponytail: alte Einträge bleiben liegen (je ~200 Byte); Aufräumen nach VERFALL_S, wenn es stört.
