"""Stufe 0 Cache: war genau dieser Stand schon grün, läuft er nicht noch einmal (L3 3b-3, #102).

Schlüssel, Ablageort und Verfall leben nur hier. Der Aufrufer kennt drei Schritte:
``k = schluessel(repo, basis, ziel)`` vor dem Lauf, ``treffer(k)`` und nach dem Lauf
``merke(k, zeile)``. ``k is None`` (Schlüssel nicht bestimmbar) heißt immer „kein Treffer“ und
„nichts merken“ — der Cache ist fail-closed: Zweifel kosten einen Lauf, nie ein falsches Grün.

Schlüssel = SHA-256 über: Tree-SHA von ``ziel`` und ``basis`` (Stufe 1 prüft die Änderung gegen
``basis``), Inhalt der Lock- und Prüf-Konfig-Dateien ganz oben im Repo (auch ungetrackt oder per
``.gitignore`` versteckt — getrackte stecken ohnehin im Tree), Name + Ausführbar-Bit + Inhalt jeder per
``.gitignore``/``info/exclude`` versteckten Datei, die kein Werkzeug-Rest ist (``ist_werkzeug_rest``)
(mehr als ``_VERSTECKT_MAX_DATEIEN`` Dateien oder ``_VERSTECKT_MAX_BYTES`` → kein Schlüssel;
versteckter Symlink auf eine Datei zählt mit dem Inhalt des Ziels, auf einen Ordner oder ins Leere
→ kein Schlüssel), jedes ``node_modules/`` (oben wie tief) nur über seine ``.package-lock.json``
(fehlt sie → kein Schlüssel, ``_paketliste``), Quelltext des Prüf-Pakets selbst, die ganze Prozess-Umgebung (alle Variablen mit Wert; nur die
flüchtigen aus ``_FLUECHTIG`` zählen ohne Wert) und der Interpreter samt installierten Paketen
der Repo-venv bzw. des laufenden Interpreters.

Nicht abgedeckt (Grenze): Dateien außerhalb des Repos (z. B. ``~/.config``, ein ``.env`` im
Elternordner, das Ziel eines getrackten Symlinks — Git speichert nur den Link-Text), Inhalt der
Werkzeug-Ordner (Caches; Installationen nur über die Paketliste), Werte der Variablen in
``_FLUECHTIG``, Zeit/Netz/Zufall im Test selbst. Ignorierte Laufausgaben (z. B. ``.review/``,
``.coverage``, ``*.log``) stehen mit im Schlüssel: schreibt der Lauf sie mit wechselndem Inhalt
neu, trifft der Cache in diesem Repo nie (kein falsches Grün, nur kein Gewinn).

Ablage: eine JSON-Datei je Schlüssel im Nutzer-Cache-Ordner (``%LOCALAPPDATA%`` unter Windows,
sonst ``$XDG_CACHE_HOME`` bzw. ``~/.cache``) unter ``to-spawn/pruef/`` — nie im Arbeitsbaum.
Gemerkt wird nur das Urteil ``grün`` (nie rot, Gate-Fehler oder Wackler); nach ``VERFALL_S``
gilt ein Eintrag nicht mehr.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import stat
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path
from typing import Any

log = logging.getLogger("to_spawn.pruef")

#: Neue Zahl, wenn sich Schlüssel- oder Dateiformat ändert: alte Einträge treffen dann nie.
FORMAT = "2"
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
_UMGEBUNG = (".venv/pyvenv.cfg", "venv/pyvenv.cfg")
_PAKET = Path(__file__).resolve().parent
#: Ungetrackte Caches: ändern den geprüften Code nicht, gelten in jeder Ordnertiefe.
CACHE_ORDNER = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"})
#: Ungetrackte Installationen: nur ganz oben im Repo (``src/venv/`` kann Code sein → zählt).
INSTALL_ORDNER = frozenset({".venv", "venv", "node_modules"})
#: Laufdateien, die to-spawn selbst unter ``.to-spawn/`` schreibt (Sitzungen, Protokoll, Marker).
#: Alles andere dort (auch die Konfig) zählt: ein Test kann es lesen (#218).
_LAUF_WURZEL = ".to-spawn"
#: Ordner, deren ganzer Unterbaum Laufdaten ist.
_LAUF_ORDNER = frozenset({"sessions", "pruef", "bau_log", "rueckblick", "leitstand"})
#: Einzelne Dateien als ``fnmatch``-Muster, je Pfadteil geprüft: ``*`` greift nie über ``/`` (#238).
_LAUF_DATEIEN = tuple(
    tuple(muster.split("/"))
    for muster in (
        "thermo_*/*",
        "stop-*",
        "umzug-anfrage-*",
        "folgerunde-*.log",
        "abschluss_*_paket.json",
        "stand.json",
        "werkzeuge.json",
        "sandbox.json",
    )
)
#: Bytecode: der einzige Inhalt eines ``__pycache__/``, der nicht zählt.
_BYTECODE = (".pyc", ".pyo")
#: Kopf eines ``.pyc`` (PEP 552): Bytes 4–8 = Flags; Bit 0 Hash statt Stempel, Bit 1 Hash prüfen.
_PYC_FLAGS = slice(4, 8)
_PYC_UNGEPRUEFT = 0b01
#: ``node_modules`` (oben wie tief): statt aller Dateien zählt nur die Paketliste darin.
_MODULE = "node_modules"
_MODULE_LISTE = ".package-lock.json"
#: Deckel für versteckte Dateien im Schlüssel; darüber kein Schlüssel (voll laufen).
_VERSTECKT_MAX_DATEIEN = 2000
_VERSTECKT_MAX_BYTES = 32 * 1024 * 1024
#: Umgebungsvariablen, deren Wert je Shell/Terminal/Sitzung wechselt: zählt nur, ob sie gesetzt sind.
_FLUECHTIG = frozenset(
    {
        "_",
        "PWD",
        "OLDPWD",
        "SHLVL",
        "PYTEST_CURRENT_TEST",
        "TMUX",
        "TMUX_PANE",
        "STY",
        "WINDOW",
        "WINDOWID",
        "TERM_SESSION_ID",
        "WT_SESSION",
        "SSH_CLIENT",
        "SSH_CONNECTION",
        "SSH_TTY",
        "SSH_AUTH_SOCK",
        "XDG_SESSION_ID",
        "DBUS_SESSION_BUS_ADDRESS",
        "CLAUDE_PID",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_BRIDGE_SESSION_ID",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "CLAUDE_CODE_CHILD_NOTES_DIR",
    }
)


def ist_werkzeug_rest(repo: Path, pfad: str) -> bool:
    """Einzige Stelle für „Rest eines Werkzeugs, ändert den geprüften Stand nicht“ (#218).

    ``pfad`` relativ zum Repo mit ``/``; ein Ordner endet auf ``/`` (so melden ``git status``
    und ``git ls-files --directory`` neue Ordner). Rest sind: Laufdateien (``_ist_laufdatei``),
    Caches in jeder Tiefe, ``.venv``/``venv``/``node_modules`` nur ganz oben. Ein ``__pycache__``
    ist nur Rest, solange darin ausschließlich geprüfter Bytecode liegt (``_geprueft_bytecode``);
    dessen Inhalt prüft niemand (Grenze, #238: Python lädt ein ``.pyc`` aus ``__pycache__`` nur,
    wenn Stempel oder Hash zur Quelle passt — nur ein gezielt gefälschter Kopf ginge durch).
    Im Zweifel ``False``.
    """
    if _ist_laufdatei(pfad):
        return True
    teile = pfad.rstrip("/").split("/")
    if "__pycache__" in teile[:-1]:
        return _geprueft_bytecode(repo / pfad)
    if not pfad.endswith("/"):
        return False
    if teile[-1] == "__pycache__":
        try:
            return all(
                _geprueft_bytecode(Path(wurzel) / n)
                for wurzel, _, namen in os.walk(repo / pfad, onerror=_wirf)
                for n in namen
            )
        except OSError:
            return False
    return teile[-1] in CACHE_ORDNER or (len(teile) == 1 and teile[0] in INSTALL_ORDNER)


def _geprueft_bytecode(datei: Path) -> bool:
    """``*.pyc``/``*.pyo``, den Python nie ungeprüft statt der Quelle lädt; sonst ``False``.

    ``--invalidation-mode unchecked-hash`` (#343) lädt Python ohne Abgleich mit der Quelle:
    veralteter Bytecode liefe statt des Codes, darum zählt er wie Code. Unlesbar oder ohne
    vollständigen Kopf lädt Python ihn nie (übersetzt die Quelle neu) → bleibt Rest. Keine normale
    Datei (FIFO, Gerät, Socket, Symlink): ``open`` könnte ewig blockieren, zählt darum wie Code.
    """
    if not datei.name.endswith(_BYTECODE):
        return False
    try:
        if not stat.S_ISREG(datei.lstat().st_mode):
            return False
        with datei.open("rb") as roh:
            kopf = roh.read(_PYC_FLAGS.stop)
    except OSError:
        return True
    return len(kopf) < _PYC_FLAGS.stop or int.from_bytes(kopf[_PYC_FLAGS], "little") & 0b11 != _PYC_UNGEPRUEFT


def _ist_laufdatei(pfad: str) -> bool:
    """Laufdatei von to-spawn: Unterbaum eines ``_LAUF_ORDNER`` oder Datei nach ``_LAUF_DATEIEN``."""
    wurzel, _, rest = pfad.partition("/")
    if wurzel != _LAUF_WURZEL or not rest:
        return False
    teile = rest.split("/")
    if len(teile) > 1 and teile[0] in _LAUF_ORDNER:
        return True
    # Ordner-Einträge (``…/``) treffen nie ein Datei-Muster: ihr Inhalt wird einzeln geprüft.
    return not pfad.endswith("/") and any(
        len(muster) == len(teile) and all(map(fnmatch.fnmatchcase, teile, muster)) for muster in _LAUF_DATEIEN
    )


def _wirf(fehler: OSError) -> None:
    raise fehler


def _versteckte(repo: Path) -> list[str]:
    """Per ``.gitignore``/``info/exclude`` versteckte Dateien, sortiert; ``OSError`` = unklar.

    Ohne Werkzeug-Reste (``ist_werkzeug_rest``): alles andere kann pytest
    einsammeln, importieren oder ein Test lesen (versteckter Test, ``conftest.py``, ``.env``).
    """
    erg = subprocess.run(
        ["git", "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if erg.returncode != 0:
        raise OSError(f"git ls-files: {erg.stderr.decode(errors='replace').strip()}")
    dateien: list[str] = []
    for eintrag in erg.stdout.decode("utf-8", "surrogateescape").split("\0"):
        if not eintrag or ist_werkzeug_rest(repo, eintrag):
            continue
        if not eintrag.endswith("/"):
            dateien.append(eintrag)
            continue
        if eintrag.rstrip("/").rsplit("/", 1)[-1] == _MODULE:
            dateien.append(_paketliste(repo, eintrag))
            continue
        for wurzel, unter, namen in os.walk(repo / eintrag):
            ort = Path(wurzel).relative_to(repo).as_posix()
            links = [u for u in unter if os.path.islink(os.path.join(wurzel, u))]
            # Symlink auf Ordner betritt os.walk nicht: als Eintrag aufnehmen, ``_datei`` lehnt ihn ab.
            dateien += [f"{ort}/{u}" for u in links]
            dateien += [_paketliste(repo, f"{ort}/{u}") for u in unter if u == _MODULE and u not in links]
            unter[:] = [
                u
                for u in unter
                if u != _MODULE
                and u not in links
                and not ist_werkzeug_rest(repo, f"{ort}/{u}/")
            ]
            dateien += [f"{ort}/{n}" for n in namen if not ist_werkzeug_rest(repo, f"{ort}/{n}")]
            if len(dateien) > _VERSTECKT_MAX_DATEIEN:
                return dateien  # Deckel gesprengt: nicht weiter gehen, der Aufrufer gibt keinen Schlüssel
    return sorted(dateien)


class _OhnePaketliste(OSError):
    """Dauerzustand des Repos (pnpm/yarn/bun), kein Fehler: nur ``info``, nicht je Lauf ``warning`` (#343)."""


def _paketliste(repo: Path, ordner: str) -> str:
    """Einzige Regel für ``node_modules/`` (oben wie tief, #224): es zählt nur seine Paketliste.

    Fehlt die Liste (pnpm/yarn/bun, ``npm link``, Ordner mit Testdaten), ist der Inhalt unklar:
    ``_OhnePaketliste`` → kein Schlüssel statt eines Schlüssels, der Änderungen darin nicht sieht.
    """
    liste = f"{ordner.rstrip('/')}/{_MODULE_LISTE}"
    if not (repo / liste).is_file():
        raise _OhnePaketliste(f"{ordner.rstrip('/')}/ ohne {_MODULE_LISTE}")
    return liste


def _umwelt() -> str:
    """Ganze Umgebung (sortiert); flüchtige Variablen nur mit Namen, ohne Wert."""
    paare = sorted((k, "" if k in _FLUECHTIG else v) for k, v in os.environ.items())
    return json.dumps(paare)


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
    """Name + Inhalt + Ausführbar-Bit (oder „fehlt“) in den Hash; unlesbar → ``OSError`` an den Aufrufer."""
    hasher.update(f"\0{name}\0".encode())
    if pfad.is_file():
        hasher.update(pfad.read_bytes())
        hasher.update(b"\0x" if pfad.stat().st_mode & stat.S_IXUSR else b"\0-")
    elif pfad.is_symlink() or pfad.exists():
        # Ordner, Symlink auf Ordner oder ins Leere: Inhalt nicht im Hash → kein Schlüssel statt Grün.
        raise OSError(f"{name}: keine Datei (Ordner oder Symlink)")
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
        if (repo / _MODULE).is_dir():  # oben: Install-Ordner, kein Werkzeug-Rest-Durchlauf
            liste = _paketliste(repo, _MODULE)
            _datei(hasher, repo / liste, liste)
        for quelle in sorted(_PAKET.glob("*.py")):
            _datei(hasher, quelle, f"pruef/{quelle.name}")
        versteckt = _versteckte(repo)
        groesse = sum((repo / name).stat().st_size for name in versteckt if (repo / name).is_file())
        if len(versteckt) > _VERSTECKT_MAX_DATEIEN or groesse > _VERSTECKT_MAX_BYTES:
            log.info("Prüf-Cache: %d versteckte Dateien (%d Byte), kein Cache", len(versteckt), groesse)
            return None
        for name in versteckt:
            _datei(hasher, repo / name, f"versteckt/{name}")
        pakete = _pakete(repo)
    except _OhnePaketliste as fehler:
        log.info("Prüf-Cache: kein Cache, Paketliste fehlt (%s)", fehler)
        return None
    except (OSError, ValueError) as fehler:
        log.warning("Prüf-Cache: Schlüssel nicht bestimmbar (%s)", fehler)
        return None
    umgebung = [sys.executable, sys.version, _umwelt(), *pakete]
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
