"""Stufe 1 Sofort-Prüfung und Stufe 2 „nur Doku“ des Prüf-Werkzeugkastens (Paket-intern, #100).

Schnittstelle zur Leiter: ``stufen(repo, basis) -> (liste, halt)``. ``liste`` sind die Stufen
„sofort“ und „doku“ fürs Protokoll; ``halt`` ≠ "" heißt, die übrigen Stufen laufen nicht (Grund
steht drin: „nur Doku“ oder schnell rot). Versteckt hier: welche Dateien geändert sind (git, auch
ungespeicherte und neue), welcher Linter zu welcher Endung gehört und wo er liegt.

„Nur Doku“ (M11) gibt es nur für Repos, die ihre Doku-Pfade selbst erklären: Schlüssel
``pruefen.doku_pfade`` in ``.to-spawn/config.json``, eine Liste von Mustern relativ zur
Repo-Wurzel mit ``/`` (``fnmatch``, Groß/klein zählt, ``*`` passt auch über ``/``), z. B.
``["docs/**", "README.md"]``. Es gilt die Konfig im Stand der Basis, nie die des geprüften Commits;
ändert der Commit die Konfig, prüft die Leiter voll. Warum: Ob Code eine Datei liest, lässt sich nicht erraten (Tests über
``git ls-files``, ``iterdir``, Symlinks, ``mimetypes``) — nur das Repo weiß es. Abkürzung nur, wenn
JEDE geänderte Datei (neu, gelöscht, bei Umbenennung alter und neuer Pfad) unter ein Muster fällt,
keine davon ein Symlink ist und kein getrackter Symlink auf sie zeigt. Fehlt der Schlüssel, ist er
ungültig oder die Konfig kaputt: nie Abkürzung, die Leiter prüft voll (fail-closed).

Kein falsches Grün: fehlt ein Linter oder ist unklar, was geändert ist, wird die Stufe
ausgelassen (``lief=False`` + Grund) und die Leiter prüft voll weiter.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from to_spawn import config
from to_spawn.pruef import ausfuehren, leiter

log = logging.getLogger("to_spawn.pruef")

NUR_DOKU = "nur Doku"
ROT_HALT = "Stufe sofort rot (schnell rot)"

NICHT_NUR_DOKU = "nicht nur Doku geändert"
OHNE_DOKU_PFADE = "Doku-Pfade nicht erklärt (pruefen.doku_pfade)"
DOKU_PFADE_UNGUELTIG = "pruefen.doku_pfade ungültig (Liste von Pfad-Mustern erwartet)"
SYMLINK = "Symlink geändert oder zeigt auf geänderte Datei"
SYMLINK_UNKLAR = "Symlinks unklar (git ls-files/ls-tree gescheitert)"
KONFIG_GEAENDERT = "Konfig geändert (.to-spawn/config.json)"
#: Geänderte Pfade (casefold), die die Abkürzung sperren: die Konfig selbst oder ihr Ordner als Symlink.
_KONFIG_PFADE = frozenset({config.KONFIG_PFAD.as_posix().casefold(), config.KONFIG_PFAD.parent.as_posix().casefold()})
#: git-Modus eines Symlinks.
_SYMLINK_MODUS = "120000"


@dataclass(frozen=True)
class _Linter:
    adapter: str
    name: str
    endungen: frozenset[str]
    finde: Callable[[Path], str | None]
    argumente: tuple[str, ...]


def _ruff(repo: Path) -> str | None:
    """ruff aus dem venv des Repos (Linux ``bin``, Windows ``Scripts``), sonst aus dem PATH."""
    venv = [str(repo / ordner / teil) for ordner in (".venv", "venv") for teil in ("bin", "Scripts")]
    return shutil.which("ruff", path=os.pathsep.join(venv)) or shutil.which("ruff")


def _eslint(repo: Path) -> str | None:
    """Nur das ESLint des Repos (``node_modules/.bin``, Windows ``eslint.cmd``) — es kennt die Konfig."""
    return shutil.which("eslint", path=str(repo / "node_modules" / ".bin"))


_LINTER = (
    # Nur harte Fehler (Syntax, undefinierte Namen), nicht der Stil des Repos: kein falsches Rot
    # in Repos, die ruff nicht nutzen. Repo-Konfig bleibt aktiv (requires-python für Syntax).
    _Linter(
        "py",
        "ruff",
        frozenset({".py", ".pyi"}),
        _ruff,
        ("check", "--no-cache", "--no-fix", "--quiet", "--select", "E9,F63,F7,F82"),
    ),
    _Linter(
        "node",
        "eslint",
        frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}),
        _eslint,
        (),
    ),
)


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        erg = subprocess.run(
            ["git", *args],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as fehler:
        return ausfuehren.NICHT_GESTARTET, str(fehler)
    return erg.returncode, erg.stdout if erg.returncode == 0 else erg.stderr


def geaendert(repo: Path, basis: str) -> list[str] | None:
    """Geänderte Pfade (``/``, relativ zum Repo) von ``basis`` bis Arbeitsbaum; ``None`` = unklar."""
    code, raus = _git(repo, "diff", "--name-only", "--no-renames", "-z", basis, "--")
    if code != 0:
        return None
    neu_code, neu = _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    if neu_code != 0:
        return None
    return sorted({pfad for pfad in (raus + neu).split("\0") if pfad})


def _doku_muster(repo: Path, basis: str, pfade: list[str]) -> tuple[list[str], str]:
    """(Muster, Grund): erklärte Doku-Pfade aus dem Stand ``basis``; leere Liste = keine Abkürzung.

    Einzige Stelle der Regel „der geprüfte Commit bestimmt nie selbst, wie streng er geprüft
    wird“: Die Konfig kommt aus ``basis`` (nie aus dem Arbeitsbaum, auch kein Symlink dorthin),
    und ändert der Commit sie selbst (oder ``.to-spawn`` als Ganzes), gibt es nie die Abkürzung.
    Fehlt sie in ``basis`` oder ist sie kaputt (Warnung): keine Muster, die Leiter prüft voll.
    """
    if any(pfad.casefold() in _KONFIG_PFADE for pfad in pfade):
        return [], KONFIG_GEAENDERT
    code, text = _git(repo, "show", f"{basis}:{config.KONFIG_PFAD.as_posix()}")
    if code != 0:
        return [], OHNE_DOKU_PFADE
    try:
        eigen = json.loads(text)
    except ValueError as fehler:
        log.warning("Konfig in %s unlesbar — keine Doku-Pfade: %s", basis, fehler)
        return [], OHNE_DOKU_PFADE
    pruefen = eigen.get("pruefen") if isinstance(eigen, dict) else None
    muster = pruefen.get("doku_pfade") if isinstance(pruefen, dict) else None
    if muster is None:
        return [], OHNE_DOKU_PFADE
    if not isinstance(muster, list) or not muster or not all(isinstance(m, str) and m for m in muster):
        log.warning("%s: %r", DOKU_PFADE_UNGUELTIG, muster)
        return [], DOKU_PFADE_UNGUELTIG
    return muster, ""


def _symlinks(repo: Path, basis: str) -> set[str] | None:
    """Getrackte Symlinks im Index und in ``basis`` (git-Modus 120000); ``None`` = unklar."""
    links: set[str] = set()
    for args in (("ls-files", "-s", "-z"), ("ls-tree", "-r", "-z", basis)):
        code, raus = _git(repo, *args)
        if code != 0:
            return None
        for eintrag in filter(None, raus.split("\0")):
            kopf, _, pfad = eintrag.partition("\t")
            if kopf.split(" ", 1)[0] == _SYMLINK_MODUS:
                links.add(pfad)
    return links


def _nicht_nur_doku(repo: Path, basis: str, pfade: list[str]) -> str:
    """Grund, warum die Abkürzung „nur Doku“ nicht gilt; "" = alle Pfade sind erklärte Doku."""
    muster, grund = _doku_muster(repo, basis, pfade)
    if not muster:
        return grund
    if not all(any(fnmatch.fnmatchcase(pfad, m) for m in muster) for pfad in pfade):
        return NICHT_NUR_DOKU
    links = _symlinks(repo, basis)
    if links is None:
        return SYMLINK_UNKLAR
    if any(pfad in links or (repo / pfad).is_symlink() for pfad in pfade):
        return SYMLINK
    geaenderte = [(repo / pfad).resolve() for pfad in pfade]
    for link in links:
        datei = repo / link
        if not datei.exists() and not datei.is_symlink():
            continue  # nur in basis, im Arbeitsbaum gelöscht — wäre selbst geändert (oben)
        if not datei.is_symlink():
            return SYMLINK  # Windows ohne Symlink-Recht: Ziel nicht auflösbar → im Zweifel voll
        ziel = datei.resolve()
        if any(ziel == pfad or ziel in pfad.parents for pfad in geaenderte):
            return SYMLINK
    return ""


def _ausgelassen(name: str, adapter: str, grund: str, werkzeug: str = "") -> leiter.Stufe:
    return leiter.Stufe(name, adapter, False, grund, 0.0, "", werkzeug, leiter.AUSGELASSEN, "")


def _lint(repo: Path, linter: _Linter, dateien: list[str]) -> leiter.Stufe:
    programm = linter.finde(repo)
    if programm is None:
        return _ausgelassen(
            "sofort",
            linter.adapter,
            f"Werkzeug fehlt ({linter.name})",
            f"{linter.name}@?",
        )
    version = ausfuehren.lauf([programm, "--version"], repo)
    werkzeug = f"{linter.name}@{(version.ausgabe.split() or ['?'])[-1] if version.code == 0 else '?'}"
    # ponytail: alle Dateien in einem Aufruf; unter Windows bricht das ab ~32k Zeichen Befehlszeile,
    # Upgrade: in Blöcke teilen, wenn so große Änderungen durchs Sofort-Tor gehen.
    befehl = [programm, *linter.argumente, *dateien]
    erg = ausfuehren.lauf(befehl, repo)
    # ruff/ESLint: 0 sauber, 1 Fehler im Code, sonst Werkzeug kaputt (Konfig) → ausgelassen, voll prüft.
    if erg.code not in (0, 1):
        kurz = (erg.ausgabe.strip().splitlines() or ["?"])[-1]
        return _ausgelassen(
            "sofort",
            linter.adapter,
            f"{linter.name} lief nicht (Exit {erg.code}: {kurz})",
            werkzeug,
        )
    ergebnis = leiter.GRUEN if erg.code == 0 else leiter.ROT
    return leiter.Stufe(
        "sofort",
        linter.adapter,
        True,
        f"{len(dateien)} geänderte Datei(en)",
        round(erg.dauer_s, 2),
        " ".join(befehl),
        werkzeug,
        ergebnis,
        erg.ausgabe if ergebnis != leiter.GRUEN else "",
    )


def stufen(repo: Path, basis: str) -> tuple[list[leiter.Stufe], str]:
    """Stufen „sofort“ + „doku“ für den Arbeitsbaum gegen ``basis``; zweiter Wert = Halt-Grund."""
    pfade = geaendert(repo, basis)
    if pfade is None:
        grund = "geänderte Dateien unbekannt (git diff gescheitert)"
        return [_ausgelassen("sofort", "", grund), _ausgelassen("doku", "", grund)], ""
    if not pfade:
        # Nichts geändert: für Stufe 1/2 nichts zu tun, die Leiter prüft voll (nie Grün ohne Lauf).
        return [], ""
    liste: list[leiter.Stufe] = []
    for linter in _LINTER:
        dateien = [
            pfad for pfad in pfade if PurePosixPath(pfad).suffix.lower() in linter.endungen and (repo / pfad).is_file()
        ]
        if dateien:
            liste.append(_lint(repo, linter, dateien))
    if not liste:
        liste.append(_ausgelassen("sofort", "", "keine Code-Datei geändert"))
    if any(stufe.ergebnis == leiter.ROT for stufe in liste):
        return [*liste, _ausgelassen("doku", "", ROT_HALT)], ROT_HALT
    doku_start = time.monotonic()
    grund = _nicht_nur_doku(repo, basis, pfade)
    nur_doku = not grund
    liste.append(
        leiter.Stufe(
            "doku",
            "",
            True,
            grund or NUR_DOKU,
            round(time.monotonic() - doku_start, 2),
            "",
            "",
            leiter.GRUEN,
            "",
        )
    )
    return liste, NUR_DOKU if nur_doku else ""
